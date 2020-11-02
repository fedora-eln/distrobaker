import fedora_messaging.api as messaging
import git
import koji
import logging
import os
import pyrpkg
import regex
import tempfile
import yaml

# Global logger
logger = logging.getLogger(__name__)

# Global configuration config
c = dict()

# Retry attempts if things fail
retry = 3

# Running in the dry run mode
dry_run = False

# sources file regular expression
sre = regex.compile(r'^(?>(?P<hash>[a-f0-9]{32})  (?P<file>.+)|SHA512 \((?P<file>.+)\) = (?<hash>[a-f0-9]{128}))$')

def loglevel(val=None):
    if val is not None:
        try:
            logger.setLevel(val)
        except:
            logger.warning('Invalid log level passed to DistroBaker logger: {}'.format(val))
    return logger.getEffectiveLevel()

def retries(val=None):
    global retry
    if val is not None:
        retry = val
    return retry

def pretend(val=None):
    global dry_run
    if val is not None:
        dry_run = val
    return dry_run

def get_config():
    return c

# TODO: This needs even more error checking, e.g.
# - check if blocks are actual dictionaries
# - check if certain values are what we expect
def load_config(crepo):
    global c
    cdir = tempfile.TemporaryDirectory(prefix='distrobaker-')
    logger.info('Fetching configuration from {} to {}'.format(crepo, cdir.name))
    scm = split_scmurl(crepo)
    for attempt in range(retry):
        try:
            git.Repo.clone_from(scm['url'], cdir.name).git.checkout(scm['ref'])
        except Exception as e:
            logger.warning('Failed to fetch configuration, retrying (#{}).'.format(attempt + 1))
            logger.error('EXCEPTION: ' + str(e))
            continue
        else:
            logger.info('Configuration fetched successfully.')
            break
    else:
        logger.error('Failed to fetch configuration, giving up.')
        return None
    # Try to load yaml
    if os.path.isfile(os.path.join(cdir.name, 'distrobaker.yaml')):
        try:
            with open(os.path.join(cdir.name, 'distrobaker.yaml')) as f:
                y = yaml.safe_load(f)
            logger.debug('{} loaded, processing.'.format(os.path.join(cdir.name, 'distrobaker.yaml')))
        except Exception as e:
            logger.error('Could not parse distrobaker.yaml.')
            logger.error('EXCEPTION: ' + str(e))
            return None
    else:
        logger.error('Configuration repository does not contain distrobaker.yaml.')
        return None
    n = dict()
    if 'configuration' in y:
        cnf = y['configuration']
        if 'source' in cnf:
            n['source'] = dict()
            if 'scm' in cnf['source']:
                n['source']['scm'] = str(cnf['source']['scm'])
            else:
                logger.error('Configuration error: source.scm missing.')
                return None
            if 'cache' in cnf['source']:
                n['source']['cache'] = dict()
                for k in ('url', 'cgi', 'path'):
                    if k in cnf['source']['cache']:
                        n['source']['cache'][k] = str(cnf['source']['cache'][k])
                    else:
                        logger.error('Configuration error: source.cache.{} missing.'.format(k))
                        return None
            else:
                logger.error('Configuration error: source.cache missing.')
                return None
        else:
            logger.error('Configuration error: source missing.')
            return None
        if 'destination' in cnf:
            n['destination'] = dict()
            if 'scm' in cnf['destination']:
                n['destination']['scm'] = str(cnf['destination']['scm'])
            else:
                logger.error('Configuration error: destination.scm missing.')
                return None
            if 'cache' in cnf['destination']:
                n['destination']['cache'] = dict()
                for k in ('url', 'cgi', 'path'):
                    if k in cnf['destination']['cache']:
                        n['destination']['cache'][k] = str(cnf['destination']['cache'][k])
                    else:
                        logger.error('Configuration error: destination.cache.{} missing.'.format(k))
                        return None
            else:
                logger.error('Configuration error: destination.cache missing.')
                return None
        else:
            logger.error('Configuration error: destination missing.')
            return None
        if 'trigger' in cnf:
            n['trigger'] = dict()
            for k in ('rpms', 'modules'):
                if k in cnf['trigger']:
                    n['trigger'][k] = str(cnf['trigger'][k])
                else:
                    # Triggers aren't strictly required for oneshot; or if the relevant components are not configured.
                    logger.warning('Configuration warning: no trigger configured for {}.'.format(k))
        else:
            logger.error('Configuration error: trigger missing.')
            return None
        if 'build' in cnf:
            n['build'] = dict()
            for k in ('profile', 'prefix', 'target', 'mbs'):
                if k in cnf['build']:
                    n['build'][k] = str(cnf['build'][k])
                else:
                    logger.error('Configuration error: build.{} missing.'.format(k))
                    return None
            if 'scratch' in cnf['build']:
                n['build']['scratch'] = bool(cnf['build']['scratch'])
            else:
                logger.warning('Configuration warning: build.scratch not defined, assuming false.')
                n['build']['scratch'] = False
        else:
            logger.error('Configuration error: build missing.')
            return None
        if 'git' in cnf:
            n['git'] = dict()
            for k in ('author', 'email', 'message'):
                if k in cnf['git']:
                    n['git'][k] = str(cnf['git'][k])
                else:
                    logger.error('Configuration error: git.{} missing.'.format(k))
                    return None
        else:
            logger.error('Configuration error: git missing.')
            return None
        if 'control' in cnf:
            n['control'] = dict()
            for k in ('build', 'merge'):
                if k in cnf['control']:
                    n['control'][k] = bool(cnf['control'][k])
                else:
                    logger.error('Configuration error: control.{} missing.'.format(k))
                    return None
        else:
            logger.error('Configuration error: control missing.')
            return None
    else:
        logger.error('The requires configuration block is missing.')
        return None
    components = 0
    if 'components' in y:
        nc = dict()
        cnf = y['components']
        for k in ('rpms', 'modules'):
            if k in cnf:
                nc[k] = dict()
                for p in cnf[k].keys():
                    components += 1
                    nc[k][p] = dict()
                    for ck in ('source', 'destination'):
                        if ck in cnf[k][p]:
                            nc[k][p][ck] = str(cnf[k][p][ck])
                        else:
                            logger.error('Configuration error: components.{}.{}.{} missing.'.format(k, p, ck))
                            return None
                logger.info('Found {} configured component(s) in the {} namespace.'.format(len(nc[k]), k))
            else:
                logger.info('No components configured in the {} namespace.'.format(k))
    if not components:
        logger.warning('No components configured.  Nothing to do.')
    c['main'] = n
    c['comps'] = nc
    return c

