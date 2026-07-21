# Machine-consumer query contract

Status: requirements 1–3 and the corresponding requirement-10 tests/guides are implemented. Requirements 4–9 are the ordered follow-up contract; they must not be advertised as available until their code and tests land.

## Compatibility rules

- Additive response fields are backward compatible. Removing or changing the meaning/type of a field requires a versioned contract.
- A declared filter is either enforced or rejected. It must never be accepted and ignored.
- Public refs are stable identifiers. Integer database IDs are deployment-policy fields and are optional in hosted response schemas.
- Pagination metadata describes the complete filtered candidate set before page slicing; any collapse semantics must be reported separately.

## Ordered requirements

1. **Structured errors and client fallback — implemented.** Query/API error responses expose top-level `code`, `detail`, and object-valued `context` (including middleware-generated 429s; readiness probes retain their operator-specific status shape). Existing `"code: message"` detail strings remain unchanged, are promoted server-side when possible, and are parsed by older-server-compatible Python clients when top-level `code` is absent. Framework validation uses `request_validation_error`; declared-but-unavailable filters use `unsupported_filter` with `context.endpoint` and `context.filters`.

2. **Fail closed on ignored filters — implemented.** Non-null `inchi` on species search (including composed thermo and species-calculation search), frequency-scale-factor `model_kind`/`software_version`, and energy-correction-scheme `software`/`software_version`/`used_by_thermo` return 422 `unsupported_filter`. No subset of a request is silently applied.

3. **Hosted JSON and OpenAPI agree — implemented.** All successful scientific-response component schemas are followed transitively. Policy-hidden internal-ID properties remain documented but are not required and carry `x-tckdb-policy-hidden: true`. A real hosted, ID-stripped species response is validated against the served OpenAPI document.

4. **Canonical pressure query — planned.** Add explicit `pressure_bar` as the canonical field. Keep `pressure` as a deprecated alias for one compatibility window. If both are supplied, accept equal normalized values and reject unequal values with a coded 422 containing both values. Define and test an applicability matrix by kinetics model; reject pressure filters where the selected model cannot represent pressure rather than broadening the query. Cross-cutting convention: stored Arrhenius `a` is the submitted rate-expression value, while `degeneracy` is separate provenance metadata; TCKDB does not implicitly multiply or divide `a`, and `null` degeneracy does not mean `1`.

5. **Exhaustive composed searches — planned.** Resolve all matching identity candidates before downstream thermo/calculation filtering, ordering, collapse, and pagination. `pagination.total` is the complete filtered pre-page count, never the size of an internal candidate cap. Expose a distinct pre-collapse count when collapse changes cardinality.

6. **Comparable `lowest_energy` — planned.** Require exactly one species identity and one exact level of theory (public ref preferred). Compare only the same calculation/result energy field and the same correction convention; never rank across levels of theory, species, incomparable calculation types, missing energies, or mixed correction semantics. Otherwise return a coded 422. The stored-kinetics degeneracy convention in requirement 4 also applies to any kinetics-derived ranking or comparison.

7. **Chemistry-first PDep results — planned.** Pressure-dependence records expose ordered source and sink well/channel compositions as public species refs plus stoichiometric coefficients. Add enforceable filters for source composition, sink composition, participant species, and network public ref; declare exact/set/subset matching semantics and canonical stoichiometric normalization.

8. **Compact trust and reproducibility summaries — planned.** Add opt-in `include=trust_summary` and `include=reproducibility_summary`. Each compact block reports an explicit assessment state from `current`, `stale`, or `unassessed`, the assessment public ref/version when present, and only bounded counts/flags by default. Absence of an assessment must serialize as `unassessed`, never as approval.

9. **Typed Python models and complete iterators — planned.** Generate or maintain typed public response/error models from the corrected hosted OpenAPI contract. Every paginated scientific search receives a lazy iterator that preserves all filters/includes, advances from returned pagination metadata, terminates on the reported total, and detects non-advancing pages.

10. **Guides and regression coverage — partial, ongoing.** Every requirement lands with positive, conflict, empty-result, policy-hidden, and legacy-compatibility tests as applicable. Public examples use supported ranking fields and the actual nested geometry shape. Guides identify planned behavior explicitly and never demonstrate accepted-but-ignored filters.

## Release gate

A requirement becomes supported only when the hosted OpenAPI document, runtime response, Python client, user guide, and regression tests agree. Contract tests run against policy-hidden hosted JSON, not only Pydantic objects produced before the response seam.
