import fedora_messaging.api as messaging
import git
import koji
import logging
import os
import pyrpkg
import regex
import tempfile
import yaml

# Global configuration config
c = dict()

# Retry attempts if things fail
retry = 5

# sources file regular expression
sre = regex.compile(r'^(?>(?P<hash>[a-f0-9]{32})  (?P<file>.+)|SHA512 \((?P<file>.+)\) = (?<hash>[a-f0-9]{128}))$')

# Generic API
def get_config():
    return c

# TODO: This needs even more error checking, e.g.
# - check if blocks are actual dictionaries
# - check if certain values are what we expect
def load_config(crepo):
    global c
    cdir = tempfile.TemporaryDirectory(prefix='distrobaker-')
    logging.info('Fetching configuration from {} to {}'.format(crepo, cdir.name))
    scm = split_scmurl(crepo)
    for attempt in range(retry):
        try:
            git.Repo.clone_from(scm['url'], cdir.name).git.checkout(scm['ref'])
        except:
            logging.warning('Failed to fetch configuration, retrying (#{}).'.format(attempt + 1))
            continue
        else:
            logging.info('Configuration fetched successfully.')
            break
    else:
        logging.error('Failed to fetch configuration, giving up.')
        return None
    # Try to load yaml
    if os.path.isfile(os.path.join(cdir.name, 'distrobaker.yaml')):
        try:
            with open(os.path.join(cdir.name, 'distrobaker.yaml')) as f:
                y = yaml.safe_load(f)
            logging.debug('{} loaded, processing.'.format(os.path.join(cdir.name, 'distrobaker.yaml')))
        except:
            logging.error('Could not parse distrobaker.yaml.')
            return None
    else:
        logging.error('Configuration repository does not contain distrobaker.yaml.')
        return None
    n = dict()
    if 'configuration' in y:
        cnf = y['configuration']
        if 'source' in cnf:
            n['source'] = dict()
            if 'scm' in cnf['source']:
                n['source']['scm'] = str(cnf['source']['scm'])
            else:
                logging.error('Configuration error: source.scm missing.')
                return None
            if 'cache' in cnf['source']:
                n['source']['cache'] = dict()
                for k in ('url', 'cgi', 'path'):
                    if k in cnf['source']['cache']:
                        n['source']['cache'][k] = str(cnf['source']['cache'][k])
                    else:
                        logging.error('Configuration error: source.cache.{} missing.'.format(k))
                        return None
            else:
                logging.error('Configuration error: source.cache missing.')
                return None
        else:
            logging.error('Configuration error: source missing.')
            return None
        if 'destination' in cnf:
            n['destination'] = dict()
            if 'scm' in cnf['destination']:
                n['destination']['scm'] = str(cnf['destination']['scm'])
            else:
                logging.error('Configuration error: destination.scm missing.')
                return None
            if 'cache' in cnf['destination']:
                n['destination']['cache'] = dict()
                for k in ('url', 'cgi', 'path'):
                    if k in cnf['destination']['cache']:
                        n['destination']['cache'][k] = str(cnf['destination']['cache'][k])
                    else:
                        logging.error('Configuration error: destination.cache.{} missing.'.format(k))
                        return None
            else:
                logging.error('Configuration error: destination.cache missing.')
                return None
        else:
            logging.error('Configuration error: destination missing.')
            return None
        if 'trigger' in cnf:
            n['trigger'] = dict()
            for k in ('rpms', 'modules'):
                if k in cnf['trigger']:
                    n['trigger'][k] = str(cnf['trigger'][k])
                else:
                    # Triggers aren't strictly required for oneshot; or if the relevant components are not configured.
                    logging.warning('Configuration warning: no trigger configured for {}.'.format(k))
        else:
            logging.error('Configuration error: trigger missing.')
            return None
        if 'build' in cnf:
            n['build'] = dict()
            for k in ('profile', 'prefix', 'target', 'mbs'):
                if k in cnf['build']:
                    n['build'][k] = str(cnf['build'][k])
                else:
                    logging.error('Configuration error: build.{} missing.'.format(k))
                    return None
            if 'scratch' in cnf['build']:
                n['build']['scratch'] = bool(cnf['build']['scratch'])
            else:
                logging.warning('Configuration warning: build.scratch not defined, assuming false.')
                n['build']['scratch'] = False
        else:
            logging.error('Configuration error: build missing.')
            return None
        if 'git' in cnf:
            n['git'] = dict()
            for k in ('author', 'email', 'message'):
                if k in cnf['git']:
                    n['git'][k] = str(cnf['git'][k])
                else:
                    logging.error('Configuration error: git.{} missing.'.format(k))
                    return None
        else:
            logging.error('Configuration error: git missing.')
            return None
        if 'control' in cnf:
            n['control'] = dict()
            for k in ('build', 'merge'):
                if k in cnf['control']:
                    n['control'][k] = bool(cnf['control'])
                else:
                    logging.error('Configuration error: control.{} missing.'.format(k))
                    return None
        else:
            logging.error('Configuration error: control missing.')
            return None
    else:
        logging.error('The requires configuration block is missing.')
        return None
    components = 0
    if 'components' in y:
        n['comps'] = dict()
        cnf = y['components']
        for k in ('rpms', 'modules'):
            if k in cnf:
                n['comps'][k] = dict()
                for p in cnf[k].keys():
                    components += 1
                    n['comps'][k][p] = dict()
                    for ck in ('source', 'destination'):
                        if ck in cnf[k][p]:
                            n['comps'][k][p][ck] = str(cnf[k][p][ck])
                        else:
                            logging.error('Configuration error: components.{}.{}.{} missing.'.format(k, p, ck))
                            return None
            else:
                logging.warning('Configuration warning: no {} configured.'.format(k))
    if not components:
        logging.warning('No components configured.  Nothing to do.')
    c['main'] = n
    return c