# TODO: Checkout specific ref from the configured branch if requested
# TODO: The main config should still hold branch names but messages can request specific refs from those branches
# TODO: For modules & merge, rewrite modulemd and merge components recurseively
def sync_repo(comp, ns='rpms'):
    logger.info('Synchronizing SCM for {}/{}.'.format(ns, comp))
    tempdir = tempfile.TemporaryDirectory(prefix='repo-{}-{}-'.format(ns, comp))
    logger.debug('Temporary directory created: {}'.format(tempdir.name))
    logger.debug('Cloning {}/{} from {}/{}/{}'.format(ns, comp, c['main']['destination']['scm'], ns, c['comps'][ns][comp]['destination']))
    sscm = split_scmurl('{}/{}/{}'.format(c['main']['source']['scm'], ns, c['comps'][ns][comp]['source']))
    dscm = split_scmurl('{}/{}/{}'.format(c['main']['destination']['scm'], ns, c['comps'][ns][comp]['destination']))
    for attempt in range(retry):
        try:
            repo = git.Repo.clone_from(dscm['url'], tempdir.name, branch=dscm['ref'])
        except Exception as e:
            logger.warning('Cloning attempt #{}/{} failed, retrying.'.format(attempt + 1, retry))
            logger.error('EXCEPTION: ' + str(e))
            continue
        else:
            break
    else:
        logger.error('Exhausted cloning attempts for {}/{}, skipping.'.format(ns, comp))
        return None
    logger.debug('Successfully cloned {}/{}.'.format(ns, comp))
    logger.debug('Fetching upstream repository for {}/{}.'.format(ns, comp))
    repo.git.remote('add', 'source', sscm['url'])
    for attempt in range(retry):
        try:
            repo.git.fetch('source', sscm['ref'])
        except Exception as e:
            logger.warning('Fetching upstream attempt #{}/{} failed, retrying.'.format(attempt + 1, retry))
            logger.error('EXCEPTION: ' + str(e))
            continue
        else:
            break
    else:
        logger.error('Exhausted upstream fetching attempts for {}/{}, skipping.'.format(ns, comp))
        return None
    logger.debug('Successfully fetched upstream repository for {}/{}.'.format(ns, comp))
    logger.debug('Configuring repository properties for {}/{}.'.format(ns, comp))
    try:
        repo.git.config('user.name', c['main']['git']['author'])
        repo.git.config('user.email', c['main']['git']['email'])
    except Exception as e:
        logger.error('Failed configuring the git repository while processing {}/{}, skipping.'.format(ns, comp))
        logger.error('EXCEPTION: ' + str(e))
        return None
    if c['main']['control']['merge']:
        logger.debug('Attempting to synchronize the {}/{} branches using the merge mechanism.'.format(ns, comp))
        # TODO: Generate a random branch name for the temporary branch in switch
        try:
            actor = '{} <{}>'.format(c['main']['git']['author'], c['main']['git']['email'])
            repo.git.checkout('source/{}'.format(sscm['ref']))
            repo.git.switch('-c', 'source')
            repo.git.merge('--allow-unrelated-histories', '--no-commit', '-s', 'ours', dscm['ref'])
            repo.git.commit('--author', actor, '--allow-empty', '-m', 'Temporary working tree merge')
            repo.git.checkout(dscm['ref'])
            repo.git.merge('--no-commit', '--squash', 'source')
            msg = '{}\nSource: {}#{}'.format(c['main']['git']['message'], sscm['url'], repo.git.rev_parse('source/{}'.format(sscm['ref'])))
            msgfile = tempfile.NamedTemporaryFile(prefix='msg-{}-{}-'.format(ns, comp))
            with open(msgfile.name, 'w') as f:
                f.write(msg)
            repo.git.commit('--author', actor, '--allow-empty', '-F', msgfile.name)
        except Exception as e:
            logger.error('Failed to merge {}/{}, skipping.'.format(ns, comp))
            logger.error('Failed to merge EXCEPTION: ' + str(e))
            return None
        logger.debug('Successfully merged {}/{} with upstream.'.format(ns, comp))
    else:
        logger.debug('Attempting to synchronize the {}/{} branches using the clean pull mechanism.'.format(ns, comp))
        try:
            repo.git.pull('--ff-only', 'source', sscm['ref'])
        except Exception as e:
            logger.error('Failed to perform a clean pull for {}/{}, skipping.'.format(ns, comp))
            logger.error('EXCEPTION: ' + str(e))
            return None
        logger.debug('Successfully pulled {}/{} from upstream.'.format(ns, comp))
    logger.debug('Component {}/{} successfully synchronized.'.format(ns, comp))
    if os.path.isfile(os.path.join(tempdir.name, 'sources')):
        logger.debug('Lookaside cache sources for {}/{} found, synchronizing.'.format(ns, comp))
        if sync_cache(comp, os.path.join(tempdir.name, 'sources'), ns=ns) is not None:
            logger.debug('Lookaside cache sources for {}/{} synchronized.'.format(ns, comp))
        else:
            logger.error('Failed to synchronize lookaside cache sources for {}/{}, skipping.'.format(ns, comp))
            return None
    logger.debug('Pushing synchronized contents for {}/{}.'.format(ns, comp))
    for attempt in range(retry):
        try:
            if not dry_run:
                logger.debug('Pushing {}/{}.'.format(ns, comp))
                repo.git.push('--set-upstream', 'origin', dscm['ref'])
                logger.debug('Successfully pushed {}/{}.'.format(ns, comp))
            else:
                logger.debug('Pushing {}/{} (--dry-run).'.format(ns, comp))
                repo.git.push('--dry-run', '--set-upstream', 'origin', dscm['ref'])
                logger.debug('Successfully pushed {}/{} (--dry-run).'.format(ns, comp))
        except Exception as e:
            logger.warning('Pushing attempt #{}/{} failed, retrying.'.format(attempt + 1, retry))
            logger.error('EXCEPTION: ' + str(e))
            continue
        else:
            break
    else:
        logger.error('Exhausted pushing attempts for {}/{}, skipping.'.format(ns, comp))
        return None
    logger.info('Successfully synchronized {}/{}.'.format(ns, comp))
    return repo.git.rev_parse('HEAD')

