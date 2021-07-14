# SPDX-License-Identifier: MIT

import distrobaker
import helpers
import logging
import os
import subprocess
import tempfile
import time

from parameterized import parameterized


try:
    import unittest2 as unittest
except ImportError:
    import unittest


def _run_cmds(cmds):
    cwd = None
    for cmd in cmds:
        if cmd[0] == "cd":
            cwd = cmd[1]
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out, err = proc.communicate()


def _setup_repo(dirname, marker=None, base=None):
    clone_dirobj = tempfile.TemporaryDirectory()
    clone_dir = clone_dirobj.name

    # setup a simple bare repo, optionally containing a README and a marker file
    if base:
        cmds = (
            ["cd", "/tmp"],
            ["git", "clone", "--bare", base, dirname],
        )
        _run_cmds(cmds)
    else:
        cmds = (
            ["cd", "/tmp"],
            ["git", "init", "--bare", dirname],
            ["rm", "-rf", clone_dir],
            ["git", "clone", dirname, clone_dir],
            ["cd", clone_dir],
            ["git", "config", "user.name", "John Doe"],
            ["git", "config", "user.email", "jdoe@example.com"],
            ["git", "add", "."],
            ["git", "commit", "--allow-empty", "-m", "Initial commit"],
            ["git", "push"],
        )
        _run_cmds(cmds)

    if marker:
        cmds = (
            ["cd", "/tmp"],
            ["rm", "-rf", clone_dir],
            ["git", "clone", dirname, clone_dir],
            ["cd", clone_dir],
            ["git", "config", "user.name", "John Doe"],
            ["git", "config", "user.email", "jdoe@example.com"],
            ["git", "rm", "-rf", "--ignore-unmatch", "."],
            ["bash", "-c", "echo %s > README" % marker],
            ["touch", marker],
            ["git", "add", "."],
            ["git", "commit", "-m", "Marker commit"],
            ["git", "push"],
        )
        _run_cmds(cmds)


