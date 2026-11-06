# Central research audit registry — V1

This orchestrator module receives **software-test evidence**, not market-data attestations, broker orders, or confirmed investment signals.

## Capabilities

The registry verifies four audit artifact members (`junit.xml`, `pytest.log`, `audit_summary.json`, `audit_summary.sha256`), SHA-256 checksums, JUnit counts and test exit status, exact engine/repository allowlist, source commit, run ID, and the mandatory software-only research boundary. Invalid and conflicting bundles are rejected. Legitimate **FAIL** bundles are retained so software regressions and later recoveries are not lost.

Evidence is indexed in an append-only SQLite table and ZIPs are stored under hash-addressed local folders. Reimporting the same run is idempotent. A reporting cursor produces only changed findings: new engines, PASS-to-FAIL regressions, FAIL-to-PASS recoveries, increases or decreases in errors, failures, skipped tests, and overall test count. Merely changing a commit is not an alert.

## Installation and operation

Python 3.11+; built with standard-library modules. From the repository root:

```bash
python -m unittest discover -s tests -v

python -m audit_registry --registry-dir ./local-audit-registry \
  ingest /path/to/stock-scanner-evidence.zip /path/to/tmf-evidence.zip

python -m audit_registry --registry-dir ./local-audit-registry \
  report --markdown ./local-audit-reports/changes.md \
  --json ./local-audit-reports/changes.json --acknowledge
```

`--acknowledge` advances the cursor after writing the report. Omit it to preview findings. The registry retains old events regardless of cursor position.

## Optional authenticated collection

Install GitHub CLI (`gh`) and configure read access to Actions artifacts. After the respective audit workflows have been **merged onto main** and collection is approved, set `collector_enabled=true` in `config/audit-engines.json` for each engine. All engines default to disabled.

```bash
python -m audit_registry --registry-dir ./local-audit-registry collect --per-engine 20
```

The collector queries completed main-branch workflow runs through the authenticated GitHub API, downloads named artifacts via `gh run download`, verifies artifact run ID and source SHA against GitHub metadata, checks their internal checksums, and appends them to the registry. Artifacts are re-zipped locally after download, so local ZIP hashes are **not** identical to GitHub's original ZIP hashes. The contents are rechecked. Older runs beyond the selected fetch window are not inspected and need backfill.

The collector is **not yet scheduled**. It must execute on a persistent, authenticated machine before unattended operation is possible. A missed-run detector exists but `monitor_enabled` defaults to false; turn it on only after the collector operates reliably and workflows have reached default branches. Missing evidence means that no qualifying report was ingested, not proof that GitHub did not run.

## Limitations

- SQLite triggers prevent ordinary audit event UPDATE/DELETE, but a filesystem administrator can tamper with a local registry. Store it outside the Git repository, restrict access, and back it up to write-protected storage for durable retention.
- External `requirements`, runner and code hashes are recorded but not independently established merely by parsing the ZIP. The external provider and signed evidence still require review.
- No GitHub APIs are invoked by local ingestion; optional collection requires a separately approved `gh` authentication context.
- CI checks run on synthetic fixtures, not live market data or predictive-performance claims. Research acceptance remains **NOT_ESTABLISHED**, independent review **NOT_PERFORMED**, broker submission **false**.
- GitHub-hosted evidence expires on a retention schedule, and the registry does not imply continuous AI agent execution.

## Execution Engine compatibility boundary (2026-11-06)

The first Execution Engine `main` CI artifacts used the prefix `offline-audit-...`, did not record the engine identifier, and did not enforce network blocking. **They are not valid `research-ci-audit-v1` registry inputs**. Historical evidence is not rewritten or silently promoted.

The Execution Engine migration PR switches CI to the same pinned shared runner used by Scanner and TMF, enforces pytest-socket isolation and publishes `trading-execution-audit-...`. The central registry's `collector_min_run_id=42314496329` for trading-execution skips known pre-contract main runs, including `42314496328`. Earlier artifact ZIPs remain accessible via GitHub until retention expiry but are excluded from registry-supported evidence. An explicit optional `artifact_prefix` setting supports already-validated alternate naming conventions; it does not waive required evidence fields or bypass identity verification.

Collector and schedule monitors remain disabled pending execution CI validation and provisioning of a persistent read-only collection host and protected backup storage.