# TODO: Handle multiple hashes for the same filename.
#       Perhaps via a list of tuples and a directory structure similar to download_path in tempdir
def sync_cache(comp, sources, ns='rpms'):
    sums = dict()
    logger.debug('Processing lookaside cache sources for {}/{}.'.format(ns, comp))
    try:
        with open(sources) as f:
            for l in f.readlines():
                rec = sre.match(l.rstrip())
                if rec is None:
                    logger.error('Garbage found in {}/{}:sources, skipping.'.format(ns, comp))
                    return None
                rec = rec.groupdict()
                logger.debug('Found lookaside cache sources for {}/{}: {} ({}).'.format(ns, comp, rec['file'], rec['hash']))
                sums[rec['file']] = rec['hash']
    except Exception as e:
        logger.error('Failed processing lookaside cache sources for {}/{}.'.format(ns, comp))
        logger.error('EXCEPTION: ' + str(e))
        return None
    scache = pyrpkg.lookaside.CGILookasideCache('sha512', c['main']['source']['cache']['url'], c['main']['source']['cache']['cgi'])
    scache.download_path = c['main']['source']['cache']['path']
    dcache = pyrpkg.lookaside.CGILookasideCache('sha512', c['main']['destination']['cache']['url'], c['main']['destination']['cache']['cgi'])
    dcache.download_path = c['main']['destination']['cache']['path']
    tempdir = tempfile.TemporaryDirectory(prefix='cache-{}-{}-'.format(ns, comp))
    logger.debug('Temporary directory created: {}'.format(tempdir.name))
    for f in sums:
        hashtype = 'sha512' if len(sums[f]) == 128 else 'md5'
        # There's no API for this and .upload doesn't let us override it
        dcache.hashtype = hashtype
        for attempt in range(retry):
            try:
                if not dcache.remote_file_exists('{}/{}'.format(ns, comp), f, sums[f]):
                    logger.debug('File {} for {}/{} not available in the destination cache, downloading.'.format(f, ns, comp))
                    scache.download('{}/{}'.format(ns, comp), f, sums[f], os.path.join(tempdir.name, f), hashtype=hashtype)
                    logger.debug('File {} for {}/{} successfully downloaded.  Uploading to the destination cache.'.format(f, ns, comp))
                    if not dry_run:
                        dcache.upload('{}/{}'.format(ns, comp), os.path.join(tempdir.name, f), sums[f])
                        logger.debug('File {} for {}/{} successfully uploaded to the destination cache.'.format(f, ns, comp))
                    else:
                        logger.debug('Running in dry run mode, not uploading {} for {}/{}.'.format(f, ns, comp))
                else:
                    logger.debug('File {} for {}/{} already uploaded, skipping.'.format(f, ns, comp))
            except Exception as e:
                logger.warning('Failed attempt #{}/{} handling {} for {}/{}, retrying.'.format(attempt + 1, retry, f, ns, comp))
                logger.error('EXCEPTION: ' + str(e))
            else:
                break
        else:
            logger.error('Exhausted lookaside cache synchronization attempts for {}/{} while working on {}, skipping.'.format(ns, comp, f))
            return None
    return sums

