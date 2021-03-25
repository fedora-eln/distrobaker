# SPDX-License-Identifier: MIT

import distrobaker
import helpers
import os
import sys
import tempfile

from parameterized import parameterized

try:
    import unittest2 as unittest
except ImportError:
    import unittest


class TestConfigSetting(unittest.TestCase):
    def test_initial_config(self):
        # configuration should start out empty
        cfg = distrobaker.get_config()
        self.assertIs(type(cfg), dict)
        self.assertFalse(cfg)

    def test_load_config(self):
        with tempfile.TemporaryDirectory() as td:
            helpers.setup_test_repo(
                td,
                os.path.join(helpers.DATA_DIR, "config", "distrobaker.yaml"),
            )
            # attempting to load config without specifying a branch will fail
            cfg = distrobaker.load_config(td)
            self.assertIsNone(cfg)
            # try again specifying the branch
            cfg = distrobaker.load_config(td + "#main")

        print("DEBUG loaded config = %s", cfg, file=sys.stderr)
        self.assertIs(type(cfg), dict)
        self.assertIsNotNone(cfg)
        # make sure what was loaded matches get_config()
        self.assertEqual(cfg, distrobaker.get_config())

        # verify some derived values are present in the configuration
        # with the expected values
        self.assertEqual(
            cfg["comps"]["rpms"]["ipa"],
            {
                "source": "freeipa.git#f33",
                "destination": "ipa.git#fluff-42.0.0-alpha",
                "cache": {"source": "freeipa", "destination": "ipa"},
            },
        )
        self.assertEqual(
            cfg["comps"]["modules"]["testmodule:master"],
            {
                "source": "testmodule.git#master",
                "destination": "testmodule#stream-master-fluff-42.0.0-alpha-experimental",
                "cache": {"source": "testmodule", "destination": "testmodule"},
            },
        )

    # test for failure when loading config files that are missing required sections
    # (this is just a randomly selected few of many possibilities)
    @parameterized.expand(
        [
            # (testcase_name, config_file, expected_error)
            (
                "no configuration",
                "distrobaker-no-configuration.yaml",
                "configuration block is missing",
            ),
            (
                "no trigger",
                "distrobaker-no-trigger.yaml",
                "trigger missing",
            ),
            (
                "no source profile",
                "distrobaker-no-source-profile.yaml",
                "source.profile missing",
            ),
        ]
    )
    def test_load_config_missing_section(
        self, testcase_name, config_file, expected_error
    ):
        with tempfile.TemporaryDirectory() as td:
            helpers.setup_test_repo(
                td,
                os.path.join(helpers.DATA_DIR, "config", config_file),
            )
            with self.assertLogs(distrobaker.logger) as cm:
                cfg = distrobaker.load_config(td + "#main")
            self.assertIsNone(cfg)
            # make sure expected_error appears in logger output
            self.assertTrue(
                helpers.strings_with_substring(cm.output, expected_error),
                msg="'{}' not found in logger output: {}".format(
                    expected_error, cm.output
                ),
            )
