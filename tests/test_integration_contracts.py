"""Offline Phase 0 contract tests; no market data, credentials or broker calls."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from integration_contracts import ContractError, verify_bundle


class ContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir()
        self.artifact_path = self.artifact_root / "candidate.csv"
        self.artifact_path.write_bytes(b"symbol,rank\nSPY,1\n")
        self.registry_path = Path(__file__).resolve().parents[1] / "config/engine_registry.json"
        self.envelope_path = self.root / "envelope.json"
        self.manifest_path = self.root / "manifest.json"
        self.envelope = {
            "schema_version": "research-run-envelope-v1",
            "run_id": "scanner-20261116-001",
            "engine_id": "scanner",
            "source_repository": "enriquespinoza/Thinkorswim-Stock-Scanner-Engine",
            "source_commit": "a" * 40,
            "data_as_of_utc": "2026-11-16T20:00:00Z",
            "available_at_utc": "2026-11-16T20:05:00Z",
            "generated_at_utc": "2026-11-16T20:10:00Z",
            "subject": {"kind": "universe", "id": "SP500"},
            "artifact_type": "candidate",
            "horizon": None,
            "manifest_sha256": "",
            "research_acceptance": "NOT_ESTABLISHED",
            "broker_submission_permitted": False,
        }
        self.manifest = {
            "schema_version": "research-artifact-manifest-v1",
            "run_id": self.envelope["run_id"],
            "engine_id": self.envelope["engine_id"],
            "source_repository": self.envelope["source_repository"],
            "source_commit": self.envelope["source_commit"],
            "data_as_of_utc": self.envelope["data_as_of_utc"],
            "available_at_utc": self.envelope["available_at_utc"],
            "generated_at_utc": self.envelope["generated_at_utc"],
            "artifacts": [{
                "path": "candidate.csv",
                "schema_id": "scanner-candidate-handoff-v1",
                "sha256": hashlib.sha256(self.artifact_path.read_bytes()).hexdigest(),
                "byte_length": self.artifact_path.stat().st_size,
            }],
            "upstreams": [],
            "immutable": True,
        }

    def verify(self):
        manifest_bytes = json.dumps(self.manifest, sort_keys=True).encode()
        self.manifest_path.write_bytes(manifest_bytes)
        self.envelope["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        self.envelope_path.write_text(json.dumps(self.envelope), encoding="utf-8")
        return verify_bundle(self.envelope_path, self.manifest_path,
                             self.artifact_root, self.registry_path)

    def rejected(self, part):
        with self.assertRaises(ContractError) as cm:
            self.verify()
        self.assertIn(part, str(cm.exception))

    def test_valid_candidate_bundle(self):
        result = self.verify()
        self.assertEqual(result["status"], "VERIFIED_SOFTWARE_CONTRACT")
        self.assertEqual(result["artifacts_checked"], 1)
        self.assertEqual(result["research_acceptance"], "NOT_ESTABLISHED")
        self.assertFalse(result["broker_submission_permitted"])

    def test_valid_minute_forecast_with_upstream(self):
        self.envelope["engine_id"] = "tmf"
        self.envelope["source_repository"] = "enriquespinoza/TMF-Intraday-Predictive-Analytics"
        self.envelope["subject"] = {"kind": "asset", "id": "TMF"}
        self.envelope["artifact_type"] = "forecast"
        self.envelope["horizon"] = {"value": 60, "unit": "minutes", "calendar": "XNYS"}
        self.manifest.update({key: self.envelope[key] for key in
                              ("engine_id", "source_repository")})
        self.manifest["upstreams"] = [{
            "engine_id": "technical", "run_id": "tae-123",
            "manifest_sha256": "b" * 64, "available_at_utc": "2026-11-16T20:07:00Z",
        }]
        self.assertEqual(self.verify()["engine_id"], "tmf")

    def test_missing_horizon_for_forecast_is_rejected(self):
        self.envelope["artifact_type"] = "forecast"
        self.rejected("forecasts require explicit horizons")

    def test_incorrect_horizon_unit_is_rejected(self):
        self.envelope["horizon"] = {"value": 1, "unit": "days", "calendar": "XNYS"}
        self.rejected("unsupported horizon unit")

    def test_manifest_identity_conflict_is_rejected(self):
        self.manifest["run_id"] = "someone-else"
        self.rejected("manifest/envelope mismatch: run_id")

    def test_unregistered_engine_is_rejected(self):
        self.envelope["engine_id"] = "rogue"
        self.manifest["engine_id"] = "rogue"
        self.rejected("unregistered")

    def test_repository_identity_spoof_is_rejected(self):
        self.envelope["source_repository"] = "enriquespinoza/Trading-Execution-Engine"
        self.manifest["source_repository"] = self.envelope["source_repository"]
        self.rejected("disagrees with registry")

    def test_nonexistent_artifact_is_rejected(self):
        self.artifact_path.unlink()
        self.rejected("absent or symlink")

    def test_tampered_artifact_is_rejected(self):
        self.artifact_path.write_bytes(b"symbol,rank\nSPY,2\n")
        self.rejected("artifact hash mismatch")

    def test_path_traversal_is_rejected(self):
        self.manifest["artifacts"][0]["path"] = "../secret.csv"
        self.rejected("unsafe artifact path")

    def test_duplicate_artifact_is_rejected(self):
        self.manifest["artifacts"].append(copy.deepcopy(self.manifest["artifacts"][0]))
        self.rejected("duplicate artifact path")

    def test_future_upstream_is_rejected(self):
        self.manifest["upstreams"] = [{
            "engine_id": "tmf", "run_id": "new",
            "manifest_sha256": "c" * 64, "available_at_utc": "2026-11-16T20:11:00Z",
        }]
        self.rejected("upstream unavailable")

    def test_cyclic_upstream_is_rejected(self):
        self.manifest["upstreams"] = [{
            "engine_id": "scanner", "run_id": self.envelope["run_id"],
            "manifest_sha256": "c" * 64, "available_at_utc": "2026-11-16T20:08:00Z",
        }]
        self.rejected("cyclic self-upstream")

    def test_new_fields_are_rejected_without_new_version(self):
        self.envelope["silent_fallback"] = True
        self.rejected("missing or unrecognized fields")

    def test_broker_permission_is_rejected(self):
        self.envelope["broker_submission_permitted"] = True
        self.rejected("broker submission prohibited")

    def test_unearned_research_acceptance_is_rejected(self):
        self.envelope["research_acceptance"] = "ACCEPTED"
        self.rejected("cannot certify research")

    def test_offset_timestamp_is_rejected(self):
        self.envelope["generated_at_utc"] = "2026-11-16T15:10:00-05:00"
        self.rejected("UTC Z timestamp required")

    def test_unavailable_data_is_rejected(self):
        self.envelope["available_at_utc"] = "2026-11-16T20:11:00Z"
        self.rejected("time order")

    def test_symlink_artifact_is_rejected(self):
        linked = self.artifact_root / "linked.csv"
        try:
            linked.symlink_to(self.artifact_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        self.manifest["artifacts"][0]["path"] = "linked.csv"
        self.rejected("artifact path contains symlink")

    def test_manifest_byte_tampering_is_rejected(self):
        self.verify()
        self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ContractError, "manifest hash mismatch"):
            verify_bundle(self.envelope_path, self.manifest_path,
                          self.artifact_root, self.registry_path)

    def test_duplicate_json_fields_are_rejected(self):
        self.verify()
        original = self.envelope_path.read_text()
        self.envelope_path.write_text(original.replace(
            '"run_id":', '"run_id": "shadow", "run_id":', 1))
        with self.assertRaisesRegex(ContractError, "duplicate JSON field"):
            verify_bundle(self.envelope_path, self.manifest_path,
                          self.artifact_root, self.registry_path)


if __name__ == "__main__":
    unittest.main()
