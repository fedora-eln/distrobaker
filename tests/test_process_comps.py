# SPDX-License-Identifier: MIT

# WORK IN PROGRESS - NOT YET FUNCTIONAL!

import distrobaker
import helpers
import logging
import mock
import os
import sys
import tempfile


try:
    import unittest2 as unittest
except ImportError:
    import unittest


def load_test_config(config_file):
    with tempfile.TemporaryDirectory() as td:
        helpers.setup_test_repo(
            td, os.path.join(helpers.DATA_DIR, "config", config_file)
        )
        cfg = distrobaker.load_config(td + "#main")
    print("\nDEBUG loaded config = %s" % cfg, file=sys.stderr)
    return cfg


class KojiSessionMock:
    def __init__(self):
        # placeholder for initialization
        pass

    def listTagged(self, tag, latest=False):
        print(
            "\nDEBUG KojiSessionMock.listTagged called with tag %s." % tag,
            file=sys.stderr,
        )
        tagged_builds = {
            "rawhide": [
                {
                    "id": 1,
                    "package_name": "foo",
                    "version": "rawhide",
                    "release": "1.1",
                },
                {
                    "id": 2,
                    "package_name": "bar",
                    "version": "rawhide",
                    "release": "2.1",
                },
            ],
            "rawhide-modular": [
                {
                    "id": 3,
                    "package_name": "baz",
                    "version": "stable",
                    "release": "20210324.c0ffee43",
                    "nvr": "baz-stable-20210324.c0ffee43",
                },
                {
                    "id": 4,
                    "package_name": "qux",
                    "version": "rolling",
                    "release": "20210324.deadbeef",
                    "nvr": "qux-rolling-20210324.deadbeef",
                },
            ],
        }

        return tagged_builds[tag] if tag in tagged_builds else []

    def getBuild(self, nvr):
        print(
            "\nDEBUG KojiSessionMock.getBuild called with nvr %s." % nvr,
            file=sys.stderr,
        )

        build_info = {
            "baz-stable-20210324.c0ffee43": {
                "source": "https://src.example.com/modules/baz.git?#00000000",
                "extra": {
                    "typeinfo": {
                        "module": {
                            "name": "baz",
                            "stream": "stable",
                            "modulemd_str": "placeholder",
                        }
                    },
                },
            },
            "qux-rolling-20210324.deadbeef": {
                "source": "https://src.example.com/modules/qux.git?#00000000",
                "extra": {
                    "typeinfo": {
                        "module": {
                            "name": "qux",
                            "stream": "rolling",
                            "modulemd_str": "placeholder",
                        }
                    },
                },
            },
        }

        return build_info[nvr] if nvr in build_info else None


