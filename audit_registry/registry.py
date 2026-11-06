"""Local append-only audit registry and changes-only report engine.

All evidence is software-health evidence, not independent research acceptance.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

EXPECTED_FILES = frozenset({"audit_summary.json", "audit_summary.sha256", "junit.xml", "pytest.log"})
SCHEMA = "research-ci-audit-v1"
REGISTRY_SCHEMA = "orchestrator-audit-registry-v1"
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_ARCHIVE_BYTES = 18 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
ENGINE_ID = re.compile(r"[a-z][a-z0-9-]{1,63}\Z")
RUN_ID = re.compile(r"[0-9]{1,20}\Z")


class EvidenceError(ValueError):
    """Input was not a qualified, internally consistent software test bundle."""


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError("Timestamp must be ISO 8601 text")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("Invalid ISO 8601 timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise EvidenceError("Timestamp must contain timezone")
    return dt.astimezone(timezone.utc)


def load_config(path: Path) -> dict[str, dict[str, Any]]:
    content = json.loads(path.read_text(encoding="utf-8"))
    if content.get("schema_version") != REGISTRY_SCHEMA:
        raise EvidenceError("Unsupported engine registry schema")
    result: dict[str, dict[str, Any]] = {}
    for item in content.get("engines", []):
        engine = item.get("engine_id")
        repository = item.get("repository")
        if not isinstance(engine, str) or not ENGINE_ID.fullmatch(engine) or engine in result:
            raise EvidenceError("Invalid or duplicated engine ID")
        if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise EvidenceError("Invalid repository mapping")
        if "artifact_prefix" in item:
            prefix = item["artifact_prefix"]
            if not isinstance(prefix, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-audit", prefix):
                raise EvidenceError("Invalid audit artifact prefix")
        min_run_id = item.get("collector_min_run_id", 0)
        if type(min_run_id) is not int or min_run_id < 0:
            raise EvidenceError("collector_min_run_id must be a nonnegative integer")
        if type(item.get("monitor_enabled")) is not bool:
            raise EvidenceError("monitor_enabled must be boolean")
        if "collector_enabled" in item and type(item["collector_enabled"]) is not bool:
            raise EvidenceError("collector_enabled must be boolean")
        if item["monitor_enabled"]:
            schedule = item.get("schedule_hhmm_utc")
            weekdays = item.get("weekdays")
            if not isinstance(schedule, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", schedule):
                raise EvidenceError("Invalid expected audit schedule")
            if not isinstance(weekdays, list) or not weekdays or any(type(d) is not int or not 0 <= d <= 6 for d in weekdays):
                raise EvidenceError("Invalid weekdays")
            if type(item.get("grace_minutes")) is not int or not 0 <= item["grace_minutes"] <= 1440:
                raise EvidenceError("Invalid grace period")
            utc_time(item["active_from_utc"])
        result[engine] = item
    if not result:
        raise EvidenceError("No registered engines")
    return result


def parse_junit(data: bytes) -> dict[str, int]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EvidenceError("Malformed JUnit XML") from exc
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite") if root.tag == "testsuites" else []
    if not suites:
        raise EvidenceError("No JUnit suites")
    counts = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    try:
        for suite in suites:
            for field in counts:
                n = int(suite.attrib[field])
                if n < 0:
                    raise EvidenceError("Negative JUnit count")
                counts[field] += n
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError("Invalid JUnit counts") from exc
    if counts["tests"] < 1 or counts["failures"] + counts["errors"] + counts["skipped"] > counts["tests"]:
        raise EvidenceError("Inconsistent JUnit counts")
    return counts


def verified_bundle(path: Path, config: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], str, str]:
    """Verify exact bundle bytes and software gate. Does NOT attest GH provenance."""
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise EvidenceError("Archive exceeds size limit")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if set(names) != EXPECTED_FILES or len(names) != len(EXPECTED_FILES):
                raise EvidenceError("Missing, duplicate or unexpected archive members")
            for item in entries:
                if item.file_size > MAX_FILE_BYTES or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16):
                    raise EvidenceError("Unsafe or oversized archive member")
            bundle = {name: archive.read(name) for name in EXPECTED_FILES}
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
        raise EvidenceError("Invalid ZIP artifact") from exc
    summary_bytes = bundle["audit_summary.json"]
    digest = sha(summary_bytes)
    if bundle["audit_summary.sha256"] != f"{digest}  audit_summary.json\n".encode("ascii"):
        raise EvidenceError("Summary SHA-256 sidecar mismatch")
    try:
        summary = json.loads(summary_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise EvidenceError("Malformed JSON summary") from exc
    if not isinstance(summary, dict) or summary.get("schema_version") != SCHEMA:
        raise EvidenceError("Unexpected audit schema")
    engine = summary.get("engine_id")
    if not isinstance(engine, str) or engine not in config or summary.get("repository") != config[engine]["repository"]:
        raise EvidenceError("Engine/repository mismatch or unknown engine")
    if not isinstance(summary.get("git_commit"), str) or not HEX40.fullmatch(summary["git_commit"]):
        raise EvidenceError("Missing commit identity")
    if not isinstance(summary.get("workflow_run_id"), str) or not RUN_ID.fullmatch(summary["workflow_run_id"]):
        raise EvidenceError("Missing valid workflow run ID")
    utc_time(summary.get("generated_at_utc"))
    if summary.get("classification") != "SOFTWARE_TEST_ONLY":
        raise EvidenceError("Unexpected research classification")
    if summary.get("research_acceptance") != "NOT_ESTABLISHED" or summary.get("independent_review") != "NOT_PERFORMED":
        raise EvidenceError("Software evidence claims unauthorized research acceptance")
    if summary.get("broker_submission_permitted") is not False or summary.get("network_disabled") is not True:
        raise EvidenceError("Broker/network safety boundary not met")
    if summary.get("status") not in ("PASS", "FAIL"):
        raise EvidenceError("Invalid test status")
    actual = parse_junit(bundle["junit.xml"])
    reported = summary.get("test_counts")
    if not isinstance(reported, dict) or any(type(reported.get(k)) is not int or reported[k] != actual[k] for k in actual):
        raise EvidenceError("JUnit counts disagree with summary")
    evidence_hashes = summary.get("evidence_sha256")
    if not isinstance(evidence_hashes, dict):
        raise EvidenceError("Missing evidence hashes")
    for field, filename in (("junit_xml", "junit.xml"), ("pytest_log", "pytest.log")):
        if evidence_hashes.get(field) != sha(bundle[filename]):
            raise EvidenceError(f"{field} hash mismatch")
    for value in evidence_hashes.values():
        if value is not None and (not isinstance(value, str) or not HEX64.fullmatch(value)):
            raise EvidenceError("Malformed external evidence hash")
    if type(summary.get("test_exit_code")) is not int:
        raise EvidenceError("Missing pytest exit code")
    if not isinstance(summary.get("problems"), list) or not all(isinstance(problem, str) for problem in summary["problems"]):
        raise EvidenceError("Malformed problems list")
    if summary["status"] == "PASS":
        if summary["test_exit_code"] != 0 or actual["errors"] or actual["failures"] or actual["skipped"] == actual["tests"] or summary["problems"]:
            raise EvidenceError("Contradictory PASS result")
    else:
        if summary["test_exit_code"] == 0 and actual["errors"] == actual["failures"] == 0 and not summary["problems"]:
            raise EvidenceError("Contradictory FAIL result")
    return summary, digest, sha(path.read_bytes())


def open_registry(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(root / "registry.sqlite3", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS audit_events (
      event_id INTEGER PRIMARY KEY AUTOINCREMENT,
      engine_id TEXT NOT NULL,
      repository TEXT NOT NULL,
      workflow_run_id TEXT NOT NULL,
      summary_sha256 TEXT NOT NULL,
      archive_sha256 TEXT NOT NULL,
      generated_at_utc TEXT NOT NULL,
      ingested_at_utc TEXT NOT NULL,
      status TEXT NOT NULL,
      summary_json TEXT NOT NULL,
      UNIQUE(repository, workflow_run_id)
    );
    CREATE TRIGGER IF NOT EXISTS prohibit_audit_event_update BEFORE UPDATE ON audit_events
      BEGIN SELECT RAISE(ABORT, 'append-only audit events'); END;
    CREATE TRIGGER IF NOT EXISTS prohibit_audit_event_delete BEFORE DELETE ON audit_events
      BEGIN SELECT RAISE(ABORT, 'append-only audit events'); END;
    CREATE TABLE IF NOT EXISTS report_cursors (
      report_name TEXT PRIMARY KEY,
      last_event_id INTEGER NOT NULL DEFAULT 0,
      freshness_json TEXT NOT NULL DEFAULT '{}'
    );
    """)
    return conn


