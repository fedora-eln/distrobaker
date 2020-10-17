#!/usr/bin/python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
# Petr Šabata, Red Hat Inc., 2020
#
# DistroBaker
# Sync tool for distibution git sync and build automation.
#
# This service implements distibution git sync for keeping
# downstream distibutions up-to-date automatically.  It implements
# the following mechanisms:
#
#  * Fetches its own configuration from a git repository, the
#    link to which is defined in the environment.
#
#  * Monitors message buses for updates to relevant components.
#
#  * Automatically syncs distribution git repositories from and
#    to configured branches on component updates.
#
#  * Automatically submits package builds from the synced branch
#    in the configured build system.
#

import fedora_messaging
from fedora_messaging import api as msgapi
import git
import io
import koji
import logging
import os
import pyrpkg
import regex
import shutil
import tempfile
import yaml


# Global configuration dict
# Holds the generic configuration as well as the components.
conf = {
    'configuration': os.environ.get('DISTROBAKERCONF'),
    'gituser': os.environ.get('DISTROBAKERUSER'),
    'gitemail': os.environ.get('DISTROBAKEREMAIL'),
}

# Status for this instance
# Holds tasks executed by this instance and their status.
status = {
    'status': 'starting',
    'tasks': []
}

sourcere = regex.compile(r'^(?>(?P<hash>[a-f0-9]{32})  (?P<filename>.+)|SHA512 \((?P<filename>.+)\) = (?<hash>[a-f0-9]{128}))$')

def split_branch(s):
    """Utility function.  Splits the provided string into
    URL and branch name.
    """
    splitdata = s.split('#', 1)
    return {
        'url': splitdata[0],
        'branch': splitdata[1] if len(splitdata) == 2 else 'master',
    }

def load_config():
    """Fetches configuration from a repository defined by the
    DISTROBAKERCONF environment variables, parses it and populates the
    global config dictionary.
    """
    global conf
    global status
    logging.info('Loading configuration from ' + conf['configuration'])
    try:
        shutil.rmtree('conf')
    except FileNotFoundError:
        pass
    repolink = split_branch(conf['configuration'])
    try:
        git.Repo.clone_from(repolink['url'], 'conf', depth=1, branch=repolink['branch'])
        with io.open('conf/distrobaker.yaml', 'r') as stream:
            yamlconf = yaml.safe_load(stream)
    except:
        status['status'] = 'error - no configuration'
        return
    try:
        for prop in ('source', 'destination', 'trigger', 'target', 'profile', 'buildprefix', 'build', 'merge', 'sourcecache', 'sourcecachecgi', 'sourcecachepath', 'destinationcache', 'destinationcachecgi', 'destinationcachepath'):
            conf[prop] = yamlconf['configuration'][prop]
        conf['components'] = {}
        for component in yamlconf['components'].keys():
            compdata = {
                'source':
                    conf['source'] + yamlconf['components'][component]['source'],
                'destination':
                    conf['destination'] + yamlconf['components'][component]['destination'],
            }
            for prop in ('trigger', 'target', 'profile', 'build', 'merge'):
                if prop in yamlconf['components'][component].keys():
                    compdata[prop] = yamlconf['components'][component][prop]
                else:
                    compdata[prop] = conf[prop]
            conf['components'][component] = compdata
    except KeyError:
        status['status'] = 'error - configuration error'
        return
    status['status'] = 'ok'

def merge_component(component):
    """Merges the component's source distribution repository into
    the destination repository.  Attempt to perform a fast-forward
    merge but creates a merge commit if not possible.
    """
    global conf
    global status
    repodir = tempfile.mkdtemp()
    srclink = split_branch(conf['components'][component]['source'])
    dstlink = split_branch(conf['components'][component]['destination'])
    sourcefiles = dict()
    try:
        logging.debug('Fetching repositories...')
        repo = git.Repo.clone_from(dstlink['url'], repodir, branch=dstlink['branch'])
        srcremote = repo.create_remote('source', srclink['url'])
        srcremote.fetch()
        try:
            logging.info('Attempting a fast forward merge...')
            repo.remotes.source.pull(srclink['branch'])
            logging.info('Successfully pulled.')
        except git.exc.GitCommandError:
            if not conf['components'][component]['merge']:
                logging.warning('Failed to do a fast forward merge but configured not to do a merge commit, skpping.')
            else:
                # TODO: Rewrite this to use the API
                logging.info('Failed to do a fast forward merge.  Merging with a commit.')
                actor = git.Actor(conf['gituser'], conf['gitemail'])
                repo.git.reset('--hard')
                repo.git.merge('--no-commit', '-s', 'recursive', '-X', 'theirs', 'source/' + srclink['branch'])
                commitmessage = """Automatic DistroBaker synchronization

                To opt out from automatic synchronization from monitored upstream,
                consult the distribution documentation or reach out to the EXD/RCM team.
                """
                commitmessage += '\nTriggered by ' + component + ' appearing in ' + conf['components'][component]['trigger']
                commitmessage += '\nSource: ' + conf['components'][component]['source']
                repo.index.commit(commitmessage, author=actor, committer=actor)
        try:
            with open(os.path.join(repodir, 'sources')) as fh:
                for rec in fh.readlines():
                    match = sourcere.match(rec)
                    if match is None:
                        logging.warning('Sources record not parsed:' + rec)
                        continue
                    group = match.groupdict()
                    sourcefiles[group['filename']] = group['hash']
        except:
            logging.warning('Could not process sources for this component!')
        copy_source_files(component, sourcefiles)
        try:
            logging.info('Pushing changes...')
            repo.git.push('--set-upstream', 'origin', dstlink['branch'])
            logging.info('Successfully pushed.')
            conf['components'][component]['ref'] = str(repo.commit())
        except git.exc.GitCommandError as e:
            logging.warning('Pushing failed!')
            logging.warning(e)
            pass
    except:
        logging.warning('Failed to fetch repositories!')
        return
    try:
        shutil.rmtree(repodir)
    except:
        pass

