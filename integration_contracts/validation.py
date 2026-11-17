"""Fail-closed validation of a producer envelope and immutable artifact manifest.

Only Python's standard library is used. This validates interoperability and
recorded bytes; it does not certify data-provider accuracy or forecast skill.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath

ENVELOPE_VERSION = "research-run-envelope-v1"
MANIFEST_VERSION = "research-artifact-manifest-v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SCHEMA_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}\Z")
TYPES = {"candidate", "descriptive", "forecast", "allocation", "execution", "review", "audit"}
SUBJECTS = {"asset", "universe", "portfolio", "system"}
HORIZON_UNITS = {"minutes", "trading_sessions", "calendar_days"}
ENVELOPE_FIELDS = {
    "schema_version", "run_id", "engine_id", "source_repository", "source_commit",
    "data_as_of_utc", "available_at_utc", "generated_at_utc", "subject",
    "artifact_type", "horizon", "manifest_sha256", "research_acceptance",
    "broker_submission_permitted",
}
MANIFEST_FIELDS = {
    "schema_version", "run_id", "engine_id", "source_repository", "source_commit",
    "data_as_of_utc", "available_at_utc", "generated_at_utc",
    "artifacts", "upstreams", "immutable",
}


class ContractError(ValueError):
    """Evidence is missing, ambiguous, incompatible, or changed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def fields(value: object, required: set[str], where: str) -> dict:
    require(isinstance(value, dict), f"{where}: expected object")
    require(set(value) == required, f"{where}: missing or unrecognized fields")
    return value


def identifier(value: object, where: str) -> None:
    require(isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)), f"{where}: invalid identifier")


def sha256(value: object, where: str) -> None:
    require(isinstance(value, str) and bool(SHA256.fullmatch(value)), f"{where}: invalid SHA-256")


def utc(value: object, where: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"{where}: UTC Z timestamp required")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{where}: invalid timestamp") from exc
    require(moment.utcoffset().total_seconds() == 0, f"{where}: must be UTC")
    return moment


def load_json(path: Path) -> tuple[dict, bytes]:
    def unique_pairs(pairs: list[tuple[str, object]]) -> dict:
        obj = {}
        for key, value in pairs:
            require(key not in obj, f"duplicate JSON field: {key}")
            obj[key] = value
        return obj
    try:
        data = path.read_bytes()
        value = json.loads(data, object_pairs_hook=unique_pairs,
                           parse_constant=lambda x: (_ for _ in ()).throw(
                               ContractError(f"nonfinite JSON number: {x}")))
    except ContractError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise ContractError(f"unreadable JSON: {path.name}") from exc
    require(isinstance(value, dict), f"{path.name}: expected JSON object")
    return value, data


def validate_registry(registry: dict, engine_id: str, repository: str) -> None:
    require(registry.get("schema_version") == "1.0.0", "unsupported engine registry")
    require(registry.get("policy") == "research_only_no_live_orders", "unsafe engine registry policy")
    engines = registry.get("engines")
    require(isinstance(engines, list), "registry engines must be a list")
    matches = [e for e in engines if isinstance(e, dict) and e.get("id") == engine_id]
    require(len(matches) == 1, "unregistered or duplicate producer engine")
    require(matches[0].get("repository") == repository, "producer repository disagrees with registry")


def validate_envelope(envelope: dict) -> None:
    fields(envelope, ENVELOPE_FIELDS, "envelope")
    require(envelope["schema_version"] == ENVELOPE_VERSION, "unsupported envelope version")
    identifier(envelope["run_id"], "run_id")
    identifier(envelope["engine_id"], "engine_id")
    require(isinstance(envelope["source_repository"], str) and
            envelope["source_repository"].startswith("enriquespinoza/") and
            len(envelope["source_repository"].split("/")) == 2 and
            bool(envelope["source_repository"].split("/")[1]), "invalid repository")
    require(isinstance(envelope["source_commit"], str) and
            bool(COMMIT_SHA.fullmatch(envelope["source_commit"])), "invalid source commit")
    sha256(envelope["manifest_sha256"], "manifest hash")
    as_of = utc(envelope["data_as_of_utc"], "data_as_of_utc")
    available = utc(envelope["available_at_utc"], "available_at_utc")
    generated = utc(envelope["generated_at_utc"], "generated_at_utc")
    require(as_of <= available <= generated, "time order: as_of <= available <= generated")
    subject = fields(envelope["subject"], {"kind", "id"}, "subject")
    require(isinstance(subject["kind"], str) and subject["kind"] in SUBJECTS, "invalid subject kind")
    identifier(subject["id"], "subject id")
    require(isinstance(envelope["artifact_type"], str) and
            envelope["artifact_type"] in TYPES, "invalid artifact type")
    horizon = envelope["horizon"]
    if horizon is None:
        require(envelope["artifact_type"] != "forecast", "forecasts require explicit horizons")
    else:
        horizon = fields(horizon, {"value", "unit", "calendar"}, "horizon")
        require(type(horizon["value"]) is int and horizon["value"] > 0, "horizon value must be positive integer")
        require(isinstance(horizon["unit"], str) and horizon["unit"] in HORIZON_UNITS,
                "unsupported horizon unit")
        identifier(horizon["calendar"], "horizon calendar")
    require(envelope["research_acceptance"] == "NOT_ESTABLISHED", "Phase 0 cannot certify research")
    require(envelope["broker_submission_permitted"] is False, "broker submission prohibited")