class TestProcessComponents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # be sure to do no harm
        distrobaker.dry_run = True
        # load our distrobaker configuration
        load_test_config("distrobaker-process_components.yaml")

    def setUp(self):
        self.assertTrue(distrobaker.dry_run)
        # make sure configuration was loaded
        self.assertIsNotNone(distrobaker.get_config())

    def test_not_a_set(self):
        with self.assertLogs(distrobaker.logger, level=logging.DEBUG) as cm:
            distrobaker.process_components(None)
        expected_error = "must be passed a set"
        self.assertTrue(
            helpers.strings_with_substring(cm.output, expected_error),
            msg="'{}' not found in logger output: {}".format(
                expected_error, cm.output
            ),
        )

        with self.assertLogs(distrobaker.logger, level=logging.DEBUG) as cm:
            distrobaker.process_components(list())
        expected_error = "must be passed a set"
        self.assertTrue(
            helpers.strings_with_substring(cm.output, expected_error),
            msg="'{}' not found in logger output: {}".format(
                expected_error, cm.output
            ),
        )

        with self.assertLogs(distrobaker.logger, level=logging.DEBUG) as cm:
            distrobaker.process_components(dict())
        expected_error = "must be passed a set"
        self.assertTrue(
            helpers.strings_with_substring(cm.output, expected_error),
            msg="'{}' not found in logger output: {}".format(
                expected_error, cm.output
            ),
        )

    @mock.patch("distrobaker.get_buildsys")
    @mock.patch("distrobaker.build_comp")
    @mock.patch("distrobaker.sync_repo")
    def test_empty_set(
        self,
        mock_sync_repo,
        mock_build_comp,
        mock_get_buildsys,
    ):
        mock_get_buildsys.return_value = KojiSessionMock()
        mock_sync_repo.return_value = (
            "https://src.example.com/dummy.git?#dummyref"
        )

        with self.assertLogs(distrobaker.logger, level=logging.DEBUG) as cm:
            distrobaker.process_components(set())
        print(
            "\nDEBUG log messages from call to process_components: %s"
            % cm.output,
            file=sys.stderr,
        )
        expected_message = "Synchronized 4 component(s), 0 skipped."
        self.assertTrue(
            helpers.strings_with_substring(cm.output, expected_message),
            msg="'{}' not found in logger output: {}".format(
                expected_message, cm.output
            ),
        )

        self.assertEqual(mock_sync_repo.call_count, 4)
        mock_sync_repo.assert_has_calls(
            [
                mock.call(comp="foo", ns="rpms", nvr=None),
                mock.call(comp="bar", ns="rpms", nvr=None),
                mock.call(
                    comp="baz:stable",
                    ns="modules",
                    nvr="baz-stable-20210324.c0ffee43",
                ),
                mock.call(
                    comp="qux:rolling",
                    ns="modules",
                    nvr="qux-rolling-20210324.deadbeef",
                ),
            ],
            any_order=True,
        )

        self.assertEqual(mock_build_comp.call_count, 4)
        mock_build_comp.assert_has_calls(
            [
                mock.call(comp="foo", ref="dummyref", ns="rpms"),
                mock.call(comp="bar", ref="dummyref", ns="rpms"),
                mock.call(comp="baz:stable", ref="dummyref", ns="modules"),
                mock.call(comp="qux:rolling", ref="dummyref", ns="modules"),
            ],
            any_order=True,
        )

    @mock.patch("distrobaker.get_buildsys")
    @mock.patch("distrobaker.build_comp")
    @mock.patch("distrobaker.sync_repo")
    def test_specific_comps(
        self,
        mock_sync_repo,
        mock_build_comp,
        mock_get_buildsys,
    ):
        mock_get_buildsys.return_value = KojiSessionMock()
        mock_sync_repo.return_value = (
            "https://src.example.com/dummy.git?#dummyref"
        )

        with self.assertLogs(distrobaker.logger, level=logging.DEBUG) as cm:
            distrobaker.process_components({"rpms/bar", "modules/qux:rolling"})
        print(
            "\nDEBUG log messages from call to process_components: %s"
            % cm.output,
            file=sys.stderr,
        )
        expected_message = "Synchronized 2 component(s), 0 skipped."
        self.assertTrue(
            helpers.strings_with_substring(cm.output, expected_message),
            msg="'{}' not found in logger output: {}".format(
                expected_message, cm.output
            ),
        )

        self.assertEqual(mock_sync_repo.call_count, 2)
        mock_sync_repo.assert_has_calls(
            [
                mock.call(comp="bar", ns="rpms", nvr=None),
                mock.call(
                    comp="qux:rolling",
                    ns="modules",
                    nvr="qux-rolling-20210324.deadbeef",
                ),
            ],
            any_order=True,
        )

        self.assertEqual(mock_build_comp.call_count, 2)
        mock_build_comp.assert_has_calls(
            [
                mock.call(comp="bar", ref="dummyref", ns="rpms"),
                mock.call(comp="qux:rolling", ref="dummyref", ns="modules"),
            ],
            any_order=True,
        )

    @mock.patch("distrobaker.get_buildsys")
    @mock.patch("distrobaker.build_comp")
    @mock.patch("distrobaker.sync_repo")
    def test_alternate_comps(
        self,
        mock_sync_repo,
        mock_build_comp,
        mock_get_buildsys,
    ):
        mock_get_buildsys.return_value = KojiSessionMock()
        mock_sync_repo.return_value = (
            "https://src.example.com/dummy.git?#dummyref"
        )

        with self.assertLogs(distrobaker.logger, level=logging.DEBUG) as cm:
            distrobaker.process_components(
                {"rpms/fred", "modules/waldo:hidden"}
            )
        print(
            "\nDEBUG log messages from call to process_components: %s"
            % cm.output,
            file=sys.stderr,
        )
        expected_message = "Synchronized 2 component(s), 0 skipped."
        self.assertTrue(
            helpers.strings_with_substring(cm.output, expected_message),
            msg="'{}' not found in logger output: {}".format(
                expected_message, cm.output
            ),
        )

        self.assertEqual(mock_sync_repo.call_count, 2)
        mock_sync_repo.assert_has_calls(
            [
                mock.call(comp="fred", ns="rpms", nvr=None),
                mock.call(
                    comp="waldo:hidden",
                    ns="modules",
                    nvr=None,
                ),
            ],
            any_order=True,
        )

        self.assertEqual(mock_build_comp.call_count, 2)
        mock_build_comp.assert_has_calls(
            [
                mock.call(comp="fred", ref="dummyref", ns="rpms"),
                mock.call(comp="waldo:hidden", ref="dummyref", ns="modules"),
            ],
            any_order=True,
        )
