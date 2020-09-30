# DistroBaker

DistroBaker is a simple service for downstream operating system
distributions that allows for simple and automatic syncs of
upstream component repositories into downstream branches, as well as
automatic component builds in the downstream distribution.

Initially written for Red Hat Enterprise Linux and CentOS Stream.

## Current features

* Configuration fetching and parser
* Fedora messaging bus monitoring and its `buildsys.tag` messages
* Git repository manipulation for SCM syncs (unauthenticated)

## Planned

* Reload configuration on the fly at certain intervals or with
  each tag message (what's more feasible)
* Tests with a local broker
* A lot more error checking and recovery
* Configurable merge message

## Implementation

The tool fetches its configuration from a git repository
configured by the `DISTROBAKERCONF` environment variable.  It
connects to the messaging bus as configured in the
`fedora_messaging` configuration file, defined by the
`FEDORA_MESSAGING_CONF` environment variable.  Only `buildsys.tag`
messages are currently assumed.

Furthermore, `DISTROBAKERUSER` and `DISTROBAKEREMAIL` environment
variables define the user used for git merge commits.

If the tag message matches any of the configured components, the
tool syncs the SCM repositories as configured and, optionally,
triggers a build in the target build system using the configured
profile.

Currently only Koji and Fedora Messaging are supported.

## Configuration format

The tool expects the configuration file to be located in
`${DISTROBAKERCONF}/distrobaker.yaml`.  A non-functional example
of the configuration file below.

```yaml
# General source and destination configuration, plus default
# values for components.
configuration:
    source: https://src.fedoraproject.org/rpms/
    destination: /tmp/
    trigger: rawhide
    target: rhel-9.0.0-candidate
    profile: brew
    build: false
    merge: false
# Component configuration.  At least one component is required.
# Source and destination properties are required and are appended
# to the configuration properties of the same names.  Branch names
# are optional and separated by the pound symbol (#).
# Triggers, targets and build can be overriden per component if required.
components:
    gcc:
        source: gcc.git
        destination: gcc#rhel-9.0.0
    glibc:
        source: glibc.git#test
        destination: glibc#rhel-9.0.0-test
        trigger: glibc-test-tag
        target: rhel-9.0.0-glibc-test-candidate
        build: true
        merge: true
```

### Flags

This section explains each of the tags noted in the example above.

#### `source`

In the `configuration` section this holds the global dist-git prefix
holding the sources and it's mandatory.  An example would be a local
directory with bare repositories, or a remote dist-git instance, including
the namespace.

In the `components` section this holds the component's suffix that is
appended to the global prefix, creating a path to the unique repository.
It may optionally include the branch name, separated from the repository
name with the pound symbols (`#`).  If the branch name is not provided,
`master` is assumed.  Mandatory.

#### `destination`

Analogous to source, in the `configuration` section this holds the global
prefix for all destination repositories and is mandatory.

In the `components` section, this key holds the destination repository name
which is appended to the global prefix, with the optional branch name
at the end following the pound symbol (`#`).  If the branch is not provided,
`master` is assumed.  Mandatory.

#### `trigger`

Defines the Koji tag name serving as a trigger during the build tagging event.
This can be defined both globally in the `configuration` section for all
components, or individually overridden in the `components` section.

If not defined per component, the global default is used.  The default is
mandatory, per component definitions are optional.

DistroBaker watches for the `trigger` build tagging events on the source
Koji instance via fedora-messaging configured in `FEDORA_MESSAGING_CONF`.

#### `target`

Defines the destination Koji build target, a Koji-defined pair of buildroot
and destination tags in the destination Koji instance.  This can be defined
both globally in the `configuration` section for all components, or individually
overrides in the `components` section.

If not defined per component, the global default is used.  The default is
mandatory, per component definitions are optional.

Destination Koji instance is determined by the profile flag, see below.

#### `profile`

Defines the rpkg profile used, configuring access to the destinations Koji
instance.  The DistroBaker instance must have access credentials to access
the target instance, namely a valid Kerberos ticket.

The flag can be configured globally in the `configuration` section and
individually overrides in the `components` section.

The global default is mandatory, the per component configuration optional.
If per component value is not defined, the global is used.

#### `build`

A boolean value controlling whether component builds should also be submitted.
Like most flags, it can be defined globally in the `configuration` section and
individually overriden in the `components` section.

The global default is mandatory, the per component configuration optional.  If
per component value is not defined, the global is used.

If true, DistroBaker will submit builds to the destination Koji instance as per
`profile` and `target` flags.

If false, DistroBaker will only sync dist-git content.

#### `merge`

A boolean value controlling whether DistroBaker should merge changes with a merge
commit in cases where clean fast forward merges (or pulls) are not possible.
Like most flags, it can be defined globally in the `configuration` section and
individually override in the `components` section.

The global default is mandatory, the per component configuration optional.  If
per component value is not defined, the global is used.

If true, DistroBaker will merge source branches into destination branches using
the `-X theirs` strategy and create a merge commit using `DISTROBAKERUSER` and
`DISTROBAKEREMAIL`.  The commit message itself is currently hardcoded.

If false, DistroBaker will report a warning and continue.

Note that pushing merge commits will prevent future pulls and all syncs from that
point will require a new merge commit.  Also note the potential of destroying
downstream-only changes with this approach.  The recommended setting is `false`.

## Scripts

The `scripts` directory contains various scripts extending the code DistroBaker
functionality.  Currently only one is provided:

### `initialsync.py`

The `initialsync.py` script performs the initial sync based on the configuration
without listening to messages and triggering syncs and builds via tagging events.

Its purpose is to prepare the target distribution in one go and should generally
only be used once, with DistroBaker later keeping it up to date.

It uses the DistroBaker module and `DISTROBAKER` variables have to be set.