def ingest(root: Path, path: Path, config: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summary, summary_hash, archive_hash = verified_bundle(path, config)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage_dir = root / "blobs" / "sha256" / archive_hash[:2]
    storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive_target = storage_dir / (archive_hash + ".zip")
    if not archive_target.exists():
        fd, tempname = tempfile.mkstemp(prefix=".ingest-", dir=storage_dir)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(path.read_bytes())
                file.flush()
                os.fsync(file.fileno())
            os.chmod(tempname, 0o600)
            os.replace(tempname, archive_target)
        finally:
            if os.path.exists(tempname):
                os.unlink(tempname)
    elif sha(archive_target.read_bytes()) != archive_hash:
        raise EvidenceError("Stored evidence blob has been tampered with")
    conn = open_registry(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT event_id, summary_sha256 FROM audit_events WHERE repository=? AND workflow_run_id=?",
            (summary["repository"], summary["workflow_run_id"]),
        ).fetchone()
        if previous:
            if previous["summary_sha256"] != summary_hash:
                raise EvidenceError("Conflicting evidence for an existing workflow run")
            conn.rollback()
            return {"result": "duplicate", "event_id": previous["event_id"], "engine_id": summary["engine_id"]}
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""INSERT INTO audit_events (
            engine_id, repository, workflow_run_id, summary_sha256, archive_sha256,
            generated_at_utc, ingested_at_utc, status, summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            summary["engine_id"], summary["repository"], summary["workflow_run_id"], summary_hash,
            archive_hash, summary["generated_at_utc"], now, summary["status"], canonical(summary),
        ))
        conn.commit()
        return {"result": "imported", "event_id": cursor.lastrowid, "engine_id": summary["engine_id"], "status": summary["status"]}
    finally:
        conn.close()