# TODO: Checkout specific ref from the configured branch if requested
# TODO: The main config should still hold branch names but messages can request specific refs from those branches
# TODO: For modules & merge, rewrite modulemd and merge components recurseively
def sync_repo(comp, ns='rpms'):
    logging.info('Synchronizing SCM for {}/{}.'.format(ns, comp))
    tempdir = tempfile.TemporaryDirectory(prefix='repo-{}-{}-'.format(ns, comp))
    logging.debug('Temporary directory created: {}'.format(tempdir.name))
    logging.debug('Cloning {}/{} from {}'.format(ns, comp, c['main']['destination']['scm'] + c['comps'][ns][comp]['destination']))
    sscm = split_scmurl(c['main']['source']['scm'] + c['comps'][ns][comp]['source'])
    dscm = split_scmurl(c['main']['destination']['scm'] + c['comps'][ns][comp]['destination'])
    for attempt in range(retry):
        try:
            repo = git.Repo.clone_from(dscm['url'], tempdir.name, branch=dscm['ref'])
        except:
            logging.warning('Cloning attempt #{}/{} failed, retrying.'.format(attempt + 1, retry))
            continue
        else:
            break
    else:
        logging.error('Exhausted cloning attempts for {}/{}, skipping.'.format(ns, comp))
        return None
    logging.debug('Successfully cloned {}/{}.'.format(ns, comp))
    logging.debug('Fetching upstream repository for {}/{}.'.format(ns, comp))
    repo.git.remote('add', 'source', sscm['url'])
    for attempt in range(retry):
        try:
            repo.git.fetch('source', sscm['ref'])
        except:
            logging.warning('Fetching upstream attempt #{}/{} failed, retrying.'.format(attempt + 1, retry))
            continue
        else:
            break
    else:
        logging.error('Exhausted upstream fetching attempts for {}/{}, skipping.'.format(ns, comp))
        return None
    logging.debug('Successfully fetched upstream repository for {}/{}.'.format(ns, comp))
    if c['main']['control']['merge']:
        logging.debug('Attempting to synchronize the {}/{} branches using the merge mechanism.'.format(ns, comp))
        # TODO: Generate a random branch name for the temporary branch in switch
        try:
            actor = git.Actor(c['main']['git']['user'], c['main']['git']['email'])
            repo.git.checkout('source/{}'.format(sscm['ref']))
            repo.git.switch('-c', 'source')
            repo.git.merge('--allow-unrelated-histories', '--no-commit', '-s', 'ours', dscm['ref'])
            repo.index.commit('Temporary working tree merge', author=actor, committer=actor)
            repo.git.checkout(dscm['ref'])
            repo.git.merge('--no-commit', '--squash', 'source')
            repo.index.commit(c['main']['git']['message'] + c['main']['source']['scm'] + c['comps'][ns][comp]['source'], author=actor, committer=actor)
        except:
            logging.error('Failed to merge {}/{}, skipping.'.format(ns, comp))
            return None
        logging.debug('Successfully merged {}/{} with upstream.'.format(ns, comp))
    else:
        logging.debug('Attempting to synchronize the {}/{} branches using the clean pull mechanism.'.format(ns, comp))
        try:
            repo.git.pull('source', sscm['ref'])
        except:
            logging.error('Failed to perform a clean pull for {}/{}, skipping.'.format(ns, comp))
            return None
        logging.debug('Successfully pulled {}/{} from upstream.'.format(ns, comp))
    logging.debug('Component {}/{} successfully synchronized.'.format(ns, comp))
    if os.path.isfile(os.path.join(tempdir.name, 'sources')):
        logging.debug('Lookaside cache sources for {}/{} found, synchronizing.'.format(ns, comp))
        if sync_cache(comp, os.path.join(tempdir.name, 'sources'), ns=ns) is not None:
            logging.debug('Lookaside cache sources for {}/{} synchronized.'.format(ns, comp))
        else:
            logging.error('Failed to synchronize lookaside cache sources for {}/{}, skipping.'.format(ns, comp))
            return None
    logging.debug('Pushing synchronized contents for {}/{}.'.format(ns, comp))
    for attempt in range(retry):
        try:
            # TODO: Uncomment after testing
            #repo.git.push('--set-upstream', 'origin', dst['ref'])
            pass
        except:
            logging.warning('Pushing attempt #{}/{} failed, retrying.'.format(attempt + 1, retry))
            continue
        else:
            break
    else:
        logging.error('Exhausted pushing attempts for {}/{}, skipping.'.format(ns, comp))
        return None
    logging.info('Successfully synchronized {}/{}.'.format(ns, comp))
    return repo.git.rev_parse('HEAD')

