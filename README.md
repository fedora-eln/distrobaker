# DistroBaker

DistroBaker is a simple service for downstream operating system distributions
that allows for simple and automatic syncs of upstream component repositories
into downstream branches, as well as automatic component builds in the
downstream distribution.

Initially written for Red Hat Enterprise Linux and CentOS Stream.

## Features

* Configuration fetching and parser
* Fedora messaging bus monitoring and its `buildsys.tag` messages
* Git repository manipulation for SCM syncs with fast forward and squash
  merging strategies

## Usage

```
% distrobaker [-l LOGLEVEL] [-u UPDATE] [-r RETRY] [-1] [-d|-n] [-s SELECT] config
```

`config` is a mandatory positional argument and points to a configuration
repository holding `distrobaker.yaml`; see below.  Optionally branch name can
be specified using the `url#branch` syntax.

`-l` or `--loglevel` accepts standard Python `logging` module log levels;
defaults to `INFO`.

`-u` or `--update` sets the configuration update interval in minutes; defaults
to 5 minutes.

`-r` or `--retry` sets the number of retries on failures, such as on clones,
pulls, cache downloads and uploads and pushes; defaults to 5.

`-1` or `--oneshot` runs DistroBaker in a one-shot mode, iterating over all
configured components and resyncing.  Useful for bootstrapping; defaults to
false, where DistroBaker runs in a service mode listening for tagging messages.

With `strict: false`, DistroBaker queries the respective trigger tags for the
list of components.

`-d`, `-n` or `--dry-run` runs DistroBaker in a dry-run mode where all
potentially destructive operations are skipped.  This includes cache uploads,
SCM pushes and component builds; defaults to non-pretend mode.

`-s` or `--select` limits the component set to the specified space-separated
list of components in the `ns/component` form.

### Examples

Start in the generic service mode, with debug logging, fetching the
configuration from a remote repository and the `prod` branch:

`% distrobaker -l debug https://example.com/distrobaker.git#prod`

Do a one time sync of all the components, excessively retrying 15 times upon
each failure.  Configuration is in a local repository named `conf`.

`% distrobaker -1 -r 15 conf`

A single test sync run for three specific components using a local repository:

`% distrobaker -1 -n -s 'rpms/gzip rpms/bzip2 rpms/gzip' /tmp/conf#testbranch`

## Implementation

The tool fetches its configuration from the provided repository and either
performs a complete sync of all configured components (in the one-shot mode) or
listens for bus messages triggering individual component syncs (in the service
mode, the default).

If started in the service mode, it connects to the messaging bus as configured
in the `fedora_messaging` configuration file, defined by the
`FEDORA_MESSAGING_CONF` environment variable.  Only `buildsys.tag` messages are
currently processed.

The tool syncs the SCM repositories as configured and, optionally, triggers a
build in the target build system using the configured profile.

What input DistroBaker accepts as valid depends on whether it's running in the
strict mode or not.  See `configuration.control.strict` for more details.

Currently only Koji and Fedora Messaging are supported.

## Configuration format

The tool expects the configuration file to be located in
`(config)/distrobaker.yaml`.  A non-functional example of the configuration
file below.

```yaml
configuration:
  source:
    scm: https://src.fedoraproject.org/
    cache:
      url: https://src.fedoraproject.org/repo/pkgs
      cgi: https://src.fedoraproject.org/repo/pkgs/upload.cgi
      path: "%(name)s/%(filename)s/%(hashtype)s/%(hash)s/%(filename)s"
    profile: koji
  destination:
    scm: ssh://pkgs.example.com/
    cache:
      url: http://pkgs.example.com/repo
      cgi: http://pkgs.example.com/lookaside/upload.cgi
      path: "%(name)s/%(filename)s/%(hashtype)s/%(hash)s/%(filename)s"
    profile: brew
    mbs:
      api_url: https://mbs.example.com/module-build-service/1/
      auth_method: oidc
      oidc_id_provider: https://id.example.com/openidc/
      oidc_client_id: mbs-authorizer
      oidc_client_secret: notsecret
      oidc_scopes:
        - openid
        - https://id.example.com/scope/groups
        - https://mbs.example.com/oidc/submit-build
  trigger:
    rpms: rawhide
    modules: rawhide-modular
  build:
    prefix: git://pkgs.example.com/
    target: fluff-42.0.0-alpha-candidate
    scratch: false
    platform: platform:fl42
  git:
    author: DistroBaker
    email: noreply@example.com
    message: >
      Merged update from upstream sources

      This is an automated DistroBaker update from upstream sources.  If you do not
      know what this is about or would like to opt out, contact the DistroBaker maintainers.
  control:
    strict: false
    build: true
    merge: true
    exclude:
      rpms:
        - firefox
        - kernel
        - thunderbird
      modules:
        - testmodule2:master
  defaults:
    rpms:
      source: "%(component)s.git"
      destination: "%(component)s.git#fluff-42.0.0-alpha"
    modules:
      source: "%(component)s.git#%(stream)s"
      destination: "%(component)s.git#%(stream)s-fluff-42.0.0-alpha"
      rpms:
        source: "%(component)s.git"
        destination: "%(component)s.git#stream-%(name)s-%(stream)s"
    cache:
      source: "%(component)s"
      destination: "%(component)s"
components:
  rpms:
    gzip:
      source: gzip.git
      destination: gzip.git#fluff-42.0.0-alpha-experimental
    ipa:
      source: freeipa.git#f33
      cache:
        source: freeipa
        destination: ipa
  modules:
    testmodule:master:
      destination: testmodule#stream-master-fluff-42.0.0-alpha-experimental
      rpms:
        componentrpm:
          source: componentsource.git#sourcebranch
          destination: coomponentrpm.git#fluff-42.0.0-alpha-experimental
```

