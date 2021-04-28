# SPDX-License-Identifier: MIT

import distrobaker
import helpers

try:
    import unittest2 as unittest
except ImportError:
    import unittest


class TestProcessComponentsUnconfigured(unittest.TestCase):
    def setUp(self):
        # be sure to do no harm
        distrobaker.dry_run = True
        # reset global configuration in case it is set from running other tests
        distrobaker.c = dict()

    def test_unconfigured_empty_set(self):
        with self.assertLogs(distrobaker.logger) as cm:
            distrobaker.process_components(set())
        # make sure expected error appears in logger output
        expected_error = "DistroBaker is not configured, aborting"
        self.assertTrue(
            helpers.strings_with_substring(cm.output, expected_error),
            msg="'{}' not found in logger output: {}".format(
                expected_error, cm.output
            ),
        )
