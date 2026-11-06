"""Read-only GitHub Actions artifact collection using authenticated GitHub CLI.

Collector never executes downloaded tests or calls broker functions.
"""
from __future__ import annotations
import json
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable
from .registry import EvidenceError, ingest, verified_bundle

REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
EXPECTED = {"audit_summary.json", "audit_summary.sha256", "junit.xml", "pytest.log"}


def gh_json(endpoint: str, gh_bin: str = "gh") -> dict[str, Any]:
    if not endpoint.startswith("repos/") or ".." in endpoint or "\n" in endpoint:
        raise EvidenceError("Unsafe GitHub endpoint")
    try:
        result = subprocess.run(
            [gh_bin, "api", "--method", "GET", endpoint],
            capture_output=True, text=True, check=False, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceError("GitHub CLI unavailable or API timeout") from exc
    if result.returncode != 0:
        raise EvidenceError("GitHub API query failed; check authentication")
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise EvidenceError("Malformed GitHub API response") from exc
    if not isinstance(response, dict):
        raise EvidenceError("Expected GitHub API object")
    return response


def download_artifact(repo: str, run_id: int, artifact_name: str,
                      path: Path, gh_bin: str = "gh") -> None:
    """Extract named artifact via gh, then assemble a local ZIP for verification.

    Local ZIP hash differs from GitHub's original ZIP. Its *inner* evidence
    is checked against the original summary's SHA-256 references.
    """
    if not REPO.fullmatch(repo) or type(run_id) is not int or run_id <= 0:
        raise EvidenceError("Invalid repository or run ID")
    if not re.fullmatch(r"[a-z0-9-]+-audit-[0-9]+-[0-9]+", artifact_name):
        raise EvidenceError("Invalid audit artifact name")
    with tempfile.TemporaryDirectory(prefix="audit-extract-") as tmp:
        destination = Path(tmp)
        try:
            result = subprocess.run(
                [gh_bin, "run", "download", str(run_id), "--repo", repo,
                 "--name", artifact_name, "--dir", str(destination)],
                capture_output=True, check=False, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EvidenceError("GitHub artifact download unavailable") from exc
        if result.returncode != 0:
            raise EvidenceError("GitHub artifact download failed")
        extracted = sorted(p for p in destination.rglob("*") if p.is_file())
        if len(extracted) != len(EXPECTED) or {p.name for p in extracted} != EXPECTED:
            raise EvidenceError("Downloaded artifact contains missing or unexpected files")
        if any(p.is_symlink() or p.stat().st_size > 12 * 1024 * 1024 for p in extracted):
            raise EvidenceError("Unsafe extracted file")
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for file in extracted:
                bundle.writestr(file.name, file.read_bytes())


def collect(
    registry_dir: Path,
    config: dict[str, dict[str, Any]],
    *,
    per_engine: int = 20,
    gh_bin: str = "gh",
    api: Callable[[str, str], dict[str, Any]] = gh_json,
    download: Callable[[str, int, str, Path, str], None] = download_artifact,
) -> list[dict[str, Any]]:
    """Ingest completed main-branch CI runs after authenticated identity checks."""
    if not 1 <= per_engine <= 100:
        raise EvidenceError("per_engine must be 1-100")
    output: list[dict[str, Any]] = []
    for engine, specification in sorted(config.items()):
        if not specification.get("collector_enabled", False):
            continue
        repo = specification["repository"]
        workflow = specification.get("workflow_file", "research-ci-audit.yml")
        if workflow not in ("research-ci-audit.yml", "ci-audit.yml"):
            raise EvidenceError("Unrecognized workflow name")
        endpoint = (
            f"repos/{repo}/actions/workflows/{workflow}/runs?"
            f"branch=main&status=completed&per_page={per_engine}"
        )
        runs = api(endpoint, gh_bin).get("workflow_runs")
        if not isinstance(runs, list):
            raise EvidenceError("Missing workflow runs list")
        for run in sorted(runs, key=lambda r: (r.get("created_at") or "", r.get("id") or 0)):
            run_id = run.get("id")
            if type(run_id) is int and run_id < specification.get("collector_min_run_id", 0):
                continue
            commit = run.get("head_sha")
            if type(run_id) is not int or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise EvidenceError("Malformed GitHub run identity")
            if run.get("status") != "completed" or run.get("head_branch") != "main":
                raise EvidenceError("Unexpected GitHub run state")
            artifacts = api(f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100", gh_bin).get("artifacts")
            if not isinstance(artifacts, list):
                raise EvidenceError("Missing GitHub artifact list")
            artifact_stem = specification.get("artifact_prefix", f"{engine}-audit")
            expected_prefix = f"{artifact_stem}-{run_id}-"
            matched = [
                a for a in artifacts
                if isinstance(a.get("name"), str)
                and a["name"].startswith(expected_prefix)
                and not a.get("expired", False)
            ]
            if len(matched) != 1:
                raise EvidenceError(f"Missing or ambiguous audit evidence for {engine} run {run_id}")
            artifact = matched[0]
            if type(artifact.get("id")) is not int or artifact["id"] <= 0:
                raise EvidenceError("Invalid artifact metadata")
            with tempfile.TemporaryDirectory(prefix="audit-collect-") as tmp:
                downloaded = Path(tmp) / "evidence.zip"
                download(repo, run_id, artifact["name"], downloaded, gh_bin)
                summary, _, _ = verified_bundle(downloaded, config)
                if (summary["repository"] != repo or
                    summary["workflow_run_id"] != str(run_id) or
                    summary["git_commit"] != commit):
                    raise EvidenceError("GitHub API identity disagrees with downloaded evidence")
                stored = ingest(registry_dir, downloaded, config)
                output.append({"engine_id": engine, "run_id": str(run_id), **stored})
    return output
