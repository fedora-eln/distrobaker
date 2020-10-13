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

import distrobaker.distrobaker as baker

if __name__ == "__main__":
    baker.main()
