# DistroBaker

DistroBaker is a simple service for downstream operating system
distributions that allows for simple and automatic syncs of
upstream component repositories into downstream branches, as well as
automatic component builds in the downstream distribution.

Initially written for Red Hat Enterprise Linux and CentOS Stream.

## Features

* Configuration fetching and parser
* Fedora messaging bus monitoring and its `buildsys.tag` messages
* Git repository manipulation for SCM syncs with fast forward and
  squash merging strategies

## Usage

```
% distrobaker [-l LOGLEVEL] [-u UPDATE] [-r RETRY] [-1] [-d] config
```

`config` is a mandatory positional argument and points to a configuration
repository holding `distrobaker.yaml`; see below.  Optionally branch
name can be specified using the `url#branch` syntax.

`-l` or `--loglevel` accepts standard Python `logging` module log levels;
defaults to `INFO`.

`-u` or `--update` sets the configuration update interval in minutes;
defaults to 15 minutes.

`-r` or `--retry` sets the number of retries on failures, such as on
clones, pulls, cache downloads and uploads and pushes; defaults to 5.

`-1` or `--oneshot` runs DistroBaker in a one-shot mode, iterating over
all configured components and resyncing.  Useful for bootstrapping;
defaults to false, where DistroBaker runs in a service mode listening
for tagging messages.

`-d` or `--dry-run` runs DistroBaker in a dry-run mode where all
potentially destructive operations are skipped.  This includes cache
uploads, SCM pushes and component builds; defaults to non-pretend mode.

## Implementation

The tool fetches its configuration from the provided repository
and either performs a complete sync of all configured components
(in the one-shot mode) or listens for bus messages triggering
individual component syncs (in the service mode, the default).

If started in the service mode, it connects to the messaging bus
as configured in the `fedora_messaging` configuration file, defined
by the `FEDORA_MESSAGING_CONF` environment variable.
Only `buildsys.tag` messages are currently processed.

If the tag message matches any of the configured components, the
tool syncs the SCM repositories as configured and, optionally,
triggers a build in the target build system using the configured
profile.

Currently only Koji and Fedora Messaging are supported.

## Configuration format

The tool expects the configuration file to be located in
`(config)/distrobaker.yaml`.  A non-functional example
of the configuration file below.

```yaml
configuration:
  source:
    scm: https://src.fedoraproject.org/
    cache:
      url: https://src.fedoraproject.org/repo/pkgs
      cgi: https://src.fedoraproject.org/repo/pkgs/upload.cgi
      path: "%(name)s/%(filename)s/%(hashtype)s/%(hash)s/%(filename)s"
  destination:
    scm: ssh://pkgs.example.com/
    cache:
      url: http://pkgs.example.com/repo
      cgi: http://pkgs.example.com/lookaside/upload.cgi
      path: "%(name)s/%(filename)s/%(hashtype)s/%(hash)s/%(filename)s"
  trigger:
    rpms: rawhide
    modules: rawhide-modular
  build:
    profile: brew
    scratch: false
    prefix: git://pkgs.example.com/
    target: fluff-42.0.0-alpha-candidate
    mbs: https://mbs.example.com/
  git:
    author: DistroBaker
    email: noreply@example.com
    message: >
      Merged update from upstream sources

      This is an automated DistroBaker update from upstream sources.  If you do not
      know what this is about or would like to opt out, contact the DistroBaker maintainers.
  control:
    build: true
    merge: true
components:
  rpms:
    gzip:
      source: gzip.git
      destination: gzip.git#fluff-42.0.0-alpha
  modules:
    testmodule-master:
      source: testmodule.git#master
      destination: testmodule#stream-master-fluff-42.0.0-alpha
```

### Configuration options

This section explains each of the tags noted in the example above.

#### `configuration`

This section covers the basic DistroBaker configuration.  All fields are
mandatory unless otherwise noted.

##### `source`

The `source` block configures the upstream source for component sync,
specifically the `scm` root as well as the lookaside `cache`.

`scm` is a base URL of the upstream source control.  Read-only access
is sufficient.

`cache` and defines the lookaside `url`, `cgi` and `path`, where `url`
is the cache base URL, `cgi` is its upload interface and `path` is
Python-formatted string passed to pyrpkg defining the file path used
by this particular cache.

Example:

```yaml
source:
  scm: https://src.fedoraproject.org
  cache:
    url: https://src.fedoraproject.org/repo/pkgs
    cgi: https://src.fedoraproject.org/repo/pkgs/upload.cgi
    path: "%(name)s/%(filename)s/%(hashtype)s/%(hash)s/%(filename)s"
```

##### `destination`

The `destination` block configures the downstream destination source control and
cache.  DistroBaker needs write access to both to effectively sync components.

The structure is the same as that of the `source` block.

##### `trigger`

The `trigger` block defines Koji tag triggers for supported namespaces.  Currently
this includes `rpms` and `modules`.  The properties are namespace names, their
values are the respective tag names.

Example:

```yaml
trigger:
  rpms: rawhide
  modules: rawhide-modular
```

##### `build`

The `build` block configures the destination build system, both Koji and MBS.

The `profile` property defines the Koji profile configuration name.  These are
typically sourced from `/etc/koji` and provide relevant interfaces and certificates.

The `scratch` property defines whether submitted builds should be real or scratch
builds.  This is optional and defaults to `false`.

The `prefix` property defines the URL used to prefix the namespace and the component
name upon submission.  This is typically an SCM interface the build system can access.
Could be read-only.

The `target` property defines the destination build system target.  Targets are buildroot
and destination tag tuples.

The `mbs` property is currently a string placeholder.

##### `git`

The `git` block configures git `author`, `email` and the commit `message` used
during merge operations.

The `message` is always extended with `Source: url#ref`, referencing the upstream
commit used as a base for the merge.

Hint: Use the `|`-style YAML text blocks to preserve newlines.

Example:

```yaml
git:
  author: DistroBaker
  email: osci-team@example.com
  message: |
    Merged update from upstream sources

    This is an automated DistroBaker update from upstream sources.
    If you do not know what this is about or would like to opt out,
    contact the OSCI team.
```

##### `control`

The `control` block configures the basic operation.

The `build` property controls whether builds get submitted.

The `merge` property controls whether DistroBaker attempts to do clean
fast forward pulls (`false`) or squashed merges (`true`).

Example:

```yaml
control:
  build: true
  merge: true
```

#### `components`

This section defines synchronization components listed under their respective
namespaces.  Currently `rpms` and `modules` are supported.  Namespace and
component key names matter for SCM URL affixing and lookaside cache paths.

Every component must define two fields, `source` and `destination`.

##### `source`

The source repository for the component with optional branch in the
`repository#branch` format.  Repository name is concatenated with the
source SCM URL (`configuration.source.scm`) and the relevant namespace.

If no branch is provided, `master` is assumed.

Example: `source: gzip.git#f34`

##### `destination`

The destination repository for the component with optional branch in the
`repository#branch` format.  Repository name is concatenated with the
destination SCM URL (`configuration.destination.scm`) and the relevant
namespace.

If no branch is provided, `master` is assumed.

Example: `destination: gzip.git#fluff-42.0.0-beta`
