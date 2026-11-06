# Research CI audit contract (pilot)

The shared Python runner at `scripts/research_ci_audit.py` is the source of truth for the `research-ci-audit-v1` JSON evidence format. Repository workflows check out this repository at an immutable Git commit and call the runner against their own source checkout. No scanner/TMF code or datasets move into the orchestrator.

All test runs are **software-health only**. Even a successful run must state:

- `classification: SOFTWARE_TEST_ONLY`
- `research_acceptance: NOT_ESTABLISHED`
- `independent_review: NOT_PERFORMED`
- `broker_submission_permitted: false`

The runner executes the local pytest test suite, fails closed on process errors, nonzero return codes, malformed/missing/empty/all-skipped JUnit reports, and writes `junit.xml`, `pytest.log`, `audit_summary.json`, `audit_summary.sha256`. The hash of the report demonstrates byte consistency, **not** independently verified market data, signed identity or immutable historical provenance. Logs and artifacts may expose information printed by tests: do not run credentialed/live tests until log redaction and protected evidence storage are implemented. Tests should be fully offline and deterministic.

For local use after installing pytest and existing dependencies:

    python -m pip install pytest-socket
    python scripts/research_ci_audit.py --repo-root . --output-dir audit-output --engine-id scanner --offline

Use `--engine-id tmf-predictive-analytics` for TMF. Missing baseline reference data or provider credentials must not silently cause a PASS in a test that purports to verify those resources: keep such research gates separately marked NOT_ESTABLISHED.

The workflow and runner are versioned. Repository CI must pin an exact orchestrator commit when checking out the shared runner; upgrading it requires an explicit reviewable PR. GitHub Actions artifacts expire, so later work must add independent write-protected storage, checks for missing scheduled runs, and a registry of test-policy versions and historical audit provenance. No auto-merge and no live brokerage authorization.
