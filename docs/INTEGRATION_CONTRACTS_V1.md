# Phase 0 — Integration contracts and governance

**Status:** Initial research-only software interoperability gate. No
cross-repository deployment, independent predictive validation, paper-trading
approval, or live-order authorization is implied.

## Roles and scope

- **Orchestrator:** schema versions, registry identities, provenance checks,
  run coordination, immutable audit history, version pins, integration tests,
  and model-promotion control.
- **Research engines:** independently own their datasets, source code, models,
  feature timing, domain artifacts and test evidence.
- **Consumers:** validate domain-specific meanings beyond these shared fields.
- **Decision Review:** independent advisory assessment of historical decisions
  and matured outcomes, never automatic promotion or order placement.
- **Enterprise IT Homelab:** optional infrastructure; not a market-signal source.
- **US Retail Sales Dashboard:** excluded from this integration program.

The engine registry now includes Decision Review as the eighth experimental
engine. All integration and paper-validation states remain unverified; no
engine is assigned an approved release or source commit.

## Versioned contracts

- `schemas/research_run_envelope_v1.schema.json`
- `schemas/research_artifact_manifest_v1.schema.json`
- `integration_contracts/validation.py` — stdlib-only semantic and byte validator
- `tests/test_integration_contracts.py` — synthetic offline acceptance and rejection tests

The **envelope** identifies the producer engine and repository, 40-character
Git commit, stable run ID, subject (asset, universe, portfolio, or system),
artifact type, immutable manifest SHA-256 and explicit UTC time axes.
Forecasts must include a horizon with a positive integer value, unit
(`minutes`, `trading_sessions`, or `calendar_days`) and named calendar.
Descriptive records and scanner candidates may specify `null` horizon.

The **manifest** repeats immutable run identity, declares source artifacts with
relative paths, versioned domain schema IDs, SHA-256 digests and original byte
lengths, and records upstream run IDs and availability times.
The orchestrator validates the raw manifest bytes against its envelope SHA-256,
each artifact's original bytes, file safety, and registered producer identity.

Time axes:

1. `data_as_of_utc` — latest observation represented by the source.
2. `available_at_utc` — earliest verifiable information availability.
3. `generated_at_utc` — time the producer created the run.

All must end in UTC `Z` and satisfy as-of <= available <= generated.
Upstream availability cannot follow the dependent run's generation time.
Consumers still need source-specific lag, exchange calendar, forecast-origin
and target-maturity checks. Same-day rows are not automatically point-in-time
compatible.

### Example research-only envelope

This is **synthetic** and contains a placeholder digest, not real evidence:

```json
{
  "schema_version": "research-run-envelope-v1",
  "run_id": "tmf-20261116-001",
  "engine_id": "tmf",
  "source_repository": "enriquespinoza/TMF-Intraday-Predictive-Analytics",
  "source_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "data_as_of_utc": "2026-11-16T20:00:00Z",
  "available_at_utc": "2026-11-16T20:05:00Z",
  "generated_at_utc": "2026-11-16T20:10:00Z",
  "subject": {"kind": "asset", "id": "TMF"},
  "artifact_type": "forecast",
  "horizon": {"value": 60, "unit": "minutes", "calendar": "XNYS"},
  "manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "research_acceptance": "NOT_ESTABLISHED",
  "broker_submission_permitted": false
}
```

## Local read-only verification

```bash
python scripts/validate_registry.py
python -m unittest discover -s tests -p 'test_integration_contracts.py' -v
python -m integration_contracts \
  --envelope /trusted/run/envelope.json \
  --manifest /trusted/run/manifest.json \
  --artifact-root /trusted/run/artifacts \
  --registry config/engine_registry.json
```

Only `VERIFIED_SOFTWARE_CONTRACT` may be returned by this validator.
It cannot assert market-data correctness, independent source authenticity,
predictive advantage, availability of remote upstream manifests, or paper
broker readiness. Research acceptance is fixed at `NOT_ESTABLISHED` and
`broker_submission_permitted=false`. The validator has no network access
or broker integration.

## Outstanding producer/consumer adapters

| Boundary | Additional required domain checks |
| --- | --- |
| Scanner → Optimizer | Candidate contract v1, eligible ranks, session/source hashes |
| Technical → Orchestrator/Ensemble | Causal availability; descriptive events are not automatic forecasts |
| Fundamental → Orchestrator/Ensemble | Separate SEC descriptive artifact from future calibrated expert forecast |
| TMF → Orchestrator/Ensemble | Minute versus trading-session horizon, origin/target and Treasury release timing |
| Eligible forecasts → Ensemble | Same asset, horizon, origin, point-in-time input and matured reliability |
| Ensemble → Optimizer | Forecast/uncertainty overlay only; optimizer owns portfolio constraints |
| Optimizer → Execution | Resolve rich CSV/manifest contract against basic target JSON example |
| All engines → Decision Review | Real decision records, rejected actions, execution outcomes, matured forecasts |

The generic contract intentionally does **not** replace those domain contracts.
Each adapter requires positive and rejection fixtures and version-pinned producer
and consumer commits. Sibling engines must not be imported as unpinned code.

## Change governance

1. Individual ChatGPT project conversations work in each engine's feature branch;
   integration changes enter through a separately reviewed orchestrator PR.
2. A breaking public artifact change requires a new schema ID/version and paired
   producer/consumer contract tests. No silent coercion, fallback or backdating.
3. CI performs synthetic and byte-level software checks. A green test run does
   **not** establish predictive value or permission to submit orders.
4. Future orchestrator releases must pin immutable engine commits and record
   evidence and reviewer approval. Current registry release pins remain null.
5. The separate Research Data Accumulation Audit must quantify observation
   growth, freshness, matured forecast outcomes, and benchmark-relative skill.
6. Before promotion, preserve preregistered criteria, historical holdout and
   forward evidence, independent review and approved paper-trading controls.
7. Preserve failed runs, rejected inputs and original source hashes. Keep private
   broker data, credentials and licensed raw market snapshots out of public CI.

**Next acceptance gate:** Build a pinned, synthetic
Scanner → Optimizer → SimulationBroker integration exercise with deliberate
incompatible-input rejection and immutable decision history. No integration
or paper-validation field becomes `verified` before evidence-based review.
