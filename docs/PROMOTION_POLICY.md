# Model promotion policy

Promotion requires **all** applicable gates, not merely passing unit tests.

- Registered hypothesis, target, horizon, benchmark, and preregistered acceptance criteria.
- Reproducible data provenance, licensing/adjustment policy, and frozen train/validation/test split.
- Unit, security, data-quality, contract and regression checks.
- Leakage-controlled out-of-sample / walk-forward tests with uncertainty and multiple-testing considerations.
- Forward predictions recorded before outcomes, with sufficient matured samples.
- Calibration and proper scoring rules against naive and market benchmarks.
- Paper environment validation of order lifecycle, partial fills, rejects, costs, reconciliation, and kill switches where applicable.
- Independent review and sign-off with immutable git SHA, data fingerprint and model artifact hash.

Statuses: experimental, historical_validated, forward_validating, paper_validating, approved_frozen, rejected, retired.

No automatic promotion. Thresholds and sample sizes must be specified **before** evaluation, per model and market horizon. Never claim that paper trading guarantees live profitability.