def classify(previous: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    if previous is None:
        return ["ENGINE_ONBOARDED"]
    changes = []
    old, new = previous, current
    if old["status"] == "PASS" and new["status"] == "FAIL":
        changes.append("SOFTWARE_REGRESSION")
    elif old["status"] == "FAIL" and new["status"] == "PASS":
        changes.append("SOFTWARE_RECOVERY")
    for key in ("failures", "errors", "skipped"):
        a, b = old["test_counts"][key], new["test_counts"][key]
        if b > a:
            changes.append(f"{key.upper()}_INCREASED")
        elif b < a:
            changes.append(f"{key.upper()}_DECREASED")
    new_problems = set(new["problems"]) - set(old["problems"])
    if new_problems:
        changes.append("NEW_RUNNER_PROBLEMS")
    if old["test_counts"]["tests"] != new["test_counts"]["tests"]:
        changes.append("TEST_COUNT_CHANGED")
    return changes


def freshness(config: dict[str, dict[str, Any]], events: list[dict[str, Any]], now: datetime) -> dict[str, str]:
    """Detect missing INGESTED scheduled evidence; not proof that Actions never ran."""
    output = {}
    for engine, spec in config.items():
        if not spec["monitor_enabled"]:
            continue
        start = utc_time(spec["active_from_utc"])
        hh, mm = (int(part) for part in spec["schedule_hhmm_utc"].split(":"))
        day = now.date()
        expected = None
        for _ in range(9):
            candidate = datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc)
            if candidate >= start and candidate.weekday() in spec["weekdays"] and candidate + timedelta(minutes=spec["grace_minutes"]) <= now:
                expected = candidate
                break
            day -= timedelta(days=1)
        if expected is None:
            continue
        # Warn only when no report captured in the session window; may also mean the collector failed.
        found = any(
            ev["engine_id"] == engine
            and expected - timedelta(minutes=60) <= utc_time(ev["generated_at_utc"]) < expected + timedelta(days=1)
            for ev in events
        )
        if not found:
            output[engine] = "MISSING_INGESTED_EVIDENCE:" + expected.isoformat()
    return output


