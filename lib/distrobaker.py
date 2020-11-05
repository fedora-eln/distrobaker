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

# Matching the namespace/component text format
cre = regex.compile(r'^(?P<namespace>rpms|modules)/(?P<component>[A-Za-z0-9:._+-]+)$')

def loglevel(val=None):
    """Gets or, optionally, sets the logging level of the module.
    Standard numeric levels are accepted.

    :param val: The logging level to use, optional
    :returns: The current logging level
    """
    if val is not None:
        try:
            logger.setLevel(val)
        except:
            logger.warning('Invalid log level passed to DistroBaker logger: {}'.format(val))
    return logger.getEffectiveLevel()

def retries(val=None):
    """Gets or, optionally, sets the number of retries for various
    operational failures.  Typically used for handling dist-git requests.

    :param val: The number of retries to attept, optional
    :returns: The current value of retries
    """
    global retry
    if val is not None:
        retry = val
    return retry

def pretend(val=None):
    """Gets and, optionally, sets the dry_run mode.

    :param val: True to run in dry_run, False otherwise, optional
    :returns: The current value of the dry_run mode
    """
    global dry_run
    if val is not None:
        dry_run = val
    return dry_run

def get_config():
    """Gets the current global configuration dictionary.

    The dictionary may be empty if no configuration has been successfully
    loaded yet.

    :returns: The global configuration dictionary
    """
    return c

def split_scmurl(scmurl):
    """Splits a `link#ref` style URLs into the link and ref parts.  While
    generic, many code paths in DistroBaker expect these to be branch names.
    `link` forms are also accepted, in which case the returned `ref` is None.

    :param scmurl: A link#ref style URL, with #ref being optional
    :returns: A dictionary with `link` and `ref` keys
    """
    scm = scmurl.split('#', 1)
    return {
        'link': scm[0],
        'ref': scm[1] if len(scm) >= 2 else None
    }