# TODO: Handle multiple hashes for the same filename.
#       Perhaps via a list of tuples and a directory structure similar to download_path in tempdir
def sync_cache(comp, sources, ns='rpms'):
    sums = dict()
    logging.debug('Processing lookaside cache sources for {}/{}.'.format(ns, comp))
    try:
        with open(sources) as f:
            for l in f.readlines():
                rec = sre.match(l.rstrip())
                if rec is None:
                    logging.error('Garbage found in {}/{}:sources, skipping.'.format(ns, comp))
                    return None
                rec = rec.groupdict()
                logging.debug('Found lookaside cache sources for {}/{}: {} ({}).'.format(ns, comp, rec['file'], rec['hash']))
                sums[rec['file']] = rec['hash']
    except:
        logging.error('Failed processing lookaside cache sources for {}/{}.'.format(ns, comp))
        return None
    scache = pyrpkg.lookaside.CGILookasideCache('sha512', c['main']['source']['cache']['url'], c['main']['source']['cache']['cgi'])
    scache.download_path = c['main']['source']['cache']['path']
    dcache = pyrpkg.lookaside.CGILookasideCache('sha512', c['main']['destination']['cache']['url'], c['main']['destination']['cache']['cgi'])
    dcache.download_path = c['main']['destination']['cache']['path']
    tempdir = tempfile.TemporaryDirectory('cache-{}-{}-'.format(ns, comp))
    logging.debug('Temporary directory created: {}'.format(tempdir.name))
    for f in sums:
        hashtype = 'sha512' if len(sums[f]) else 'md5'
        # There's no API for this and .upload doesn't let us override it
        dcache.hashtype = hashtype
        for attempt in range(retry):
            try:
                if not dcache.remote_file_exists('{}/{}'.format(ns, comp), f, sums[f]):
                    logging.debug('File {} for {}/{} not available in the destination cache, downloading.'.format(f, ns, comp))
                    scache.download('{}/{}'.format(ns, comp), f, sums[f], os.path.join(tempdir.name, f), hashtype=hashtype)
                    logging.debug('File {} for {}/{} successfully downloaded.  Uploading to the destination cache.'.format(f, ns, comp))
                    dcache.upload('{}/{}'.format(ns, comp), os.path.join(tempdir.name, f), sums[f])
                    logging.debug('File {} for {}/{} successfully uploaded to the destination cache.'.format(f, ns, comp))
                else:
                    logging.debug('File {} for {}/{} already uploaded, skipping.'.format(f, ns, comp))
            except:
                logging.warning('Failed attempt #{}/{} handling {} for {}/{}, retrying.'.format(attempt + 1, retry, f, ns, comp))
            else:
                break
        else:
            logging.error('Exhausted lookaside cache synchronization attempts for {}/{} while working on {}, skipping.'.format(ns, comp, f))
            return None
    return sums

# TODO: Implement modules
def build_comp(comp, ref, ns='rpms'):
    logging.info('Processing build for {}/{}.'.format(ns, comp))
    if ns == 'rpms':
        try:
            buildconf = koji.read_config(profile_name=c['main']['build']['profile'])
        except:
            logging.error('Failed initializing koji with the {} profile while building {}/{}, skipping.'.format(c['main']['build']['profile'], ns, comp))
            return None
        buildsys = koji.ClientSession(buildconf['server'], opts=buildconf)
        try:
            buildsys.gssapi_login()
        except:
            logging.error('Failed authenticating against koji while building {}/{}, skipping.'.format(ns, comp))
            return None
        try:
            task = buildsys.build('{}{}#{}'.format(c['main']['build']['prefix'], comp, ref), c['main']['build']['target'], { 'scratch': c['main']['build']['scratch'] })
            logging.info('Build submitted for {}/{}; task {}; SCMURL: {}{}#{}.'.format(ns, comp, task, c['main']['build']['prefix'], comp, ref))
            return task
        except:
            pass
    elif ns == 'modules':
        logging.critical('Cannot build {}/{}; module building not implemented.'.format(ns, comp))
        return None
    else:
        logging.critical('Cannot build {}/{}; unknown namespace.'.format(ns, comp))
        return None

# TODO: Implement this
# TODO: Trigger syncs and builds
def process_message(comp, msg):
    pass

# TODO: Implement this
# TODO: Get SCMURL for the given build
# TODO: Might need to check for modules and ask MBS if needed
def get_buildinfo(comp, build):
    pass

# Utility functions
def split_scmurl(scmurl):
    scm = scmurl.split('#', 1)
    return {
        'url': scm[0],
        'ref': scm[1] if len(scm) >= 2 else 'master'
    }