# TODO: Implement modules
def build_comp(comp, ref, ns='rpms'):
    logger.info('Processing build for {}/{}.'.format(ns, comp))
    if ns == 'rpms':
        try:
            buildconf = koji.read_config(profile_name=c['main']['build']['profile'])
        except Exception as e:
            logger.error('Failed initializing koji with the {} profile while building {}/{}, skipping.'.format(c['main']['build']['profile'], ns, comp))
            logger.error('EXCEPTION: ' + str(e))
            return None
        buildsys = koji.ClientSession(buildconf['server'], opts=buildconf)
        try:
            buildsys.gssapi_login()
        except Exception as e:
            logger.error('Failed authenticating against koji while building {}/{}, skipping.'.format(ns, comp))
            logger.error('EXCEPTION: ' + str(e))
            return None
        try:
            if not dry_run:
                task = buildsys.build('{}/{}/{}#{}'.format(c['main']['build']['prefix'], ns, comp, ref), c['main']['build']['target'], { 'scratch': c['main']['build']['scratch'] })
                logger.info('Build submitted for {}/{}; task {}; SCMURL: {}/{}/{}#{}.'.format(ns, comp, task, c['main']['build']['prefix'], ns, comp, ref))
                return task
            else:
                logger.info('Running in the dry mode, not submitting any builds for {}/{} ({}/{}/{}#{}).'.format(ns, comp, c['main']['build']['prefix'], ns, comp, ref))
                return 0
        except Exception as e:
            logger.error('Failed submitting build for {}/{} ({}/{}/{}#{}).'.format(ns, comp, c['main']['build']['prefix'], ns, comp, ref))
            logger.error('EXCEPTION: ' + str(e))
            return None
    elif ns == 'modules':
        logger.critical('Cannot build {}/{}; module building not implemented.'.format(ns, comp))
        return None
    else:
        logger.critical('Cannot build {}/{}; unknown namespace.'.format(ns, comp))
        return None