### Configuration options

This section explains each of the tags noted in the example above.

#### `configuration`

This section covers the basic DistroBaker configuration.  All fields are
mandatory unless otherwise noted.

##### `source`

The `source` block configures the upstream source for component sync,
specifically the `scm` root as well as the lookaside `cache`, the Koji build
system profile and the MBS instance.

`scm` is a base URL of the upstream source control.  Read-only access is
sufficient.

`cache` defines the lookaside `url`, `cgi` and `path`, where `url` is the cache
base URL, `cgi` is its upload interface and `path` is Python-formatted string
passed to pyrpkg defining the file path used by this particular cache.

`profile` specifies the Koji build system profile name; the specified
configuration must be available on the host, along with the necessary
certificates.

Example:

```yaml
source:
  scm: https://src.fedoraproject.org
  cache:
    url: https://src.fedoraproject.org/repo/pkgs
    cgi: https://src.fedoraproject.org/repo/pkgs/upload.cgi
    path: "%(name)s/%(filename)s/%(hashtype)s/%(hash)s/%(filename)s"
  profile: koji
```

##### `destination`

The `destination` block configures the downstream destination source control
and cache.  DistroBaker needs write access to both to effectively sync
components.

The structure is the same as that of the `source` block plus the addition of a
mandatory `mbs` block.

The `mbs` block configures the MBS instance used for building modules. It must
always contain `api_url` and `auth_method` properties. `auth_method` must be
`kerberos` or `oidc`. When `auth_method` is `oidc`, additional
`oidc_id_provider`, `oidc_client_id`, `oidc_client_secret`, and `oidc_scopes`
properties must be provided. The values to use for the `mbs` sub-properties
can be taken directly from the `[<profile>.mbs]` section of the appropriate
`/etc/rpkg/<profile>.conf` (eg., `/etc/rpkg/centpkg.conf`) file.

Example:

```yaml
destination:
  scm: ssh://pkgs.example.com/
  cache:
    url: http://pkgs.example.com/repo
    cgi: http://pkgs.example.com/lookaside/upload.cgi
    path: "%(name)s/%(filename)s/%(hashtype)s/%(hash)s/%(filename)s"
  profile: brew
  mbs:
    api_url: https://mbs.example.com/module-build-service/1/
    auth_method: oidc
    oidc_id_provider: https://id.example.com/openidc/
    oidc_client_id: mbs-authorizer
    oidc_client_secret: notsecret
    oidc_scopes:
      - openid
      - https://id.example.com/scope/groups
      - https://mbs.example.com/oidc/submit-build
```

##### `trigger`

The `trigger` block defines Koji tag triggers for supported namespaces.
Currently this includes `rpms` and `modules`.  The properties are namespace
names, their values are the respective tag names.

Example:

```yaml
trigger:
  rpms: rawhide
  modules: rawhide-modular
```

##### `build`

The `build` block configures the destination build system, both Koji and MBS.

The `scratch` property defines whether submitted builds should be real or
scratch builds.  This is optional and defaults to `false`.

The `prefix` property defines the URL used to prefix the namespace and the
component name upon submission.  This is typically an SCM interface the build
system can access.  Could be read-only.

The `target` property defines the destination build system target.  Targets are
buildroot and destination tag tuples.

