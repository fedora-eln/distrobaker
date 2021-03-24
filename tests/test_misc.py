# SPDX-License-Identifier: MIT

import distrobaker
import logging

try:
    import unittest2 as unittest
except ImportError:
    import unittest


class TestMiscSettings(unittest.TestCase):
    def test_loglevel(self):
        self.assertIsNotNone(distrobaker.loglevel())
        self.assertEqual(distrobaker.loglevel(logging.INFO), logging.INFO)
        self.assertEqual(distrobaker.loglevel(), logging.INFO)
        self.assertEqual(distrobaker.loglevel(logging.DEBUG), logging.DEBUG)
        self.assertEqual(distrobaker.loglevel(), logging.DEBUG)

    def test_retries(self):
        self.assertIsNotNone(distrobaker.retries())
        self.assertEqual(distrobaker.retries(2), 2)
        self.assertEqual(distrobaker.retries(), 2)
        self.assertEqual(distrobaker.retries(3), 3)
        self.assertEqual(distrobaker.retries(), 3)

    def test_pretend(self):
        self.assertIsNotNone(distrobaker.pretend())
        self.assertEqual(distrobaker.pretend(True), True)
        self.assertEqual(distrobaker.pretend(), True)
        self.assertEqual(distrobaker.pretend(False), False)
        self.assertEqual(distrobaker.pretend(), False)


class TestMiscParsing(unittest.TestCase):
    def test_split_scmurl(self):
        self.assertDictEqual(
            distrobaker.split_scmurl(""),
            {"link": "", "ref": None, "ns": None, "comp": ""},
        )
        self.assertDictEqual(
            distrobaker.split_scmurl(
                "https://example.com/distrobaker.git#prod"
            ),
            {
                "link": "https://example.com/distrobaker.git",
                "ref": "prod",
                "ns": "example.com",
                "comp": "distrobaker.git",
            },
        )
        self.assertDictEqual(
            distrobaker.split_scmurl("conf"),
            {"link": "conf", "ref": None, "ns": None, "comp": "conf"},
        )
        self.assertDictEqual(
            distrobaker.split_scmurl("/tmp/conf#testbranch"),
            {
                "link": "/tmp/conf",
                "ref": "testbranch",
                "ns": "tmp",
                "comp": "conf",
            },
        )
        self.assertDictEqual(
            distrobaker.split_scmurl(
                "https://src.fedoraproject.org/rpms/gzip.git#rawhide"
            ),
            {
                "link": "https://src.fedoraproject.org/rpms/gzip.git",
                "ref": "rawhide",
                "ns": "rpms",
                "comp": "gzip.git",
            },
        )

    def test_split_module(self):
        self.assertDictEqual(
            distrobaker.split_module(""), {"name": "", "stream": "master"}
        )
        self.assertDictEqual(
            distrobaker.split_module(":"), {"name": "", "stream": "master"}
        )
        self.assertDictEqual(
            distrobaker.split_module("name"),
            {"name": "name", "stream": "master"},
        )
        self.assertDictEqual(
            distrobaker.split_module("name:stream"),
            {"name": "name", "stream": "stream"},
        )
        self.assertDictEqual(
            distrobaker.split_module(":stream"),
            {"name": "", "stream": "stream"},
        )
        self.assertDictEqual(
            distrobaker.split_module("name:stream:version:context"),
            {"name": "name", "stream": "stream"},
        )