def build_report(conn: sqlite3.Connection, config: dict[str, dict[str, Any]], now: datetime, name: str = "default") -> dict[str, Any]:
    row = conn.execute("SELECT last_event_id, freshness_json FROM report_cursors WHERE report_name=?", (name,)).fetchone()
    cursor = int(row["last_event_id"]) if row else 0
    prior_freshness = json.loads(row["freshness_json"]) if row else {}
    rows = conn.execute("SELECT * FROM audit_events ORDER BY event_id ASC").fetchall()
    events = [dict(r) for r in rows]
    last: dict[str, dict[str, Any]] = {}
    changes: list[dict[str, Any]] = []
    for row in events:
        summary = json.loads(row["summary_json"])
        engine = row["engine_id"]
        old = last.get(engine)
        if row["event_id"] > cursor:
            codes = classify(old, summary)
            if codes:
                changes.append({
                    "event_id": row["event_id"],
                    "engine_id": engine,
                    "run_id": row["workflow_run_id"],
                    "git_commit": summary["git_commit"],
                    "from_status": old["status"] if old else None,
                    "to_status": summary["status"],
                    "test_counts": summary["test_counts"],
                    "change_codes": codes,
                })
        last[engine] = summary
    fresh = freshness(config, events, now)
    for engine, warning in sorted(fresh.items()):
        if prior_freshness.get(engine) != warning:
            changes.append({"engine_id": engine, "change_codes": ["MISSING_INGESTED_EVIDENCE"], "detail": warning})
    for engine in sorted(set(prior_freshness) - set(fresh)):
        changes.append({"engine_id": engine, "change_codes": ["INGESTION_FRESHNESS_RECOVERED"]})
    return {
        "schema_version": REGISTRY_SCHEMA,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "cursor_before": cursor,
        "cursor_after": events[-1]["event_id"] if events else cursor,
        "events_seen": len([r for r in events if r["event_id"] > cursor]),
        "research_acceptance": "NOT_ESTABLISHED",
        "broker_submission_permitted": False,
        "changed_findings": changes,
        "freshness_state": fresh,
        "latest": {
            engine: {
                "status": s["status"], "test_counts": s["test_counts"],
                "workflow_run_id": s["workflow_run_id"], "generated_at_utc": s["generated_at_utc"],
            }
            for engine, s in sorted(last.items())
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    changes = report["changed_findings"]
    out = ["# Research software audit — changed findings", "",
           f"New evidence ingested: {report['events_seen']} run(s); changed findings: {len(changes)}.", "",
           "All results are software-health evidence only. Research acceptance remains NOT_ESTABLISHED; broker submission is prohibited.", ""]
    if not changes:
        out.append("No changed findings since the last acknowledged report.")
    for change in changes:
        codes = ", ".join(change["change_codes"])
        run = f" (workflow run {change['run_id']})" if "run_id" in change else ""
        out.append(f"- **{change['engine_id']}**{run}: {codes}.")
    return "\n".join(out) + "\n"


def acknowledge(conn: sqlite3.Connection, name: str, report: dict[str, Any]) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("""INSERT INTO report_cursors(report_name, last_event_id, freshness_json)
      VALUES (?, ?, ?)
      ON CONFLICT(report_name) DO UPDATE SET
        last_event_id=excluded.last_event_id, freshness_json=excluded.freshness_json""",
        (name, report["cursor_after"], canonical(report["freshness_state"])))
    conn.commit()