# FIXME: This needs even more error checking, e.g.
#         - check if blocks are actual dictionaries
#         - check if certain values are what we expect
def load_config(crepo):
    """Loads or updates the global configuration from the provided URL in
    the `link#branch` format.  If no branch is provided, assumes `master`.

    The operation is atomic and the function can be safely called to update
    the configuration without the danger of clobbering the current one.

    `crepo` must be a git repository with `distrobaker.yaml` in it.

    :param crepo: `link#branch` style URL pointing to the configuration
    :returns: The configuration dictionary, or None on error
    """
    global c
    cdir = tempfile.TemporaryDirectory(prefix='distrobaker-')
    logger.info('Fetching configuration from {} to {}'.format(crepo, cdir.name))
    scm = split_scmurl(crepo)
    if scm['ref'] is None:
        scm['ref'] = 'master'
    for attempt in range(retry):
        try:
            git.Repo.clone_from(scm['link'], cdir.name).git.checkout(scm['ref'])
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
        for k in ('source', 'destination'):
            if k in cnf:
                n[k] = dict()
                if 'scm' in cnf[k]:
                    n[k]['scm'] = str(cnf[k]['scm'])
                else:
                    logger.error('Configuration error: {}.scm missing.'.format(k))
                    return None
                if 'cache' in cnf[k]:
                    n[k]['cache'] = dict()
                    for kc in ('url', 'cgi', 'path'):
                        if kc in cnf[k]['cache']:
                            n[k]['cache'][kc] = str(cnf[k]['cache'][kc])
                        else:
                            logger.error('Configuration error: {}.cache.{} missing.'.format(k, kc))
                            return None
                else:
                    logger.error('Configuration error: {}.cache missing.'.format(k))
                    return None
                if 'profile' in cnf[k]:
                    n[k]['profile'] = str(cnf[k]['profile'])
                else:
                    logger.error('Configuration error: {}.profile missing.'.format(k))
                    return None
                if 'mbs' in cnf[k]:
                    n[k]['mbs'] = str(cnf[k]['mbs'])
                else:
                    logger.error('Configuration error: {}.mbs missing.'.format(k))
                    return None
            else:
                logger.error('Configuration error: {} missing.'.format(k))
                return None
        if 'trigger' in cnf:
            n['trigger'] = dict()
            for k in ('rpms', 'modules'):
                if k in cnf['trigger']:
                    n['trigger'][k] = str(cnf['trigger'][k])
                else:
                    logger.error('Configuration error: trigger.{} missing.'.format(k))
        else:
            logger.error('Configuration error: trigger missing.')
            return None
        if 'build' in cnf:
            n['build'] = dict()
            for k in ('prefix', 'target'):
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
            for k in ('build', 'merge', 'strict'):
                if k in cnf['control']:
                    n['control'][k] = bool(cnf['control'][k])
                else:
                    logger.error('Configuration error: control.{} missing.'.format(k))
                    return None
            n['control']['exclude'] = { 'rpms': set(), 'modules': set() }
            if 'exclude' in cnf['control']:
                for cns in ('rpms', 'modules'):
                    if cns in cnf['control']['exclude']:
                        n['control']['exclude'][cns].update(cnf['control']['exclude'][cns])
            for cns in ('rpms', 'modules'):
                if n['control']['exclude']['rpms']:
                    logger.info('Excluding {} component(s) from the {} namespace.'.format(len(n['control']['exclude'][cns]), cns))
                else:
                    logger.info('Not excluding any components from the {} namespace.'.format(cns))
        else:
            logger.error('Configuration error: control missing.')
            return None
        if 'defaults' in cnf:
            n['defaults'] = dict()
            for dk in ('cache', 'rpms', 'modules'):
                if dk in cnf['defaults']:
                    n['defaults'][dk] = dict()
                    for dkk in ('source', 'destination'):
                        if dkk in cnf['defaults'][dk]:
                            n['defaults'][dk][dkk] = str(cnf['defaults'][dk][dkk])
                        else:
                            logger.error('Configuration error: defaults.{}.{} missing.'.format(dk, dkk))
                else:
                    logger.error('Configuration error: defaults.{} missing.'.format(dk))
                    return None
        else:
            logger.error('Configuration error: defaults missing.')
            return None
    else:
        logger.error('The requires configuration block is missing.')
        return None
    components = 0
    nc = {
        'rpms': dict(),
        'modules': dict(),
        }
    if 'components' in y:
        cnf = y['components']
        for k in ('rpms', 'modules'):
            if k in cnf:
                for p in cnf[k].keys():
                    components += 1
                    nc[k][p] = dict()
                    # FIXME: Modules and their streams -- split the name by colon
                    nc[k][p]['source'] = n['defaults'][k]['source'] % { 'component': p }
                    nc[k][p]['destination'] = n['defaults'][k]['destination'] % { 'component': p }
                    nc[k][p]['cache'] = {
                            'source': n['defaults']['cache']['source'] % { 'component': p },
                            'destination': n['defaults']['cache']['destination'] % { 'component': p },
                        }
                    if cnf[k][p] is None:
                        cnf[k][p] = dict()
                    for ck in ('source', 'destination'):
                        if ck in cnf[k][p]:
                            nc[k][p][ck] = str(cnf[k][p][ck])
                    if 'cache' in cnf[k][p]:
                        for ck in ('source', 'destination'):
                            if ck in cnf[k][p]['cache']:
                                nc[k][p]['cache'][ck] = str(cnf[k][p]['cache'][ck])
            logger.info('Found {} configured component(s) in the {} namespace.'.format(len(nc[k]), k))
    if n['control']['strict']:
        logger.info('Running in the strict mode.  Only configured components will be processed.')
    else:
        logger.info('Running in the non-strict mode.  All trigger components will be processed.')
    if not components:
        if n['control']['strict']:
            logger.warning('No components configured while running in the strict mode.  Nothing to do.')
        else:
            logger.info('No components explicitly configured.')
    c['main'] = n
    c['comps'] = nc
    return c

