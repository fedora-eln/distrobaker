# SPDX-License-Identifier: MIT

import helpers
import logging
import sys
import tempfile


from io import StringIO

try:
    import unittest2 as unittest
except ImportError:
    import unittest

try:
    from unittest.mock import patch
except ImportError:
    from mock import patch


class TestConfigRef(unittest.TestCase):
    def setUp(self):
        # configure logging
        logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s")
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        self.logger.debug("logging has been configured")

        # import main distrobaker script as a module
        self.dbmain = helpers.import_path("distrobaker")

        # create a temporary directoy to use as a git repo
        self.git_repo_dirobj = tempfile.TemporaryDirectory()
        self.git_repo_dir = self.git_repo_dirobj.name
        self.logger.debug("git repo dir = %s" % self.git_repo_dir)

    def tearDown(self):
        self.git_repo_dirobj.cleanup()
        pass

    def test_get_config_ref(self):
        helpers.setup_test_repo(self.git_repo_dir)

        last_commit = helpers.last_commit(self.git_repo_dir)
        self.logger.debug("git last commit = %s" % last_commit)
        self.assertRegex(last_commit.decode(), helpers.GIT_HASH_REGEX)

        config_ref = self.dbmain.get_config_ref(
            self.git_repo_dir + "#main", self.logger
        )
        self.logger.debug("config ref = %s" % config_ref)
        self.assertRegex(config_ref.decode(), helpers.GIT_HASH_REGEX)

        self.assertEqual(config_ref, last_commit)

        config_ref = self.dbmain.get_config_ref(
            self.git_repo_dir + "#doesnotexist", self.logger
        )
        self.logger.debug("config ref = %s" % config_ref)
        self.assertEqual(config_ref, None)


class TestConsole(unittest.TestCase):
    def setUp(self):
        # import main distrobaker script as a module
        self.dbmain = helpers.import_path("distrobaker")

    @patch("sys.stdout", new=StringIO())
    @patch("sys.argv", ["distrobaker", "-h"])
    def test_main_help(self):
        with self.assertRaises(SystemExit) as cm:
            self.dbmain.main()
        self.assertEqual(cm.exception.code, 0)
        output = sys.stdout.getvalue()
        self.assertIn("show this help message and exit", output)
