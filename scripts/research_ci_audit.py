"""Execute offline pytest and write the research-ci-audit-v1 evidence contract.

This software-only audit never establishes investment research acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "research-ci-audit-v1"
COUNT_FIELDS = ("tests", "failures", "errors", "skipped")


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_junit(path: Path) -> dict[str, int]:
    """Reject missing, empty, malformed or inconsistent pytest JUnit results."""
    root = ET.parse(path).getroot()
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = root.findall("testsuite")
    else:
        raise ValueError("Invalid JUnit root")
    if not suites:
        raise ValueError("No JUnit suites")
    counts = {name: 0 for name in COUNT_FIELDS}
    for suite in suites:
        for name in COUNT_FIELDS:
            count = int(suite.attrib[name])
            if count < 0:
                raise ValueError("Negative JUnit count")
            counts[name] += count
    if counts["tests"] < 1 or sum(counts[key] for key in COUNT_FIELDS[1:]) > counts["tests"]:
        raise ValueError("Empty or inconsistent JUnit results")
    return counts


def git_commit(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("audit-output"))
    parser.add_argument("--engine-id", required=True)
    parser.add_argument("--offline", action="store_true", help="Use pytest-socket to block network access")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()
    if args.timeout_seconds < 1 or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", args.engine_id):
        parser.error("Provide positive timeout and lowercase hyphenated engine-id")

    repo = args.repo_root.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    junit = output / "junit.xml"
    log_file = output / "pytest.log"
    for previous in (junit, log_file):
        previous.unlink(missing_ok=True)

    problems: list[str] = []
    started = time.monotonic()
    exit_code = 1
    pytest_args = [sys.executable, "-m", "pytest", "-q", "tests", f"--junitxml={junit}"]
    if args.offline:
        pytest_args.append("--disable-socket")
    with log_file.open("w", encoding="utf-8") as log:
        try:
            result = subprocess.run(
                pytest_args,
                cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                timeout=args.timeout_seconds, check=False,
            )
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
            problems.append("pytest_timeout")
        except OSError as exc:
            problems.append(f"pytest_launch_error:{type(exc).__name__}")

    try:
        counts = parse_junit(junit)
    except (ET.ParseError, ValueError, TypeError, KeyError, OSError) as exc:
        counts = {field: 0 for field in COUNT_FIELDS}
        problems.append(f"junit_invalid:{type(exc).__name__}")
    if counts["tests"] and counts["skipped"] == counts["tests"]:
        problems.append("all_tests_skipped")

    commit = git_commit(repo)
    if os.getenv("GITHUB_ACTIONS") == "true":
        if not commit:
            problems.append("missing_git_commit")
        if not os.getenv("GITHUB_RUN_ID"):
            problems.append("missing_workflow_run_id")

    status = (
        "PASS"
        if exit_code == 0 and not problems and counts["failures"] == 0 and counts["errors"] == 0
        else "FAIL"
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "classification": "SOFTWARE_TEST_ONLY",
        "research_acceptance": "NOT_ESTABLISHED",
        "independent_review": "NOT_PERFORMED",
        "broker_submission_permitted": False,
        "engine_id": args.engine_id,
        "repository": os.getenv("GITHUB_REPOSITORY") or repo.name,
        "git_commit": commit,
        "workflow_run_id": os.getenv("GITHUB_RUN_ID") or None,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "test_exit_code": exit_code,
        "network_disabled": args.offline,
        "test_counts": counts,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "problems": problems,
        "evidence_sha256": {
            "junit_xml": sha256_file(junit),
            "pytest_log": sha256_file(log_file),
            "runner": sha256_file(Path(__file__)),
            "requirements": sha256_file(repo / "requirements.txt"),
            "requirements_lock": sha256_file(repo / "requirements-lock.txt"),
            "pyproject": sha256_file(repo / "pyproject.toml"),
        },
    }
    manifest = output / "audit_summary.json"
    manifest.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "audit_summary.sha256").write_text(
        f"{sha256_file(manifest)}  audit_summary.json\n", encoding="ascii"
    )
    print(
        f"AUDIT {status} [{args.engine_id}]: {counts['tests']} tests, "
        f"{counts['failures']} failures, {counts['errors']} errors, "
        f"{counts['skipped']} skipped; evidence={manifest}",
        flush=True,
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
