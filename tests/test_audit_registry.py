"""Offline acceptance tests: checksums, append-only records, regressions, and collection."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from audit_registry.registry import (
    EvidenceError, acknowledge, build_report, ingest, load_config,
    open_registry, parse_junit, verified_bundle,
)
from audit_registry.collect import collect


def digest(value):
    return hashlib.sha256(value).hexdigest()


class RegistryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.database = self.root / "store"
        self.cfg = {
            "schema_version": "orchestrator-audit-registry-v1",
            "engines": [
                {"engine_id": "stock-scanner", "repository": "example/Scanner",
                 "monitor_enabled": False, "collector_enabled": False,
                 "workflow_file": "research-ci-audit.yml"},
                {"engine_id": "tmf-predictive-analytics", "repository": "example/TMF",
                 "monitor_enabled": False, "collector_enabled": False,
                 "workflow_file": "research-ci-audit.yml"},
            ],
        }
        self.conf = self.root / "engines.json"
        self.save_config()

    def save_config(self):
        self.conf.write_text(json.dumps(self.cfg), encoding="utf-8")
        self.config = load_config(self.conf)

    def artifact(self, run="100", engine="stock-scanner", result="PASS", errors=0,
                 skipped=0, modifications=None, corrupt=None):
        xml = (f'<testsuites><testsuite tests="4" errors="{errors}" '
               f'failures="0" skipped="{skipped}"/></testsuites>').encode()
        log = b"tests executed\n"
        summary = {
            "schema_version": "research-ci-audit-v1",
            "generated_at_utc": "2026-11-06T23:30:00+00:00",
            "engine_id": engine,
            "repository": self.config[engine]["repository"],
            "workflow_run_id": run,
            "git_commit": "a" * 40,
            "status": result,
            "test_exit_code": 0 if result == "PASS" else 1,
            "test_counts": {"tests": 4, "errors": errors, "failures": 0, "skipped": skipped},
            "problems": [],
            "classification": "SOFTWARE_TEST_ONLY",
            "research_acceptance": "NOT_ESTABLISHED",
            "independent_review": "NOT_PERFORMED",
            "broker_submission_permitted": False,
            "network_disabled": True,
            "evidence_sha256": {
                "junit_xml": digest(xml), "pytest_log": digest(log),
                "runner": "f" * 64, "requirements": None,
            },
        }
        summary.update(modifications or {})
        body = (json.dumps(summary, sort_keys=True) + "\n").encode()
        sidecar = f"{digest(body)}  audit_summary.json\n".encode()
        contents = {
            "junit.xml": xml,
            "pytest.log": log,
            "audit_summary.json": body,
            "audit_summary.sha256": sidecar,
        }
        if corrupt:
            contents[corrupt] = b"tampered"
        path = self.root / f"{engine}-{run}-{len(list(self.root.glob('*.zip')))}.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in contents.items():
                archive.writestr(name, content)
        return path

    def test_ingest_and_idempotence(self):
        path = self.artifact()
        self.assertEqual(ingest(self.database, path, self.config)["result"], "imported")
        self.assertEqual(ingest(self.database, path, self.config)["result"], "duplicate")

    def test_same_run_different_evidence_rejected(self):
        ingest(self.database, self.artifact(), self.config)
        changed = self.artifact(modifications={"git_commit": "b" * 40})
        with self.assertRaisesRegex(EvidenceError, "Conflicting evidence"):
            ingest(self.database, changed, self.config)

    def test_corrupted_checksum_rejected(self):
        with self.assertRaisesRegex(EvidenceError, "hash mismatch"):
            verified_bundle(self.artifact(corrupt="pytest.log"), self.config)

    def test_corrupt_summary_sidecar_rejected(self):
        with self.assertRaisesRegex(EvidenceError, "sidecar"):
            verified_bundle(self.artifact(corrupt="audit_summary.sha256"), self.config)

    def test_impossible_green_result_rejected(self):
        for change in [
            {"test_exit_code": 2},
            {"problems": ["timeout"]},
            {"test_counts": {"tests": 4, "errors": 1, "failures": 0, "skipped": 0}},
        ]:
            with self.subTest(change=change), self.assertRaises(EvidenceError):
                verified_bundle(self.artifact(modifications=change), self.config)

    def test_broker_and_research_statuses_cannot_escalate(self):
        for change in [
            {"broker_submission_permitted": True},
            {"research_acceptance": "ESTABLISHED"},
            {"network_disabled": False},
            {"repository": "other/Repo"},
        ]:
            with self.subTest(change=change), self.assertRaises(EvidenceError):
                verified_bundle(self.artifact(modifications=change), self.config)

    def test_reject_unexpected_members(self):
        path = self.artifact()
        extra = self.root / "extraneous.zip"
        with zipfile.ZipFile(path) as old, zipfile.ZipFile(extra, "w") as new:
            for file in old.namelist():
                new.writestr(file, old.read(file))
            new.writestr("../outside", b"oops")
        with self.assertRaises(EvidenceError):
            verified_bundle(extra, self.config)

    def test_append_only_db_trigger(self):
        ingest(self.database, self.artifact(), self.config)
        with open_registry(self.database) as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE audit_events SET status='FAIL' WHERE event_id=1")
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM audit_events WHERE event_id=1")

    def test_recovery_and_cursor_silence(self):
        ingest(self.database, self.artifact(run="100", result="FAIL", errors=1), self.config)
        ingest(self.database, self.artifact(run="101", result="PASS"), self.config)
        with open_registry(self.database) as conn:
            first = build_report(conn, self.config, datetime(2026, 11, 7, tzinfo=timezone.utc))
            self.assertEqual(first["events_seen"], 2)
            self.assertEqual(first["changed_findings"][-1]["change_codes"][0], "SOFTWARE_RECOVERY")
            acknowledge(conn, "default", first)
            next_report = build_report(conn, self.config, datetime(2026, 11, 7, tzinfo=timezone.utc))
            self.assertEqual(next_report["events_seen"], 0)
            self.assertEqual(next_report["changed_findings"], [])

    def test_unchanged_pass_on_new_commit_is_quiet(self):
        ingest(self.database, self.artifact(run="100"), self.config)
        with open_registry(self.database) as conn:
            first = build_report(conn, self.config, datetime.now(timezone.utc))
            acknowledge(conn, "default", first)
        ingest(self.database, self.artifact(run="101", modifications={"git_commit": "b" * 40}), self.config)
        with open_registry(self.database) as conn:
            later = build_report(conn, self.config, datetime.now(timezone.utc))
            self.assertEqual(later["events_seen"], 1)
            self.assertEqual(later["changed_findings"], [])

    def test_collector_disabled_by_default(self):
        def never(*args):
            raise AssertionError("Collector must not contact GitHub without authorization")
        self.assertEqual(collect(self.database, self.config, api=never), [])

    def test_collector_checks_authenticated_commit(self):
        self.cfg["engines"][0]["collector_enabled"] = True
        self.save_config()
        source = self.artifact()
        def api(endpoint, _):
            if endpoint.endswith("runs?branch=main&status=completed&per_page=20"):
                return {"workflow_runs": [{
                    "id": 100, "head_sha": "b" * 40,
                    "status": "completed", "head_branch": "main", "created_at": "2026-11-06T23:00:00Z",
                }]}
            return {"artifacts": [{
                "id": 1, "name": "stock-scanner-audit-100-1", "expired": False,
            }]}
        def download(repo, run_id, name, path, gh):
            path.write_bytes(source.read_bytes())
        with self.assertRaisesRegex(EvidenceError, "identity"):
            collect(self.database, self.config, api=api, download=download)

    def test_collector_imports_matching_run(self):
        self.cfg["engines"][0]["collector_enabled"] = True
        self.save_config()
        source = self.artifact()
        def api(endpoint, _):
            if endpoint.endswith("runs?branch=main&status=completed&per_page=20"):
                return {"workflow_runs": [{
                    "id": 100, "head_sha": "a" * 40,
                    "status": "completed", "head_branch": "main", "created_at": "2026-11-06T23:00:00Z",
                }]}
            return {"artifacts": [{
                "id": 1, "name": "stock-scanner-audit-100-1", "expired": False,
            }]}
        def download(repo, run_id, name, path, gh):
            path.write_bytes(source.read_bytes())
        self.assertEqual(collect(self.database, self.config, api=api, download=download)[0]["result"], "imported")
        self.assertEqual(collect(self.database, self.config, api=api, download=download)[0]["result"], "duplicate")

    def test_collector_skips_explicit_precontract_runs(self):
        self.cfg["engines"][0]["collector_enabled"] = True
        self.cfg["engines"][0]["collector_min_run_id"] = 101
        self.save_config()
        requests = []

        def api(endpoint, _):
            requests.append(endpoint)
            if "/runs?branch=main" in endpoint:
                return {"workflow_runs": [{
                    "id": 100, "head_sha": "a" * 40,
                    "status": "completed", "head_branch": "main",
                    "created_at": "2026-11-06T23:00:00Z",
                }]}
            raise AssertionError("Legacy artifacts must not be queried")

        self.assertEqual(collect(self.database, self.config, api=api), [])
        self.assertEqual(len(requests), 1)

    def test_reject_invalid_collection_boundary_or_alias(self):
        for key, value in [
            ("collector_min_run_id", -1),
            ("collector_min_run_id", True),
            ("artifact_prefix", "../unsafe"),
        ]:
            with self.subTest(key=key, value=value):
                self.cfg["engines"][0][key] = value
                with self.assertRaises(EvidenceError):
                    self.save_config()
                del self.cfg["engines"][0][key]

    def test_collector_supports_valid_legacy_artifact_name(self):
        self.cfg["engines"][0]["collector_enabled"] = True
        self.cfg["engines"][0]["artifact_prefix"] = "offline-audit"
        self.save_config()
        source = self.artifact()

        def api(endpoint, _):
            if "/runs?branch=main" in endpoint:
                return {"workflow_runs": [{
                    "id": 100, "head_sha": "a" * 40,
                    "status": "completed", "head_branch": "main",
                    "created_at": "2026-11-06T23:00:00Z",
                }]}
            return {"artifacts": [{
                "id": 9, "name": "offline-audit-100-1",
                "expired": False,
            }]}

        def download(repo, run_id, name, path, gh):
            self.assertEqual(name, "offline-audit-100-1")
            path.write_bytes(source.read_bytes())

        result = collect(self.database, self.config, api=api, download=download)
        self.assertEqual(result[0]["result"], "imported")

    def test_malformed_junit(self):
        with self.assertRaises(EvidenceError):
            parse_junit(b"<broken")


if __name__ == "__main__":
    unittest.main()
