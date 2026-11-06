"""Pure contract checks for the shared CI audit reporter."""
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.research_ci_audit import parse_junit, sha256_file


class AuditContractTests(unittest.TestCase):
    def test_valid_suite(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "junit.xml"
            report.write_text('<testsuites><testsuite tests="5" failures="0" errors="0" skipped="1"/></testsuites>')
            self.assertEqual(parse_junit(report), {"tests": 5, "failures": 0, "errors": 0, "skipped": 1})

    def test_invalid_reports_fail_closed(self):
        payloads = [
            "<bad />",
            "<testsuites />",
            "<testsuites><testsuite tests='0' failures='0' errors='0' skipped='0'/></testsuites>",
            "<testsuites><testsuite tests='1' failures='2' errors='0' skipped='0'/></testsuites>",
            "<testsuites><testsuite tests='1' failures='0' errors='0'/></testsuites>",
            "<testsuites><testsuite tests='x' failures='0' errors='0' skipped='0'/></testsuites>",
            "<testsuite tests='-1' failures='0' errors='0' skipped='0' />",
            "<unclosed",
        ]
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "junit.xml"
            for payload in payloads:
                with self.subTest(payload=payload):
                    report.write_text(payload)
                    with self.assertRaises((ValueError, KeyError, TypeError, ET.ParseError)) as context:
                        parse_junit(report)
                    self.assertIsNotNone(context.exception)

    def test_evidence_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence.bin"
            self.assertIsNone(sha256_file(path))
            path.write_bytes(b"test")
            self.assertEqual(
                sha256_file(path),
                "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            )


if __name__ == "__main__":
    unittest.main()
