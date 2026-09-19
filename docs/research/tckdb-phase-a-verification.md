# Phase A implementation and rollout evidence

Plan: [Phase A](tckdb-phase-a-implementation-plan.md). Baseline:
`8539c927d6ca81758d28a67c0a0fbac31a13c9af`. This is working-tree implementation
evidence, not a production rollout approval or a corpus accuracy claim.

## Impact analysis

Repository/worktree: `TCKDB`, `/home/calvin/code/TCKDB_v2`. GitNexus 1.6.12
rebuilt the stale index at the baseline (49,618 nodes, 103,700 edges, 1,173
reported flows). The first rebuild failed with a WAL error; the retry succeeded.
CLI queries used the rebuilt runner because the MCP engine could not open its
storage version. Raw reports were retained locally under `/tmp/tckdb-phase-a-*-impacts.json`.

| Target | Reported risk | Confirmed affected paths |
| --- | --- | --- |
| `get_species_thermo` | CRITICAL | Species thermo route, composed thermo and kinetics search |
| Species-bundle request conversion | HIGH | `_persist_thermo_block`, computed-species upload |
| Client `Thermo.to_payload` | HIGH | Both bundle builders |
| `ThermoUploadRequest` / `ThermoRecord` | MEDIUM | Upload routes, queued worker, bundles, resolution, scientific reads/search |
| Shared create fragments / bundle models | LOW, with static dispatch limits | Standalone and both bundle forms; backend entity re-exports |
| Contribution reconstruction / CHEMKIN serializers / importer | LOW | Bundle export, CHEMKIN export, mechanism import |
| Selected `to_dict`, frontend schema, decorated validators and client factories | UNKNOWN | Source inspection confirms NDJSON serialization, response parsing, Pydantic/dataclass callbacks and builder tests |

HIGH/CRITICAL findings were reported before editing. UNKNOWN was not treated
as no impact. The analyzer reports dropped cross-language accesses and bounded
process enumeration; its flow list is not an exhaustive runtime graph. Source
references and tests supplement those boundaries. No claim of complete static
call coverage is made.

Final working-tree analysis used the same runner's `LocalBackend.callTool`
with `detect_changes`, `scope="all"`, and `repo="."` to retain the complete
structured result (the CLI presentation prints only 15 symbols). It returned
88 changed symbols across 46 files, four affected species-thermo flows, and
medium risk, with no `error`, `partial` or `truncated` flags. The complete
result is `/tmp/tckdb-phase-a-detect-full.json`. This is the complete query
result within the index's stated limits, not complete dynamic call coverage.
The final code index contains 49,711 nodes and 103,939 edges. No commit was made.

## Local verification

Validation used `tckdb_env`, the intended checkout's schema/client imports,
and sequential database gates against local PostgreSQL. Installed release
metadata resolves to schemas 0.45.0, client 0.84.0, backend 0.2.0 and CHEMKIN
adapter 0.3.0. Stale ignored source-tree egg-info directories were moved to
`/tmp` because they shadowed the current editable-install metadata when
`PYTHONPATH` was set. Cantera is pinned to 3.2.0 in `backend/environment.yml`.

| Check | Result |
| --- | --- |
| REST gate (includes gate-coverage checks) | 5,558 passed, 14 skipped, 27 warnings |
| API gate (includes OpenAPI drift checks) | 3,765 passed |
| Scientific gate | 2,875 passed |
| Full Python client suite | 1,483 passed |
| Shared schema package suite | 59 passed |
| CHEMKIN adapter suite | 55 passed, 1 skipped |
| Frontend thermo contract tests | 9 passed |
| Frontend production build | Passed; existing dependency/chunk warnings |
| Scoped backend mypy | Passed, 190 source files |
| Changed Python files, Ruff | Passed |
| Generated client parity / rejection-code checks | Up to date |
| OpenAPI regeneration checks | 3 passed; golden artifact regenerated |
| Local development compatibility inventory | Executed read-only; zero findings in this development database |