def process_message(msg):
    # Messaging requires callbacks with a single argument.
    # An ugly workaround for now.
    logger.debug('Received a message with topic {}.'.format(msg.topic))
    if msg.topic.endswith('buildsys.tag'):
        try:
            logger.debug('Processing a tagging event message.')
            comp = msg.body['name']
            tag = msg.body['tag']
            logger.debug('Tagging event for {}, tag {} received.'.format(comp, tag))
        except Exception as e:
            logger.error('Failed to process the message: {}'.format(msg))
            logger.error('EXCEPTION: ' + str(e))
        if tag == c['main']['trigger']['rpms']:
            logger.debug('Message tag configured as an RPM trigger, processing.')
            if comp in c['comps']['rpms']:
                logger.info('Handling an RPM trigger for {}, tag {}.'.format(comp, tag))
                ref = sync_repo(comp, ns='rpms')
                if ref is not None:
                    task = build_comp(comp, ref, ns='rpms')
                    if task is not None:
                        logger.info('Build submission of {}/{} complete, task {}, trigger processed.'.format('rpms', comp, task))
                    else:
                        logger.error('Build submission of {}/{} failed, aborting.trigger.'.format('rpms', comp))
                else:
                    logger.error('Synchronization of {}/{} failed, aborting trigger.'.format('rpms', comp))
            else:
                logger.debug('RPM component {} not configured for sync, ignoring.'.format(comp))
        elif tag == c['main']['trigger']['modules']:
            logger.error('The message matches our module configuration but module building not implemented, ignoring.')
        else:
            logger.debug('Message tag not configured as a trigger, ignoring.')
    else:
        logger.warning('Unable to handle {} topics, ignoring.'.format(msg.topic))

# TODO: Implement this
# TODO: Get SCMURL for the given build
# TODO: Might need to check for modules and ask MBS if needed
def get_buildinfo(build):
    pass

# TODO: Get the latest build from the source
# Returns a build ID, aka NVR
def get_build(comp, ns):
    pass

# TODO: Implement this
# Fetches an build system instance or creates one
# Accepts source or destination
# Requires a config format change
def buildsys(which):
    pass

def split_scmurl(scmurl):
    scm = scmurl.split('#', 1)
    return {
        'url': scm[0],
        'ref': scm[1] if len(scm) >= 2 else 'master'
    }
