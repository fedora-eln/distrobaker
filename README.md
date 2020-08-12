# DistroBaker

DistroBaker is a simple service for downstream operating system
distributions that allows for simple and automatic syncs of
upstream component repositories into downstream branches, as well as
automatic component builds in the downstream distribution.

Initially written for Red Hat Enterprise Linux and CentOS Stream.

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

## Current features

* Configuration fetching and parser
* Fedora messaging bus monitoring and its `buildsys.tag` messages
* Git repository manipulation for SCM syncs (unauthenticated)

## Planned

* Authenticated SCM syncs
* Authenticated component builds
* Reload configuration on the fly at certain intervals or with
  each tag message (what's more feasible)
* REST API for status monitoring
* Authenticated REST API for manual sync and build triggers
* Logging
* Tests with a local broker
* A lot more error checking and recovery

## Configuration format

This is not the final format yet and is subject to change as the
tool develops.

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
```