def sync_repo(comp, ns='rpms', nvr=None):
    """Synchronizes the component SCM repository for the given NVR.
    If no NVR is provided, finds the latest build in the corresponding
    trigger tag.

    Calls sync_cache() if required.  Does not call build_comp().

    :param comp: The component name
    :param ns: The component namespace
    :param nvr: Optional NVR to synchronize
    :returns: The SCM reference of the final synchronized commit, or None on error
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    if comp in c['main']['control']['exclude'][ns]:
        logger.critical('The component {}/{} is excluded from sync, aborting.'.format(ns, comp))
        return None
    logger.info('Synchronizing SCM for {}/{}.'.format(ns, comp))
    nvr = nvr if nvr else get_build(comp, ns=ns)
    if nvr is None:
        logger.error('NVR not specified and no builds for {}/{} could be found, skipping.'.format(ns, comp))
        return None
    else:
        logger.debug('Processing {}/{}: {}'.format(ns, comp, nvr))
    tempdir = tempfile.TemporaryDirectory(prefix='repo-{}-{}-'.format(ns, comp))
    logger.debug('Temporary directory created: {}'.format(tempdir.name))
    bscm = get_scmurl(nvr)
    if bscm is None:
        logger.error('Could not find build SCMURL for {}/{}: {}, skipping.'.format(ns, comp, nvr))
        return None
    else:
        bscm = split_scmurl(bscm)
    if comp in c['comps'][ns]:
        csrc = c['comps'][ns][comp]['source']
        cdst = c['comps'][ns][comp]['destination']
    else:
        csrc = c['main']['defaults'][ns]['source'] % { 'component': comp }
        cdst = c['main']['defaults'][ns]['destination'] % { 'component': comp }
    sscm = split_scmurl('{}/{}/{}'.format(c['main']['source']['scm'], ns, csrc))
    dscm = split_scmurl('{}/{}/{}'.format(c['main']['destination']['scm'], ns, cdst))
    dscm['ref'] = dscm['ref'] if dscm['ref'] else 'master'
    logger.debug('Cloning {}/{} from {}/{}/{}'.format(ns, comp, c['main']['destination']['scm'], ns, cdst))
    for attempt in range(retry):
        try:
            repo = git.Repo.clone_from(dscm['link'], tempdir.name, branch=dscm['ref'])
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
    if sscm['ref']:
        logger.debug('Fetching the {} upstream branch for {}/{}.'.format(sscm['ref'], ns, comp))
    else:
        logger.debug('Fetching all upstream branches for {}/{}.'.format(ns, comp))
    repo.git.remote('add', 'source', sscm['link'])
    for attempt in range(retry):
        try:
            if sscm['ref']:
                repo.git.fetch('source', sscm['ref'])
            else:
                repo.git.fetch('--all')
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
        # FIXME: Generate a random branch name for the temporary branch in switch
        try:
            actor = '{} <{}>'.format(c['main']['git']['author'], c['main']['git']['email'])
            repo.git.checkout(bscm['ref'])
            repo.git.switch('-c', 'source')
            repo.git.merge('--allow-unrelated-histories', '--no-commit', '-s', 'ours', dscm['ref'])
            repo.git.commit('--author', actor, '--allow-empty', '-m', 'Temporary working tree merge')
            repo.git.checkout(dscm['ref'])
            repo.git.merge('--no-commit', '--squash', 'source')
            msg = '{}\nSource: {}#{}'.format(c['main']['git']['message'], sscm['link'], bscm['ref'])
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
            repo.git.pull('--ff-only', bscm['ref'])
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

# FIXME: Handle multiple hashes for the same filename.
#        Perhaps via a list of tuples and a directory structure similar to download_path in tempdir
def sync_cache(comp, sources, ns='rpms'):
    """Synchronizes lookaside cache contents for the given component.
    Processes the sources file list, checks if the file is already present
    in the destination cache; if not, it downloads the file from the
    source cache and uploads it.  Supports SHA512 and MD5 checksums.

    :param comp: The component name
    :param sources: Path to the sources file
    :param ns: The component namespace
    :returns: A dictionary of filenames and checksums, or None on error
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    if comp in c['main']['control']['exclude'][ns]:
        logger.critical('The component {}/{} is excluded from sync, aborting.'.format(ns, comp))
        return None
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
    if comp in c['comps'][ns]:
        scname = c['comps'][ns][comp]['cache']['source']
        dcname = c['comps'][ns][comp]['cache']['destination']
    else:
        scname = c['main']['defaults']['cache']['source'] % { 'component': comp }
        dcname = c['main']['defaults']['cache']['source'] % { 'component': comp }
    for f in sums:
        hashtype = 'sha512' if len(sums[f]) == 128 else 'md5'
        # There's no API for this and .upload doesn't let us override it
        dcache.hashtype = hashtype
        for attempt in range(retry):
            try:
                if not dcache.remote_file_exists('{}/{}'.format(ns, dcname), f, sums[f]):
                    logger.debug('File {} for {}/{} ({}/{}) not available in the destination cache, downloading.'.format(f, ns, comp, ns, dcname))
                    scache.download('{}/{}'.format(ns, scname), f, sums[f], os.path.join(tempdir.name, f), hashtype=hashtype)
                    logger.debug('File {} for {}/{} ({}/{}) successfully downloaded.  Uploading to the destination cache.'.format(f, ns, comp, ns, scname))
                    if not dry_run:
                        dcache.upload('{}/{}'.format(ns, dcname), os.path.join(tempdir.name, f), sums[f])
                        logger.debug('File {} for {}/{} ({}/{}) )successfully uploaded to the destination cache.'.format(f, ns, comp, ns, dcname))
                    else:
                        logger.debug('Running in dry run mode, not uploading {} for {}/{}.'.format(f, ns, comp))
                else:
                    logger.debug('File {} for {}/{} ({}/{}) already uploaded, skipping.'.format(f, ns, comp, ns, dcname))
            except Exception as e:
                logger.warning('Failed attempt #{}/{} handling {} for {}/{} ({}/{} -> {}/{}), retrying.'.format(attempt + 1, retry, f, ns, comp, ns, scname, ns, dcname))
                logger.error('EXCEPTION: ' + str(e))
            else:
                break
        else:
            logger.error('Exhausted lookaside cache synchronization attempts for {}/{} while working on {}, skipping.'.format(ns, comp, f))
            return None
    return sums

