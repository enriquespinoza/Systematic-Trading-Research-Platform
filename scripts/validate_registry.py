"""Fail-closed validation for the research-engine registry (stdlib only)."""
import json
from pathlib import Path

ALLOWED = {"experimental", "historical_validated", "forward_validating", "paper_validating", "approved_frozen", "rejected", "retired"}
ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "config" / "engine_registry.json").read_text(encoding="utf-8"))
assert data["schema_version"] == "1.0.0"
assert data["policy"] == "research_only_no_live_orders"
engines = data["engines"]
assert isinstance(engines, list) and engines
ids = [e["id"] for e in engines]
assert len(ids) == len(set(ids)), "Duplicate engine IDs"
for e in engines:
    assert e["repository"].startswith("enriquespinoza/"), e
    assert e["lifecycle"] in ALLOWED, e
    assert e["integration"] in {"not_verified", "verified"}, e
    assert e["paper_validation"] in {"not_verified", "verified"}, e
    if e["lifecycle"] == "approved_frozen":
        assert e["integration"] == "verified" and e["paper_validation"] == "verified", e
        assert isinstance(e["approved_commit"], str) and len(e["approved_commit"]) == 40, e
        assert e["approved_release"], e
    else:
        assert e["approved_release"] is None and e["approved_commit"] is None, e
print(f"Registry valid: {len(engines)} engines; no unapproved releases")