Logs are retained locally as `/tmp/tckdb-phase-a-*.log`. The client suite
needed local notebook-kernel socket access. Earlier red runs found incomplete
NASA fixtures, missing importer state, an uncatalogued local export error,
and stale adapter version metadata; these were corrected and rechecked.
The local ignored `docs/specs` also contained obsolete read-schema names;
those documentation references were corrected for the schema-name guard.
These local corrections are not evidence of production compatibility.

All three required scripts completed with exit status zero using their pinned
seed and worker settings. Final gate logs are `tckdb-phase-a-rest-final.log`,
`tckdb-phase-a-api-final.log` and `tckdb-phase-a-scientific.log` under `/tmp`.
The API error-body sweep examined 1,109 bodies, with 1,106 JSON error responses
reaching a client; the scientific sweep examined 486 bodies/responses. Both
exceeded their required floors. These are working-tree results; a release
must bind them to its exact implementation commit.

The REST gate also emitted background upload-worker logging errors after a
test-bound session factory was removed. The ambient-database guard refused
those connections. Its final exit status was zero; the 27 summarized warnings
were Cantera discontinuity warnings from deliberately synthetic coefficient
fixtures. The worker-thread lifetime issue is unresolved and is not claimed
fixed by Phase A. The earlier worker recovery assertion passed in isolation
and in the final full REST run. This evidence does not establish production
worker health or replace the required queue-drain review.

## Scientific validation contract

New regressions cover exact state/0 K custody through persistence, reads,
composed search, selected JSON, contribution replay, both bundle routes,
historical archive restore and frozen release bytes/checksums. Legacy partial
NASA data remains readable and recoverable while upload replay reports a
specific incompatibility. No H/S/G convention or chemical accuracy threshold
was introduced.

Numerical tests use Cantera 3.2.0, its bundled GRI-Mech 3.0 H2O data, and an
analytical constant-Cp case. They compare NASA7 source/replayed/printed cards
at bounds, 298.15 K, interior points and both sides of 1000 K. Printed error
bounds propagate the half-unit rounding of every coefficient through Cp/R,
H/(RT) and S/R, plus `1e-10`. Temperature-field precision is checked directly;
Cantera may collapse identical low/high polynomials into a single interval.
Swapped blocks and enthalpy unit changes exceed these bounds.

NASA9 tests preserve and evaluate multiple intervals with nonzero inverse-T
and integration terms, including boundaries and constant-Cp controls.
Wilhoit reference Cp values were generated with installed RMG 3.3.0:

```python
from rmgpy.thermo import Wilhoit
w = Wilhoit(Cp0=(30, "J/(mol*K)"), CpInf=(100, "J/(mol*K)"), B=(500, "K"),
            a0=1.2, a1=-0.3, a2=0.7, a3=-0.2)
print([(t, w.get_heat_capacity(t)) for t in [200, 298.15, 500, 1000, 2000, 3000]])
```

The generated values are pinned in the test with exact coefficient custody,
including present/absent integration constants. These tests do not claim H/S
reconstruction when constants are absent or accuracy against experimental data.

## Rollout hold points

No migration, backfill, production modification, package publication or release
regeneration is part of this working-tree change. Before deployment:

1. Stop accepting new queued uploads, and drain queued/processing jobs under
   the old worker. Preserve failed/incompatible payloads for explicit review.
2. Run the read-only inventory using the intended deployment environment:

   ```bash
   cd backend
   PYTHONPATH=. conda run -n tckdb_env python scripts/inventory_thermo_contract.py > thermo-contract-inventory.ndjson
   ```

   It opens a PostgreSQL read-only repeatable-read transaction. Thermo rows
   report stable references and separate creation/CHEMKIN reasons; every
   pending job is reported, including whether its thermo profile was checked.
   An empty local-development inventory does not establish production compatibility.
3. Review the inventory. Historical rows stay unchanged; use archive recovery
   for incomplete evidence. Explicitly resolve any remaining incompatible jobs.
4. Deploy backend 0.2.0 with schemas >=0.45.0 and coordinate client 0.84.0 /
   CHEMKIN adapter 0.3.0. Validate generated OpenAPI and client compatibility.
5. Record exact commit, required gates, inventory review and paired recovery
   evidence. Production rollout and the broader Phase A completion gate remain
   pending until this evidence exists. Phase B rights and reproduction gates
   are separate.
