# `app/workflows/` — write orchestration

Each module here is a **multi-step persistence orchestrator** for one
upload kind. A workflow validates nothing itself (schemas do that) and
holds no reusable logic (services do that) — it *sequences* resolution
services in dependency order and links the produced records to a
submission.

**The shape every workflow follows** (see
[`docs/guides/system_flow.md`](../../../docs/guides/system_flow.md) §2):
resolve identity (dedupe) → resolve provenance (calculation, software)
→ persist append-only results → `apply_review_policy()` to link records
to the submission.

| File | Persists |
|---|---|
| `conformer.py` | Conformer observations + groups, calculations, optional statmech/transport |
| `reaction.py` / `computed_reaction.py` | Reactions, entries, kinetics, TS |
| `computed_species.py` | Species + full computed bundle |
| `kinetics.py`, `thermo.py`, `statmech.py`, `transport.py` | Standalone product uploads |
| `transition_state.py` | TS entries with IRC/NEB linkage |
| `network.py`, `network_pdep.py` | Pressure-dependent networks |
| `contribution_bundle_submit.py` | Offline bundle replay |

**Rules:** upload schemas carry scientific content, never FK IDs; a
workflow opens/closes its submission via `services/upload_submission.py`;
new write paths must link records via `apply_review_policy()`.