def copy_source_files(component, sourcefiles):
    """Copies source files from the source to the destination cache.
    """
    scache = pyrpkg.lookaside.CGILookasideCache(
        'sha512',
        conf['sourcecache'],
        conf['sourcecachecgi'])
    scache.download_path = conf['sourcecachepath']
    dcache_md5 = pyrpkg.lookaside.CGILookasideCache(
        'md5',
        conf['destinationcache'],
        conf['destinationcachecgi'])
    dcache_md5.download_path = conf['destinationcachepath']
    dcache_sha512 = pyrpkg.lookaside.CGILookasideCache(
        'sha512',
        conf['destinationcache'],
        conf['destinationcachecgi'])
    dcache_sha512.download_path = conf['destinationcachepath']
    tempdir = tempfile.mkdtemp()
    for rec in sourcefiles:
        if (len(sourcefiles[rec]) == 128 and dcache_sha512.remote_file_exists('rpms/' + component, rec, sourcefiles[rec])) or (len(sourcefiles[rec]) == 32 and dcache_md5.remote_file_exists('rpms/' + component, rec, sourcefiles[rec])):
            logging.info('Source file already uploaded, skpping (' + rec + ')')
            continue
        hashtype = 'sha512' if len(sourcefiles[rec]) == 128 else 'md5'
        scache.download('rpms/' + component, rec, sourcefiles[rec], os.path.join(tempdir, rec), hashtype=hashtype)
        if hashtype == 'sha512':
            logging.info('Uploading ' + rec + ' with SHA512 checksum.')
            dcache_sha512.upload('rpms/' + component, os.path.join(tempdir, rec), sourcefiles[rec])
        else:
            logging.info('Uploading ' + rec + ' with MD5 checksum.')
            dcache_md5.upload('rpms/' + component, os.path.join(tempdir, rec), sourcefiles[rec])
    try:
        shutil.rmtree(tempdir)
    except:
        pass

def build_component(component, scratch=False):
    """Submits a build of the component using the configured build
    system and its build target.
    """
    global conf
    global status
    if conf['components'][component]['build']:
        if 'ref' not in conf['components'][component]:
            logging.warning(component + ' has no known ref to build, skipping.')
            return
        try:
            kojiconf = koji.read_config(profile_name=conf['components'][component]['profile'])
            session = koji.ClientSession(kojiconf['server'], opts=kojiconf)
            session.gssapi_login()
            session.build(conf['buildprefix'] + component + '#' + conf['components'][component]['ref'], conf['components'][component]['target'], {'scratch': scratch})
        except:
            logging.warning('Build submission for component ' + component + ' failed!')
    else:
        logging.info(component + ' not configured for build, skipping.')

def handle_message(message):
    """Handles message bus messages and triggers SCM syncs and
    builds, if configured to do so.
    """
    global conf
    global status
    logging.debug('Message received.  Topic: ' + message.topic)
    if message.topic.endswith('buildsys.tag'):
        component = message.body['name']
        tag = message.body['tag']
        logging.debug('Tagging message received for ' + component + ' (tag: ' + tag + ')')
        if component in conf['components'].keys() and tag == conf['components'][component]['trigger']:
            logging.info('Synchronizing ' + component + '...')
            merge_component(component)
            if conf['components'][component]['build']:
                logging.info('Building ' + component + '...')
                build_component(component)
            else:
                logging.info('Skipping build for ' + component + '...')
        else:
            logging.debug('Not configured to sync this component.  Skipping.')

def main():
    global conf
    global status
    logging.basicConfig(format='%(asctime)s:%(levelname)s:%(message)s', level=logging.INFO)
    logging.info('DistroBaker starting.')
    if not conf['configuration'] or not conf['gituser'] or  not conf['gitemail']:
        logging.critical('DISTROBAKER* variables not defined, exiting.')
        exit()
    logging.info('Loading configuration.')
    load_config()
    logging.info('Configuration loaded.')
    logging.info('Listening for messages.')
    msgapi.consume(handle_message)

if __name__ == "__main__":
    main()