def _repo_log(repodir, branch="HEAD"):
    cmd = ["git", "log", "--pretty=oneline", "--no-decorate", branch]
    proc = subprocess.Popen(
        cmd, cwd=repodir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, err = proc.communicate()
    return out.decode("utf-8")


def _repo_get_current_branch(repodir):
    cmd = ["git", "branch", "--show-current"]
    proc = subprocess.Popen(
        cmd, cwd=repodir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, err = proc.communicate()
    return out.decode("utf-8").rstrip()


def _repo_checkout_branch(repodir, branch):
    cmds = (
        ["cd", repodir],
        ["git", "checkout", branch],
    )
    _run_cmds(cmds)


def _repo_get_config_option(repodir, opt_name):
    cmd = ["git", "config", "--local", opt_name]
    proc = subprocess.Popen(
        cmd, cwd=repodir, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, err = proc.communicate()
    return out.decode("utf-8").rstrip()


class TestRepos(unittest.TestCase):
    def _setUp(self, common_history=False):
        # be sure to do no harm
        distrobaker.dry_run = True

        # create and setup a set of temporary git repos
        self.scm_repotree_dirobj = tempfile.TemporaryDirectory()
        distrobaker.logger.debug(
            "scm repotree dir = %s" % self.scm_repotree_dirobj.name
        )

        dst_repo = os.path.join(
            self.scm_repotree_dirobj.name, "destination", "rpms", "foo.git"
        )
        src_repo = os.path.join(
            self.scm_repotree_dirobj.name, "source", "rpms", "foo.git"
        )
        _setup_repo(
            dst_repo,
            base=None,
            marker="destination",
        )
        if common_history:
            baserepo = dst_repo
        else:
            baserepo = None
            # Delay a second to avoid a race condition that can result in
            # both repos ending up with the same initial commit hash which
            # can cause unexpected results by making them appear to have
            # common history!
            time.sleep(1)
        _setup_repo(
            src_repo,
            base=baserepo,
            marker="source",
        )

        # do a brute force set up of a minimal configuration
        distrobaker.c = {
            "main": {
                "destination": {
                    "scm": os.path.join(
                        self.scm_repotree_dirobj.name, "destination"
                    ),
                },
                "source": {
                    "scm": os.path.join(
                        self.scm_repotree_dirobj.name, "source"
                    ),
                },
                "git": {
                    "author": "Tux Penguin",
                    "email": "tux@example.com",
                    "message": "Commit message",
                },
            },
        }

        self.ns = "rpms"
        self.comp = "foo"
        cbld = "%(component)s.git" % {"component": self.comp}
        csrc = "%(component)s.git" % {"component": self.comp}
        cdst = "%(component)s.git" % {"component": self.comp}
        self.bscm = distrobaker.split_scmurl(
            "{}/{}/{}".format("https://dummy.scm", self.ns, cbld)
        )
        self.bscm["ref"] = self.bscm["ref"] if self.bscm["ref"] else "master"
        self.sscm = distrobaker.split_scmurl(
            "{}/{}/{}".format(
                distrobaker.c["main"]["source"]["scm"], self.ns, csrc
            )
        )
        self.dscm = distrobaker.split_scmurl(
            "{}/{}/{}".format(
                distrobaker.c["main"]["destination"]["scm"], self.ns, cdst
            )
        )
        self.dscm["ref"] = self.dscm["ref"] if self.dscm["ref"] else "master"

    def tearDown(self):
        self.scm_repotree_dirobj.cleanup()

    def test_repo_setup(self):
        self._setUp()

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = distrobaker.clone_destination_repo(
                self.ns, self.comp, self.dscm, tmpdir
            )
            self.assertIsNotNone(repo)
            self.assertEqual(tmpdir, repo.working_dir)
            # make sure the expected files are present in the clone
            self.assertCountEqual(
                os.listdir(tmpdir), [".git", "README", "destination"]
            )
            readme = open(os.path.join(tmpdir, "README")).read().rstrip()
            self.assertEqual(readme, "destination")
            # make sure the git log of the clone is identical to the original
            self.assertEqual(
                _repo_log(self.dscm["link"], self.dscm["ref"]),
                _repo_log(tmpdir),
            )
            dest_cur_branch = _repo_get_current_branch(tmpdir)

            self.assertIsNotNone(
                distrobaker.fetch_upstream_repo(
                    self.ns, self.comp, self.sscm, repo
                )
            )
            _repo_checkout_branch(tmpdir, "source/master")
            # make sure the expected files are present in the repo
            self.assertCountEqual(
                os.listdir(tmpdir), [".git", "README", "source"]
            )
            readme = open(os.path.join(tmpdir, "README")).read().rstrip()
            self.assertEqual(readme, "source")
            # make sure the git log of the repo is identical to the original
            self.assertEqual(
                _repo_log(self.sscm["link"], "master"), _repo_log(tmpdir)
            )

            _repo_checkout_branch(tmpdir, dest_cur_branch)

            # check repo option values are currently unset
            self.assertEqual(
                _repo_get_config_option(tmpdir, "user.name"),
                "",
            )
            self.assertEqual(
                _repo_get_config_option(tmpdir, "user.email"),
                "",
            )

            # configure repo and check option values are set correctly
            self.assertIsNotNone(
                distrobaker.configure_repo(self.ns, self.comp, repo)
            )
            self.assertEqual(
                _repo_get_config_option(tmpdir, "user.name"),
                "Tux Penguin",
            )
            self.assertEqual(
                _repo_get_config_option(tmpdir, "user.email"),
                "tux@example.com",
            )

    @parameterized.expand(
        [
            # (testcase_name, common_history)
            ("common", True),
            ("unrelated", False),
        ]
    )
    def test_repo_merge(self, testcase_name, common_history):
        self._setUp(common_history)

        with tempfile.TemporaryDirectory() as tmpdir:
            # prep repo for merge; testing of these methods is done by test_repo_setup()
            repo = distrobaker.clone_destination_repo(
                self.ns, self.comp, self.dscm, tmpdir
            )
            distrobaker.fetch_upstream_repo(
                self.ns, self.comp, self.sscm, repo
            )
            distrobaker.configure_repo(self.ns, self.comp, repo)

            self.assertIsNotNone(
                distrobaker.sync_repo_merge(
                    self.ns, self.comp, repo, self.bscm, self.sscm, self.dscm
                )
            )

            # make sure the expected files are present in the repo
            self.assertCountEqual(
                os.listdir(tmpdir), [".git", "README", "source"]
            )
            readme = open(os.path.join(tmpdir, "README")).read().rstrip()
            self.assertEqual(readme, "source")

    def test_repo_pull_common(self):
        self._setUp(common_history=True)

        with tempfile.TemporaryDirectory() as tmpdir:
            # prep repo for merge; testing of these methods is done by test_repo_setup()
            repo = distrobaker.clone_destination_repo(
                self.ns, self.comp, self.dscm, tmpdir
            )
            distrobaker.fetch_upstream_repo(
                self.ns, self.comp, self.sscm, repo
            )
            distrobaker.configure_repo(self.ns, self.comp, repo)

            self.assertIsNotNone(
                distrobaker.sync_repo_pull(
                    self.ns,
                    self.comp,
                    repo,
                    self.bscm,
                )
            )

    def test_repo_pull_unrelated(self):
        self._setUp(common_history=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            # prep repo for merge; testing of these methods is done by test_repo_setup()
            repo = distrobaker.clone_destination_repo(
                self.ns, self.comp, self.dscm, tmpdir
            )
            distrobaker.fetch_upstream_repo(
                self.ns, self.comp, self.sscm, repo
            )
            distrobaker.configure_repo(self.ns, self.comp, repo)

            # pull should fail due to repos having unrelated histories
            with self.assertLogs(
                distrobaker.logger, level=logging.DEBUG
            ) as cm:
                distrobaker.sync_repo_pull(
                    self.ns,
                    self.comp,
                    repo,
                    self.bscm,
                )
            expected_error = "refusing to merge unrelated histories"
            self.assertTrue(
                helpers.strings_with_substring(cm.output, expected_error),
                msg="'{}' not found in logger output: {}".format(
                    expected_error, cm.output
                ),
            )

    def test_repo_push(self):
        self._setUp(common_history=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            # prep repo for merge; testing of these methods is done by test_repo_setup()
            repo = distrobaker.clone_destination_repo(
                self.ns, self.comp, self.dscm, tmpdir
            )
            distrobaker.fetch_upstream_repo(
                self.ns, self.comp, self.sscm, repo
            )
            distrobaker.configure_repo(self.ns, self.comp, repo)

            distrobaker.sync_repo_merge(
                self.ns, self.comp, repo, self.bscm, self.sscm, self.dscm
            )

            # make sure the git log of the destination is not yet the same as the merged directory
            self.assertNotEqual(
                _repo_log(tmpdir),
                _repo_log(self.dscm["link"], self.dscm["ref"]),
            )

            # we have to temporarily disable dry-run mode or the push never happens
            distrobaker.dry_run = False
            self.assertIsNotNone(
                distrobaker.repo_push(self.ns, self.comp, repo, self.dscm)
            )
            # back to doing no harm
            distrobaker.dry_run = True

            # make sure the git log of the destination is now the same as the merged directory
            self.assertEqual(
                _repo_log(tmpdir),
                _repo_log(self.dscm["link"], self.dscm["ref"]),
            )