The `platform` property defines the destination module build system target platform in `<name>:<stream>` format.

Example:

```yaml
build:
  prefix: git://pkgs.example.com/
  target: fluff-42.0.0-alpha-candidate
  scratch: false
  platform: platform:fl42
```

##### `git`

The `git` block configures git `author`, `email` and the commit `message` used
during merge operations.

The `message` is always extended with `Source: url#ref`, referencing the
upstream commit used as a base for the merge.

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

The `strict` property controls whether DistroBaker accepts all inputs or
rejects components not explicitly configured.  With `strict: false`, any
tagging event from the configured trigger will result in sync operations,
unless the component is excluded (see `control.exclude`).  In the one-shot
mode `strict: false` processes all components tagged in the trigger tag,
or the selected set, unless the components are excluded.  With `strict: true`,
only components explicitly configured will be accepted in either mode.

The `build` property controls whether builds get submitted.

The `merge` property controls whether DistroBaker attempts to do clean fast
forward pulls (`false`) or squashed merges (`true`).

The `exclude` block is split into namespaces, `rpms` and `modules`.  Both
the block and the namespaces are optional.  If provided, DistroBaker will
refuse to sync the listed components in all cases.

Example:

```yaml
control:
  strict: false
  build: true
  merge: true
  exclude:
    rpms:
      - firefox
      - kernel
```

##### `defaults`

The `defaults` block provides string templates for the components section,
defining the basic values applied to unknown components with `strict: false`,
or known and defined components that do not define these fields.

The block is split into three identical sections, `cache` and the namespaces,
`rpms` and `modules`.  Each holds two properties, `source` and `destination`.

The `modules` namespace section can also contain an `rpms` namespace
sub-section with `source` and `destination` properties that will be applied to
RPM sub-components of modules.

The values are old-style Python format strings formatted with `%(component)s` for
the component name, and `%(stream)s` for the module stream name.  Values in the
`rpms` sub-section of the `modules` namespace can also use `%(name)s` for the
module name, and `%(ref)s` for the modulemd-provided `ref`.

Example:

```yaml
defaults:
  cache:
    source: "%(component)s"
    destination: "%(component)s"
  rpms:
    source: "%(component)s.git"
    destination: "%(component)s.git#fluff-42.0.0-alpha"
  modules:
    source: "%(component)s.git#%(stream)s"
    destination: "%(component)s.git#%(stream)s-fluff-42.0.0-alpha"
    rpms:
      source: "%(component)s.git"
      destination: "%(component)s.git#stream-%(name)s-%(stream)s"
```

#### `components`

This section defines synchronization components listed under their respective
namespaces.  Currently `rpms` and `modules` are supported.  Namespace and
component key names matter for SCM URL affixing and lookaside cache paths,
unless overriden.

Components may define their `source`, `destination` and `cache`.  Omitted
fields are populated from the defaults.  See `configuration.defaults`.

Components in the `modules` namespace may also contain an `rpms` sub-section
that can define overriding `source`, `destination` and `cache` properties for
specific RPM sub-components of that module.

If components need to be defined explicitly (for instance for `strict: true`)
without overriding any defaults, both an empty dictionary and null are valid.
For example:

```yaml
components:
  rpms:
    bzip2: {}
    gzip: ~
```

##### `source`

The source repository for the component with optional branch in the
`repository#branch` format.  Repository name is concatenated with the source
SCM URL (`configuration.source.scm`) and the relevant namespace.

If a branch is provided, DistroBaker will search the relevant build commits
in that branch.  If omitted, DistroBaker will fetch all remote branches.

Example: `source: gzip.git#f34`

##### `destination`

The destination repository for the component with optional branch in the
`repository#branch` format.  Repository name is concatenated with the
destination SCM URL (`configuration.destination.scm`) and the relevant
namespace.

If no branch is provided, `master` is assumed.

Example: `destination: gzip.git#fluff-42.0.0-beta`

##### `cache`

The `cache` block defines lookaside caches names for the source and destination.

Example:

```yaml
cache:
  source: foo
  destination: bar
```

## Development

### Code style

Please format code using `black -l 79`.

### Unit-testing

Install packages required to test the python scripts:

```
$ sudo dnf install -y \
    gcc \
    cairo-gobject-devel \
    git \
    gobject-introspection-devel \
    krb5-devel \
    libcurl-devel \
    openssl-devel \
    python3-devel \
    rpm-devel \
    tox
```

Run the tests:

```
$ tox
```