def safe_artifact(root: Path, name: object) -> Path:
    require(isinstance(name, str) and name and "\\" not in name, "invalid artifact path")
    p = PurePosixPath(name)
    require(not p.is_absolute() and all(x not in {".", ".."} for x in name.split("/")),
            "unsafe artifact path")
    require(not any(not segment for segment in name.split("/")), "empty artifact path segment")
    target = root.joinpath(*p.parts)
    require(target.resolve().is_relative_to(root.resolve()), "artifact escapes root")
    parent = root
    for segment in p.parts:
        parent = parent / segment
        require(not parent.is_symlink(), "artifact path contains symlink")
    require(target.is_file() and not target.is_symlink(), "artifact absent or symlink")
    return target


def validate_manifest(manifest: dict, envelope: dict, root: Path) -> int:
    fields(manifest, MANIFEST_FIELDS, "manifest")
    require(manifest["schema_version"] == MANIFEST_VERSION, "unsupported manifest version")
    for key in ("run_id", "engine_id", "source_repository", "source_commit",
                "data_as_of_utc", "available_at_utc", "generated_at_utc"):
        require(manifest[key] == envelope[key], f"manifest/envelope mismatch: {key}")
    require(manifest["immutable"] is True, "manifest must declare immutable evidence")
    artifacts = manifest["artifacts"]
    require(isinstance(artifacts, list) and artifacts, "at least one artifact is required")
    require(isinstance(manifest["upstreams"], list), "upstreams must be an array")
    seen_paths = set()
    for item in artifacts:
        fields(item, {"path", "schema_id", "sha256", "byte_length"}, "artifact")
        require(item["path"] not in seen_paths, "duplicate artifact path")
        seen_paths.add(item["path"])
        require(isinstance(item["schema_id"], str) and bool(SCHEMA_ID.fullmatch(item["schema_id"])),
                "invalid artifact schema id")
        sha256(item["sha256"], "artifact hash")
        require(type(item["byte_length"]) is int and item["byte_length"] >= 0, "invalid byte length")
        data = safe_artifact(root, item["path"]).read_bytes()
        require(len(data) == item["byte_length"], "artifact byte length mismatch")
        require(hashlib.sha256(data).hexdigest() == item["sha256"], "artifact hash mismatch")
    seen_upstreams = set()
    for upstream in manifest["upstreams"]:
        fields(upstream, {"engine_id", "run_id", "manifest_sha256", "available_at_utc"}, "upstream")
        identifier(upstream["engine_id"], "upstream engine")
        identifier(upstream["run_id"], "upstream run")
        sha256(upstream["manifest_sha256"], "upstream manifest hash")
        require(utc(upstream["available_at_utc"], "upstream availability") <=
                utc(envelope["generated_at_utc"], "generation time"), "upstream unavailable at run time")
        key = (upstream["engine_id"], upstream["run_id"])
        require(key != (envelope["engine_id"], envelope["run_id"]), "cyclic self-upstream")
        require(key not in seen_upstreams, "duplicate upstream")
        seen_upstreams.add(key)
    return len(artifacts)


def verify_bundle(envelope_path: Path, manifest_path: Path,
                  artifact_root: Path, registry_path: Path) -> dict:
    """Verify original manifest and artifact bytes against a registered producer."""
    envelope, _ = load_json(Path(envelope_path))
    manifest, manifest_bytes = load_json(Path(manifest_path))
    registry, _ = load_json(Path(registry_path))
    validate_envelope(envelope)
    require(hashlib.sha256(manifest_bytes).hexdigest() == envelope["manifest_sha256"],
            "manifest hash mismatch")
    validate_registry(registry, envelope["engine_id"], envelope["source_repository"])
    count = validate_manifest(manifest, envelope, Path(artifact_root))
    return {"status": "VERIFIED_SOFTWARE_CONTRACT", "engine_id": envelope["engine_id"],
            "run_id": envelope["run_id"], "artifacts_checked": count,
            "research_acceptance": "NOT_ESTABLISHED", "broker_submission_permitted": False}
