import logging
import os
import random
import string
import tempfile

import git
import koji
import pyrpkg
import regex
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
        except ValueError:
            logger.warning('Invalid log level passed to DistroBaker logger: %s', val)
        except Exception:
            logger.exception('Unable to set log level: %s', val)
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

    It also attempts to extract the namespace and component, where applicable.
    These can only be detected if the link matches the standard dist-git
    pattern; in other cases the results may be bogus or None.

    :param scmurl: A link#ref style URL, with #ref being optional
    :returns: A dictionary with `link`, `ref`, `ns` and `comp` keys
    """
    scm = scmurl.split('#', 1)
    nscomp = scm[0].split('/')
    return {
        'link': scm[0],
        'ref': scm[1] if len(scm) >= 2 else None,
        'ns': nscomp[-2] if len(nscomp) >= 2 else None,
        'comp': nscomp[-1] if nscomp else None,
    }

def split_module(comp):
    """Splits modules component name into name and stream pair.  Expects the
    name to be in the `name:stream` format.  Defaults to stream=master if the
    split fails.

    :param comp: The component name
    :returns: Dictionary with name and stream
    """
    ms = comp.split(':')
    return {
        'name': ms[0],
        'stream': ms[1] if len(ms) > 1 and ms[1] else 'master',
    }

def parse_sources(comp, ns, sources):
    """Parses the supplied source file and generates a set of
    tuples containing the filename, the hash, and the hashtype.

    :param comps: The component we are parsing
    :param ns: The namespace of the component
    :param sources: The sources file to parse
    :returns: A set of tuples containing the filename, the hash, and the hashtype, or None on error
    """
    src = set()
    try:
        if not os.path.isfile(sources):
            logger.debug('No sources file found for %s/%s.', ns, comp)
            return set()
        with open(sources, 'r') as fh:
            for line in fh:
                m = sre.match(line.rstrip())
                if m is None:
                    logger.error('Cannot parse "%s" from sources of %s/%s.', line, ns, comp)
                    return None
                m = m.groupdict()
                src.add((m['file'], m['hash'], 'sha512' if len(m['hash']) == 128 else 'md5'))
    except Exception:
        logger.exception('Error processing sources of %s/%s.', ns, comp)
        return None
    logger.debug('Found %d source file(s) for %s/%s.', len(src), ns, comp)
    return src

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
    logger.info('Fetching configuration from %s to %s', crepo, cdir.name)
    scm = split_scmurl(crepo)
    if scm['ref'] is None:
        scm['ref'] = 'master'
    for attempt in range(retry):
        try:
            git.Repo.clone_from(scm['link'], cdir.name).git.checkout(scm['ref'])
        except Exception:
            logger.warning('Failed to fetch configuration, retrying (#%d).', attempt + 1, exc_info=True)
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
            logger.debug('%s loaded, processing.', os.path.join(cdir.name, 'distrobaker.yaml'))
        except Exception:
            logger.exception('Could not parse distrobaker.yaml.')
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
                    logger.error('Configuration error: %s.scm missing.', k)
                    return None
                if 'cache' in cnf[k]:
                    n[k]['cache'] = dict()
                    for kc in ('url', 'cgi', 'path'):
                        if kc in cnf[k]['cache']:
                            n[k]['cache'][kc] = str(cnf[k]['cache'][kc])
                        else:
                            logger.error('Configuration error: %s.cache.%s missing.', k, kc)
                            return None
                else:
                    logger.error('Configuration error: %s.cache missing.', k)
                    return None
                if 'profile' in cnf[k]:
                    n[k]['profile'] = str(cnf[k]['profile'])
                else:
                    logger.error('Configuration error: %s.profile missing.', k)
                    return None
                if 'mbs' in cnf[k]:
                    n[k]['mbs'] = str(cnf[k]['mbs'])
                else:
                    logger.error('Configuration error: %s.mbs missing.', k)
                    return None
            else:
                logger.error('Configuration error: %s missing.', k)
                return None
        if 'trigger' in cnf:
            n['trigger'] = dict()
            for k in ('rpms', 'modules'):
                if k in cnf['trigger']:
                    n['trigger'][k] = str(cnf['trigger'][k])
                else:
                    logger.error('Configuration error: trigger.%s missing.', k)
        else:
            logger.error('Configuration error: trigger missing.')
            return None
        if 'build' in cnf:
            n['build'] = dict()
            for k in ('prefix', 'target'):
                if k in cnf['build']:
                    n['build'][k] = str(cnf['build'][k])
                else:
                    logger.error('Configuration error: build.%s missing.', k)
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
                    logger.error('Configuration error: git.%s missing.', k)
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
                    logger.error('Configuration error: control.%s missing.', k)
                    return None
            n['control']['exclude'] = { 'rpms': set(), 'modules': set() }
            if 'exclude' in cnf['control']:
                for cns in ('rpms', 'modules'):
                    if cns in cnf['control']['exclude']:
                        n['control']['exclude'][cns].update(cnf['control']['exclude'][cns])
            for cns in ('rpms', 'modules'):
                if n['control']['exclude']['rpms']:
                    logger.info('Excluding %d component(s) from the %s namespace.',
                                len(n['control']['exclude'][cns]), cns)
                else:
                    logger.info('Not excluding any components from the %s namespace.', cns)
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
                            logger.error('Configuration error: defaults.%s.%s missing.', dk, dkk)
                else:
                    logger.error('Configuration error: defaults.%s missing.', dk)
                    return None
        else:
            logger.error('Configuration error: defaults missing.')
            return None
    else:
        logger.error('The required configuration block is missing.')
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
                    cname = p
                    sname = ''
                    if k == 'modules':
                        ms = split_module(p)
                        cname = ms['name']
                        sname = ms['stream']
                    nc[k][p]['source'] = n['defaults'][k]['source'] % { 'component': cname, 'stream': sname }
                    nc[k][p]['destination'] = n['defaults'][k]['destination'] % { 'component': cname, 'stream': sname }
                    nc[k][p]['cache'] = {
                            'source': n['defaults']['cache']['source'] % { 'component': cname, 'stream': sname },
                            'destination': n['defaults']['cache']['destination'] % {
                                'component': cname, 'stream': sname
                            },
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
            logger.info('Found %d configured component(s) in the %s namespace.', len(nc[k]), k)
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
        logger.critical('The component %s/%s is excluded from sync, aborting.', ns, comp)
        return None
    logger.info('Synchronizing SCM for %s/%s.', ns, comp)
    nvr = nvr if nvr else get_build(comp, ns=ns)
    if nvr is None:
        logger.error('NVR not specified and no builds for %s/%s could be found, skipping.', ns, comp)
        return None
    logger.debug('Processing %s/%s: %s', ns, comp, nvr)
    tempdir = tempfile.TemporaryDirectory(prefix='repo-{}-{}-'.format(ns, comp))
    logger.debug('Temporary directory created: %s', tempdir.name)
    bscm = get_scmurl(nvr)
    if bscm is None:
        logger.error('Could not find build SCMURL for %s/%s: %s, skipping.', ns, comp, nvr)
        return None
    bscm = split_scmurl(bscm)
    if comp in c['comps'][ns]:
        csrc = c['comps'][ns][comp]['source']
        cdst = c['comps'][ns][comp]['destination']
    else:
        cname = comp
        sname = ''
        if ns == 'modules':
            ms = split_module(comp)
            cname = ms['name']
            sname = ms['stream']
        csrc = c['main']['defaults'][ns]['source'] % { 'component': cname, 'stream': sname }
        cdst = c['main']['defaults'][ns]['destination'] % { 'component': cname, 'stream': sname }
    sscm = split_scmurl('{}/{}/{}'.format(c['main']['source']['scm'], ns, csrc))
    dscm = split_scmurl('{}/{}/{}'.format(c['main']['destination']['scm'], ns, cdst))
    dscm['ref'] = dscm['ref'] if dscm['ref'] else 'master'
    logger.debug('Cloning %s/%s from %s/%s/%s', ns, comp, c['main']['destination']['scm'], ns, cdst)
    for attempt in range(retry):
        try:
            repo = git.Repo.clone_from(dscm['link'], tempdir.name, branch=dscm['ref'])
        except Exception:
            logger.warning('Cloning attempt #%d/%d failed, retrying.', attempt + 1, retry, exc_info=True)
            continue
        else:
            break
    else:
        logger.error('Exhausted cloning attempts for %s/%s, skipping.', ns, comp)
        return None
    logger.debug('Successfully cloned %s/%s.', ns, comp)
    logger.debug('Fetching upstream repository for %s/%s.', ns, comp)
    if sscm['ref']:
        logger.debug('Fetching the %s upstream branch for %s/%s.', sscm['ref'], ns, comp)
    else:
        logger.debug('Fetching all upstream branches for %s/%s.', ns, comp)
    repo.git.remote('add', 'source', sscm['link'])
    for attempt in range(retry):
        try:
            if sscm['ref']:
                repo.git.fetch('source', sscm['ref'])
            else:
                repo.git.fetch('--all')
        except Exception:
            logger.warning('Fetching upstream attempt #%d/%d failed, retrying.', attempt + 1, retry, exc_info=True)
            continue
        else:
            break
    else:
        logger.error('Exhausted upstream fetching attempts for %s/%s, skipping.', ns, comp)
        return None
    logger.debug('Successfully fetched upstream repository for %s/%s.', ns, comp)
    logger.debug('Configuring repository properties for %s/%s.', ns, comp)
    try:
        repo.git.config('user.name', c['main']['git']['author'])
        repo.git.config('user.email', c['main']['git']['email'])
    except Exception:
        logger.exception('Failed configuring the git repository while processing %s/%s, skipping.', ns, comp)
        return None
    logger.debug('Gathering destination files for %s/%s.', ns, comp)
    dsrc = parse_sources(comp, ns, os.path.join(tempdir.name, 'sources'))
    if dsrc is None:
        logger.error('Error processing the %s/%s destination sources file, skipping.', ns, comp)
        return None
    if c['main']['control']['merge']:
        logger.debug('Attempting to synchronize the %s/%s branches using the merge mechanism.', ns, comp)
        logger.debug('Generating a temporary merge branch name for %s/%s.', ns, comp)
        for attempt in range(retry):
            bname = ''.join(random.choice(string.ascii_letters) for i in range(16))
            logger.debug('Checking the availability of %s/%s#%s.', ns, comp, bname)
            try:
                repo.git.rev_parse('--quiet', bname, '--')
                logger.debug('%s/%s#%s is taken.  Some people choose really weird branch names.  '
                             'Retrying, attempt #%d/%d.', ns, comp, bname, attempt + 1, retry)
            except Exception:
                logger.debug('Using %s/%s#%s as the temporary merge branch name.', ns, comp, bname)
                break
        else:
            logger.error('Exhausted attempts finding an unused branch name while synchronizing %s/%s;'
                         'this is very rare, congratulations.  Skipping.', ns, comp)
            return None
        try:
            actor = '{} <{}>'.format(c['main']['git']['author'], c['main']['git']['email'])
            repo.git.checkout(bscm['ref'])
            repo.git.switch('-c', bname)
            repo.git.merge('--allow-unrelated-histories', '--no-commit', '-s', 'ours', dscm['ref'])
            repo.git.commit('--author', actor, '--allow-empty', '-m', 'Temporary working tree merge')
            repo.git.checkout(dscm['ref'])
            repo.git.merge('--no-commit', '--squash', bname)
            msg = '{}\nSource: {}#{}'.format(c['main']['git']['message'], sscm['link'], bscm['ref'])
            msgfile = tempfile.NamedTemporaryFile(prefix='msg-{}-{}-'.format(ns, comp))
            with open(msgfile.name, 'w') as f:
                f.write(msg)
            repo.git.commit('--author', actor, '--allow-empty', '-F', msgfile.name)
        except Exception:
            logger.exception('Failed to merge %s/%s, skipping.', ns, comp)
            return None
        logger.debug('Successfully merged %s/%s with upstream.', ns, comp)
    else:
        logger.debug('Attempting to synchronize the %s/%s branches using the clean pull mechanism.', ns, comp)
        try:
            repo.git.pull('--ff-only', 'source', bscm['ref'])
        except Exception:
            logger.exception('Failed to perform a clean pull for %s/%s, skipping.', ns, comp)
            return None
        logger.debug('Successfully pulled %s/%s from upstream.', ns, comp)
    logger.debug('Gathering source files for %s/%s.', ns, comp)
    ssrc = parse_sources(comp, ns, os.path.join(tempdir.name, 'sources'))
    if ssrc is None:
        logger.error('Error processing the %s/%s source sources file, skipping.', ns, comp)
        return None
    srcdiff = ssrc - dsrc
    if srcdiff:
        logger.debug('Source files for %s/%s differ.', ns, comp)
        if sync_cache(comp, srcdiff, ns) is None:
            logger.error('Failed to synchronize sources for %s/%s, skipping.', ns, comp)
            return None
    else:
        logger.debug('Source files for %s/%s are up-to-date.', ns, comp)
    logger.debug('Component %s/%s successfully synchronized.', ns, comp)
    logger.debug('Pushing synchronized contents for %s/%s.', ns, comp)
    for attempt in range(retry):
        try:
            if not dry_run:
                logger.debug('Pushing %s/%s.', ns, comp)
                repo.git.push('--set-upstream', 'origin', dscm['ref'])
                logger.debug('Successfully pushed %s/%s.', ns, comp)
            else:
                logger.debug('Pushing %s/%s (--dry-run).', ns, comp)
                repo.git.push('--dry-run', '--set-upstream', 'origin', dscm['ref'])
                logger.debug('Successfully pushed %s/%s (--dry-run).', ns, comp)
        except Exception:
            logger.warning('Pushing attempt #%d/%d failed, retrying.', attempt + 1, retry, exc_info=True)
            continue
        else:
            break
    else:
        logger.error('Exhausted pushing attempts for %s/%s, skipping.', ns, comp)
        return None
    logger.info('Successfully synchronized %s/%s.', ns, comp)
    return repo.git.rev_parse('HEAD')

def sync_cache(comp, sources, ns='rpms'):
    """Synchronizes lookaside cache contents for the given component.
    Expects a set of (filename, hash, hastype) tuples to synchronize, as
    returned by parse_sources().

    :param comp: The component name
    :param sources: The set of source tuples
    :param ns: The component namespace
    :returns: The number of files processed
    """
    if 'main' not in c:
        logger.critical('DistroBaker is not configured, aborting.')
        return None
    if comp in c['main']['control']['exclude'][ns]:
        logger.critical('The component %s/%s is excluded from sync, aborting.', ns, comp)
        return None
    logger.debug('Synchronizing %d cache file(s) for %s/%s.', len(sources), ns, comp)
    scache = pyrpkg.lookaside.CGILookasideCache('sha512',
                                                c['main']['source']['cache']['url'],
                                                c['main']['source']['cache']['cgi'])
    scache.download_path = c['main']['source']['cache']['path']
    dcache = pyrpkg.lookaside.CGILookasideCache('sha512',
                                                c['main']['destination']['cache']['url'],
                                                c['main']['destination']['cache']['cgi'])
    dcache.download_path = c['main']['destination']['cache']['path']
    tempdir = tempfile.TemporaryDirectory(prefix='cache-{}-{}-'.format(ns, comp))
    logger.debug('Temporary directory created: %s', tempdir.name)
    if comp in c['comps'][ns]:
        scname = c['comps'][ns][comp]['cache']['source']
        dcname = c['comps'][ns][comp]['cache']['destination']
    else:
        scname = c['main']['defaults']['cache']['source'] % { 'component': comp }
        dcname = c['main']['defaults']['cache']['source'] % { 'component': comp }
    for s in sources:
        # There's no API for this and .upload doesn't let us override it
        dcache.hashtype = s[2]
        for attempt in range(retry):
            try:
                if not dcache.remote_file_exists('{}/{}'.format(ns, dcname), s[0], s[1]):
                    logger.debug('File %s for %s/%s (%s/%s) not available in the destination cache, downloading.',
                                 s[0], ns, comp, ns, dcname)
                    scache.download('{}/{}'.format(ns, scname), s[0], s[1], os.path.join(tempdir.name, s[0]),
                                    hashtype=s[2])
                    logger.debug('File %s for %s/%s (%s/%s) successfully downloaded.  '
                                 'Uploading to the destination cache.', s[0], ns, comp, ns, scname)
                    if not dry_run:
                        dcache.upload('{}/{}'.format(ns, dcname), os.path.join(tempdir.name, s[0]), s[1])
                        logger.debug('File %s for %s/%s (%s/%s) )successfully uploaded to the destination cache.',
                                     s[0], ns, comp, ns, dcname)
                    else:
                        logger.debug('Running in dry run mode, not uploading %s for %s/%s.', s[0], ns, comp)
                else:
                    logger.debug('File %s for %s/%s (%s/%s) already uploaded, skipping.',
                                 s[0], ns, comp, ns, dcname)
            except Exception:
                logger.warning('Failed attempt #%d/%d handling %s for %s/%s (%s/%s -> %s/%s), retrying.',
                               attempt + 1, retry, s[0], ns, comp, ns, scname, ns, dcname, exc_info=True)
            else:
                break
        else:
            logger.error('Exhausted lookaside cache synchronization attempts for %s/%s while working on %s, skipping.',
                         ns, comp, s[0])
            return None
    return len(sources)

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
        logger.critical('The component %s/%s is excluded from sync, aborting.', ns, comp)
        return None
    logger.info('Processing build for %s/%s.', ns, comp)
    if ns == 'rpms':
        bsys = get_buildsys('destination')
        buildcomp = comp
        if comp in c['comps'][ns]:
            buildcomp = split_scmurl(c['comps'][ns][comp]['destination'])['comp']
        try:
            if not dry_run:
                task = bsys.build('{}/{}/{}#{}'.format(c['main']['build']['prefix'], ns, buildcomp, ref),
                                  c['main']['build']['target'],
                                  { 'scratch': c['main']['build']['scratch'] })
                logger.debug('Build submitted for %s/%s; task %d; SCMURL: %s/%s/%s#%s.',
                             ns, comp, task, c['main']['build']['prefix'], ns, buildcomp, ref)
            else:
                task = 0
                logger.info('Running in the dry mode, not submitting any builds for %s/%s (%s/%s/%s#%s).',
                            ns, comp, c['main']['build']['prefix'], ns, buildcomp, ref)
            return task
        except Exception:
            logger.exception('Failed submitting build for %s/%s (%s/%s/%s#%s).',
                             ns, comp, c['main']['build']['prefix'], ns, comp, ref)
            return None
    elif ns == 'modules':
        logger.critical('Cannot build %s/%s; module building not implemented.', ns, comp)
        return None
    else:
        logger.critical('Cannot build %s/%s; unknown namespace.', ns, comp)
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
    logger.debug('Received a message with topic %s.', msg.topic)
    if msg.topic.endswith('buildsys.tag'):
        try:
            logger.debug('Processing a tagging event message.')
            comp = msg.body['name']
            nvr = '{}-{}-{}'.format(msg.body['name'], msg.body['version'], msg.body['release'])
            tag = msg.body['tag']
            logger.debug('Tagging event for %s, tag %s received.', comp, tag)
        except Exception:
            logger.exception('Failed to process the message: %s', msg)
            return None
        if tag == c['main']['trigger']['rpms']:
            logger.debug('Message tag configured as an RPM trigger, processing.')
            if comp in c['comps']['rpms'] or not c['main']['control']['strict']:
                logger.info('Handling an RPM trigger for %s, tag %s.', comp, tag)
                if comp in c['main']['control']['exclude']['rpms']:
                    logger.info('The rpms/%s component is excluded from sync, skipping.', comp)
                    return None
                ref = sync_repo(comp, ns='rpms', nvr=nvr)
                if ref is not None:
                    task = build_comp(comp, ref, ns='rpms')
                    if task is not None:
                        logger.info('Build submission of rpms/%s complete, task %s, trigger processed.', comp, task)
                    else:
                        logger.error('Build submission of rpms/%s failed, aborting.trigger.', comp)
                else:
                    logger.error('Synchronization of rpms/%s failed, aborting trigger.', comp)
            else:
                logger.debug('RPM component %s not configured for sync and the strict mode is enabled, ignoring.', comp)
        elif tag == c['main']['trigger']['modules']:
            logger.error('The message matches our module configuration but module building not implemented, ignoring.')
        else:
            logger.debug('Message tag not configured as a trigger, ignoring.')
    else:
        logger.warning('Unable to handle %s topics, ignoring.', msg.topic)

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
        compset.update('{}/{}'.format('rpms', x['package_name']) for x in get_buildsys('source').listTagged(
            c['main']['trigger']['rpms'], latest=True))
        compset.update('{}/{}:{}'.format('modules', x['package_name'], x['version']) for x in get_buildsys(
            'source').listTagged(c['main']['trigger']['modules'], latest=True))
    logger.info('Processing %d component(s).', len(compset))
    processed = 0
    for rec in sorted(compset, key=str.lower):
        m = cre.match(rec)
        if m is None:
            logger.error('Cannot process %s; looks like garbage.', rec)
            continue
        m = m.groupdict()
        logger.info('Processing %s.', rec)
        if m['namespace'] == 'modules':
            logger.warning('The modules/%s component is a module; modules currently not implemented, skipping.',
                           m['component'])
            continue
        if m['component'] in c['main']['control']['exclude'][m['namespace']]:
            logger.info('The %s/%s component is excluded from sync, skipping.', m['namespace'], m['component'])
            continue
        if c['main']['control']['strict'] and m['component'] not in c['comps'][m['namespace']]:
            logger.info('The %s/%s component not configured while the strict mode is enabled, ignoring.',
                        m['namespace'], m['component'])
            continue
        ref = sync_repo(comp=m['component'], ns=m['namespace'])
        if ref is not None:
            build_comp(comp=m['component'], ref=ref, ns=m['namespace'])
        logger.info('Done processing %s.', rec)
        processed += 1
    logger.info('Synchronized %d component(s), %d skipped.', processed, len(compset) - processed)

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
        logger.error('Build system unavailable, cannot retrieve the SCMURL of %s.', nvr)
        return None
    try:
        bsrc = bsys.getBuild(nvr)
    except Exception:
        logger.exception('An error occured while retrieving the SCMURL for %s.', nvr)
        return None
    if 'source' in bsrc:
        bsrc = bsrc['source']
        logger.debug('Retrieved SCMURL for %s: %s', nvr, bsrc)
    else:
        bsrc = None
        logger.error('Cannot find any SCMURLs associated with %s.', nvr)
    return bsrc

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
        logger.error('Build system unavailable, cannot find the latest build for %s/%s.', ns, comp)
        return None
    if ns == 'rpms':
        try:
            nvr = bsys.listTagged(c['main']['trigger'][ns], package=comp, latest=True)
        except Exception:
            logger.exception('An error occured while getting the latest build for %s/%s.', ns, comp)
            return None
        if nvr:
            logger.debug('Located the latest build for %s/%s: %s', ns, comp, nvr[0]['nvr'])
            return nvr[0]['nvr']
        logger.error('Did not find any builds for %s/%s.', ns, comp)
        return None
    elif ns == 'modules':
        logger.error('Modules not implemented, cannot get the latest build for %s/%s.', ns, comp)
        return None
    logger.error('Unrecognized namespace: %s/%s', ns, comp)
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
        logger.error('Cannot get "%s" build system.', which)
        return None
    if not hasattr(get_buildsys, which):
        logger.debug('Initializing the %s koji instance with the "%s" profile.', which, c['main'][which]['profile'])
        try:
            bsys = koji.read_config(profile_name=c['main'][which]['profile'])
            bsys = koji.ClientSession(bsys['server'], opts=bsys)
        except Exception:
            logger.exception('Failed initializing the %s koji instance with the "%s" profile, skipping.',
                             which, c['main'][which]['profile'])
            return None
        logger.debug('The %s koji instance initialized.', which)
        if which == 'destination':
            logger.debug('Authenticating with the destination koji instance.')
            try:
                bsys.gssapi_login()
            except Exception:
                logger.exception('Failed authenticating against the destination koji instance, skipping.')
                return None
            logger.debug('Successfully authenticated with the destination koji instance.')
        if which == 'source':
            get_buildsys.source = bsys
        else:
            get_buildsys.destination = bsys
    else:
        logger.debug('The %s koji instance is already initialized, fetching from cache.', which)
    return vars(get_buildsys)[which]