# FIXME: Don't build comp, the package might have a different name.  Get it from SCM.
def build_comp(comp, ref, ns='rpms'):
    """Submits a build for the requested component.  Requires the
    component name, namespace and the destination SCM reference to build.
    The build is submitted for the configured build target.  The build
    SCMURL is prefixed with the configured prefix.

    In the dry-run mode, the returned task ID is 0.

    :param comp: The component name
    :param ref: The SCM reference
    :param ns: The component namespace
    :returns: The build system task ID, or None on error
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    if comp in c['main']['control']['exclude'][ns]:
        logger.critical('The component {}/{} is excluded from sync, aborting.'.format(ns, comp))
        return None
    logger.info('Processing build for {}/{}.'.format(ns, comp))
    if ns == 'rpms':
        bsys = get_buildsys('destination')
        try:
            if not dry_run:
                task = bsys.build('{}/{}/{}#{}'.format(c['main']['build']['prefix'], ns, comp, ref), c['main']['build']['target'], { 'scratch': c['main']['build']['scratch'] })
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
    """Processes a fedora-messaging messages.  We can only handle Koji
    tagging events; messaging should be configured properly.

    If the message is recognized and matches our configuration or mode,
    the function calls `sync_repo()` and `build_comp()`.

    :param msg: fedora-messaging message
    :returns: None
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    logger.debug('Received a message with topic {}.'.format(msg.topic))
    if msg.topic.endswith('buildsys.tag'):
        try:
            logger.debug('Processing a tagging event message.')
            comp = msg.body['name']
            nvr = '{}-{}-{}'.format(msg.body['name'], msg.body['version'], msg.body['release'])
            tag = msg.body['tag']
            logger.debug('Tagging event for {}, tag {} received.'.format(comp, tag))
        except Exception as e:
            logger.error('Failed to process the message: {}'.format(msg))
            logger.error('EXCEPTION: ' + str(e))
        if tag == c['main']['trigger']['rpms']:
            logger.debug('Message tag configured as an RPM trigger, processing.')
            if comp in c['comps']['rpms'] or not c['main']['control']['strict']:
                logger.info('Handling an RPM trigger for {}, tag {}.'.format(comp, tag))
                if comp in c['main']['control']['exclude']['rpms']:
                    logger.info('The rpms/{} component is excluded from sync, skipping.'.format(comp))
                    return None
                ref = sync_repo(comp, ns='rpms', nvr=nvr)
                if ref is not None:
                    task = build_comp(comp, ref, ns='rpms')
                    if task is not None:
                        logger.info('Build submission of {}/{} complete, task {}, trigger processed.'.format('rpms', comp, task))
                    else:
                        logger.error('Build submission of {}/{} failed, aborting.trigger.'.format('rpms', comp))
                else:
                    logger.error('Synchronization of {}/{} failed, aborting trigger.'.format('rpms', comp))
            else:
                logger.debug('RPM component {} not configured for sync and the strict mode is enabled, ignoring.'.format(comp))
        elif tag == c['main']['trigger']['modules']:
            logger.error('The message matches our module configuration but module building not implemented, ignoring.')
        else:
            logger.debug('Message tag not configured as a trigger, ignoring.')
    else:
        logger.warning('Unable to handle {} topics, ignoring.'.format(msg.topic))

