# Orchestrator architecture

The platform is the **control plane**. Every research engine remains an independent repository and experiment. This repository coordinates immutable inputs, validated artifacts, dependency versions, integration tests, audit summaries, and release decisions; it does not copy predictive algorithms.

## Interfaces
1. Each engine publishes an artifact manifest with engine id, git SHA, data snapshot fingerprint, generated timestamp (UTC), schema version, and test evidence.
2. The orchestrator validates manifests and contracts before accepting artifacts.
3. Only separately approved, pinned engine versions can be used in a system release.
4. No engine is authorized to place live orders through the orchestrator. Paper mode requires a separate explicit approval gate and broker safeguards.

## Lifecycle
hypothesis -> development -> historical test -> walk-forward -> forward observation -> broker paper validation -> independent review -> approved frozen release.

A failed gate returns the engine to experimental status. Model performance must be measured on outcomes that mature after the forecast, without look-ahead or survivorship leakage.

## Versioning
Git submodules may pin approved source commits later. Until integration contracts are verified, the registry contains repository references only, not claimed dependencies or approvals. CI should fetch only approved immutable SHAs, with least-privilege tokens and no live broker secrets.

## Evidence
Each run should report observation counts, newest observation age, matured forecasts, Brier/log loss or CRPS when applicable, benchmark comparisons, calibration, risk metrics, and exceptions. An increase in data volume alone does not establish predictive advantage.

## Phase 0 integration contract

The standardized, research-only run envelope and immutable artifact manifest are specified in [Integration Contracts v1](INTEGRATION_CONTRACTS_V1.md). `integration_contracts` verifies actual manifest/artifact byte hashes, producer identity against the eight-engine registry, timestamp ordering, horizon units and fail-closed safety fields. These checks establish software evidence consistency only. Source authenticity, domain-specific model validation, pinned cross-repository adapters, research-data accumulation and real paper execution remain separate future gates.