def process_components(compset):
    """Processes the supplied set of components.  If the set is empty,
    fetch all latest components from the trigger tags.

    :param compset: A set of components to process in the `ns/comp` form
    :returns: None
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    if not compset:
        logger.debug('No components selected, gathering components from triggers.')
        compset.update('{}/{}'.format('rpms', x['package_name']) for x in get_buildsys('source').listTagged(c['main']['trigger']['rpms'], latest=True))
        compset.update('{}/{}:{}'.format('modules', x['package_name'], x['version']) for x in get_buildsys('source').listTagged(c['main']['trigger']['modules'], latest=True))
    logger.info('Processing {} component(s).'.format(len(compset)))
    processed = 0
    for rec in sorted(compset, key=str.lower):
        m = cre.match(rec)
        if m is None:
            logger.error('Cannot process {}; looks like garbage.'.format(rec))
            continue
        m = m.groupdict()
        logger.info('Processing {}.'.format(rec))
        if m['namespace'] == 'modules':
            logger.warning('The {}/{} component is a module; modules currently not implemented, skipping.'.format(m['namespace'], m['component']))
            continue
        if m['component'] in c['main']['control']['exclude'][m['namespace']]:
            logger.info('The {}/{} component is excluded from sync, skipping.'.format(m['namespace'], m['component']))
            continue
        if c['main']['control']['strict'] and m['component'] not in c['comps'][m['namespace']]:
            logger.info('The {}/{} component not configured while the strict mode is enabled, ignoring.'.format(m['namespace'], m['component']))
            continue
        ref = sync_repo(comp=m['component'], ns=m['namespace'])
        if ref is not None:
            build_comp(comp=m['component'], ref=ref, ns=m['namespace'])
        logger.info('Done processing {}.'.format(rec))
        processed += 1
    logger.info('Synchronized {} component(s), {} skipped.'.format(processed, len(compset) - processed))

def get_scmurl(nvr):
    """Get SCMURL for a source build system build NVR.  NVRs are unique.

    :param nvr: The build NVR to look up
    :returns: The build SCMURL, or None on error
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    bsys = get_buildsys('source')
    if bsys is None:
        logger.error('Build system unavailable, cannot retrieve the SCMURL of {}.'.format(nvr))
        return None
    try:
        bsrc = bsys.getBuild(nvr)
    except Exception as e:
        logger.error('An error occured while retrieving the SCMURL for {}.'.format(nvr))
        logger.error('EXCEPTION: ' + str(e))
        return None
    if 'source' in bsrc:
        bsrc = bsrc['source']
        logger.debug('Retrieved SCMURL for {}: {}'.format(nvr, bsrc))
        return bsrc
    else:
        logger.error('Cannot find any SCMURLs associated with {}.'.format(nvr))
        return None

def get_build(comp, ns='rpms'):
    """Get the latest build NVR for the specified component.  Searches the
    component namespace trigger tag to locate this.  Note this is not the
    highest NVR, it's the latest tagged build.

    :param comp: The component name
    :param ns: The component namespace
    :returns: NVR of the latest build, or None on error
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    bsys = get_buildsys('source')
    if bsys is None:
        logger.error('Build system unavailable, cannot find the latest build for {}/{}.'.format(ns, comp))
        return None
    if ns == 'rpms':
        try:
            nvr = bsys.listTagged(c['main']['trigger'][ns], package=comp, latest=True)
        except Exception as e:
            logger.error('An error occured while getting the latest build for {}/{}.'.format(ns, comp))
            logger.error('EXCEPTION: ' + str(e))
            return None
        if nvr:
            logger.debug('Located the latest build for {}/{}: {}'.format(ns, comp, nvr[0]['nvr']))
            return nvr[0]['nvr']
        else:
            logger.error('Did not find any builds for {}/{}.'.format(ns, comp))
            return None
    else:
        logger.error('Modules not implemented, cannot get the latest build for {}/{}.'.format(ns, comp))
        return None

def get_buildsys(which):
    """Get a koji build system session for either the source or the
    destination.  Caches the sessions so future calls are cheap.
    Destination sessions are authenticated, source sessions are not.

    :param which: Session to select, source or destination
    :returns: Koji session object, or None on error
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    if which != 'source' and which != 'destination':
        logger.error('Cannot get "{}" build system.'.format(which))
        return None
    if not hasattr(get_buildsys, which):
        logger.debug('Initializing the {} koji instance with the "{}" profile.'.format(which, c['main'][which]['profile']))
        try:
            bsys = koji.read_config(profile_name=c['main'][which]['profile'])
            bsys = koji.ClientSession(bsys['server'], opts=bsys)
        except Exception as e:
            logger.error('Failed initializing the {} koji instance with the "{}" profile, skipping.'.format(which, c['main'][which]['profile']))
            logger.error('EXCEPTION: ' + str(e))
            return None
        logger.debug('The {} koji instance initialized.'.format(which))
        if which == 'destination':
            logger.debug('Authenticating with the destination koji instance.')
            try:
                bsys.gssapi_login()
            except Exception as e:
                logger.error('Failed authenticating against the destination koji instance, skipping.')
                logger.error('EXCEPTION: ' + str(e))
                return None
            logger.debug('Successfully authenticated with the destination koji instance.')
        if which == 'source':
            get_buildsys.source = bsys
        else:
            get_buildsys.destination = bsys
    else:
        logger.debug('The {} koji instance is already initialized, fetching from cache.'.format(which))
    return vars(get_buildsys)[which]
