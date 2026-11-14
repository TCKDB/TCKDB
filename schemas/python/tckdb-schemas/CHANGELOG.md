# Changelog

## 0.175.0 - 2026-11-14

Two refusal codes are added: `species_name_duplicate` (409) and `species_name_invalid` (422), returned by `POST /api/v1/curation/species-names` (curator or admin). The producer contract lists both curation routes among the role-gated write routes. No model or payload shape changed.

## 0.174.5 - 2026-11-14

The producer contract's further-reading list names the scientific check register once, for which checks refuse and which only warn and for the chemistry positions TCKDB enforces, and no longer links a design record. No refusal, warning, field or behaviour changes.

## 0.174.4 - 2026-11-14

Refusal and warning messages in the package state the rule without design-record references, and the energy-correction frequency-level refusal drops "for now". The package README points to this changelog, and its changelog entries for 0.23.0 to 0.34.0 now live at the bottom of this file, without design-record references. No code, field, payload shape or condition changes.

## 0.174.3 - 2026-11-12

The contribution-bundle example in the contract shows `source_instance.schema_version` as the current Alembic revision. `schema_version` is a free-form non-empty string that no import reads. No refusal, warning, field or behaviour changes.

## 0.174.2 - 2026-11-11

Docstrings, comments and field descriptions in the package are reworded to state behaviour only. No refusal, warning, field or behaviour changes.

## 0.174.1 - 2026-11-11

The check descriptions the generated producer contract quotes (`contract/`) are shortened to one sentence per check and per escape hatch. No refusal, warning, field or behaviour changes.

## 0.174.0 - 2026-11-11

The method-family classifier ships in the package. New module `tckdb_schemas.method_families` with `MethodFamily`, `MethodClass`, `MethodClassFilter`, `classify_method`, `method_stem_key`, `CLASSIFICATION`, `CLASSIFICATION_NAME`, `CLASSIFICATION_VERSION`, `CLASSIFICATION_REF`, `UNCLASSIFIED` and the table constants `DFT_BY_RUNG`, `OTHER_BY_FAMILY`, `DISPERSION_SUFFIXES`, `DISPERSION_STRIP_PATTERN`, `SPIN_PREFIXES`, `SUFFIX_STEM_OVERRIDES`, `SUFFIXED_KEYS`, `MIN_DFT_RUNG` and `MAX_DFT_RUNG`; and `tckdb_schemas.method_names` with `method_identity_key`, `MethodAlias`, `NAME_ALIASES`, `SUFFIX_ALIASES` and `SUFFIX_RULES`. The classification of every method name is unchanged. No refusal, warning, field or payload shape changed.

## 0.173.1 - 2026-11-11

The producer contract text is reworded to state behaviour only: route descriptions in `PRODUCER_CONTRACT.md` no longer cite decision records or design plans. No refusal, warning, field or behaviour changes.

## 0.173.0 - 2026-11-11

One warning code is added: `one_electron_hf_substitution`, returned when a kinetics record's declared correlated energy level is accepted for a one-electron participant whose linked energy was computed at Hartree-Fock. Context: `declared_level_of_theory_ref`, `energy_calculation_refs` and `cabs_singles_absent` (true when the declared method is an F12 method). It is scientific check register entry 83. No model or payload shape changed.

## 0.172.1 - 2026-11-11

Comments and docstrings in the Hessian-method and stationary-point modules are reworded to state behaviour only. No refusal, warning, field or behaviour changes.

## 0.172.0 - 2026-11-11

Three codes are added: the refusal `applied_energy_correction_value_conflict` (422) and the warnings `applied_energy_correction_note_not_stored` and `applied_energy_correction_reused_hidden` (context `review_status`: `rejected` or `deprecated`). The refusal is returned by every upload route that carries applied energy corrections when a correction with the same target, sources, scheme or scale factor, role and temperature is already stored with a different total or component breakdown. `context` carries `record_kind`, `application_role`, the target and scheme public refs, `field`, the stored and supplied totals with units, and `differs`. A matching correction is now reused rather than refused. No model or payload shape changed.

## 0.171.0 - 2026-11-11

One refusal code is added: `species_name_query_invalid` (422), returned by `GET /api/v1/scientific/species/name-search` for a query that is not 2 to 64 characters of letters, digits, space and `- , ' ( ) [ ] + . :`. No model or payload shape changed.

## 0.170.0 - 2026-11-09

One refusal code is added: `request_body_too_large` (413). The frontend proxy answers a body over its size cap (100 MiB on the upload, job and calculation-artifact routes, 10 MiB on other `/api/` routes) with this code and `context.max_bytes`, where it sent a bare HTML page. A request-validation 422 no longer echoes the request body under each error's `input`. No model or payload shape changed.

## 0.169.0 - 2026-11-09

The deposit-warning item on scientific reads gains optional `retired_at` and `retired_reason`, set only for curators and admins reading a retired warning (`artifact_attached`, `single_point_deposited`, `hessian_stored`, `superseded_by_recheck`). Other readers get current warnings only and never receive the keys. No field is removed.

## 0.168.0 - 2026-11-09

No refusal, warning or payload shape changes. The generated producer contract (`contract/`) now names the scientific check register entry for every refusal and warning code the register gained an entry for (the register has 80 entries, up from 52), and the text of register entry 18 states the tolerance order in force since #864: the method the output log reports, then the method the echoed input requests, then the recorded `freq.hessian_method` or the input deck, then the program's assumed default, then 50 cm-1.

## 0.167.0 - 2026-11-09

`thermodynamic_target.declared_by` is added to a thermo record's target declaration, with the enum `ThermoDeclarationSource` (`depositor`, the default, and `chemkin_format`). A `chemkin_format` target is refused unless it is `equilibrium_ensemble` (`thermo_declared_by_target_incoherent`) on a record with phase `gas` and a NASA-7 block (`thermo_declared_by_record_incoherent`). No field is removed.

## 0.166.0 - 2026-11-09

Four refusal codes join the producer contract; no field is added or removed: `record_self_approval` (403), `submission_self_approval` (403), `record_not_found` (404) and `record_type_not_reviewable` (422). They come from the record-review and submission-approval routes, which a producer does not call; a client that lists the codes sees them.

## 0.165.0 - 2026-11-09

The assumed Hessian method for an unrecorded `freq.hessian_method` changes for the methods below; a recorded, reported or echoed method still wins. One warning code is added, `hessian_method_refined`. No field or refusal code is added or removed.

Gaussian:

- `MP4`, `MP4(SDTQ)` and `MP5` are finite differences of energies (80 cm-1). `MP3`, `MP4(DQ)` and `MP4(SDQ)` stay finite differences of gradients (50).
- `B2PLYP`, `B2PLYPD`, `B2PLYPD3`, `B2PLYPD3BJ`, `mPW2PLYP` and `mPW2PLYPD` are analytic (30). Other double hybrids assume nothing.
- Method options are read: `MP4(SDTQ,FC)`, `MP4=FC`, `MP4 FC`, `CCSD(T,FC)`, `CCSD=(T,FC)`, `CCSD-T` and `QCISD(T,E4T)` map to the method without the option. `CCSD(T,FC)` was read as CCSD. `RO`, `CCSDT`, other tails and unknown options assume nothing.

ORCA:

- CCSD, CCSD(T), DLPNO and DLPNO2013 coupled cluster, `UHF-` / `ROHF-` prefixed coupled cluster and F12 methods (including F12-MP2 and `RI-MP2-F12`) are finite differences of energies (80) on every version. Other `DLPNO-*` methods assume nothing.
- Open-shell DLPNO-MP2 and ROHF MP2 are finite differences of energies (80). Open shell is read from the level of theory's `spin_treatment` or a `UHF` / `ROHF` method prefix; with neither, the closed-shell row (50) applies. MP2, RI-MP2, SCS-MP2 and closed-shell DLPNO-MP2 stay at 50.
- From ORCA 6, `AUTOCI-CCSD`, `AUTOCI-CCSD(T)`, `AUTOCI-MP2`, `AUTOCI-MP3`, `AUTOCI-MP4` and `AUTOCI-MP4(SDQ)` are finite differences of gradients (50) when the level of theory states `core_treatment` all-electron and `ri_approximation` none; otherwise, and for other AUTOCI methods, nothing is assumed.
- Double hybrids written without a hyphen (`PBE0DH`, `PBEQIDH`, `PBE0-QIDH`, `wB97X2`, `B2KPLYP`, `B2TPLYP`) resolve as double hybrids: ORCA 50 instead of plain DFT 30, Gaussian nothing assumed.
- An input deck or echoed input with `NumFreq` and no `NumGrad` for a method with no analytic gradient reads as finite differences of energies. For DLPNO-MP2 the deck's own open-shell evidence counts: a `UHF`, `UKS` or `ROHF` keyword, or a multiplicity above 1 on the coordinate line (`* xyz`, `*xyzfile`, `*intfile`, `*gzmtfile`, `*pdbfile`) or in a `%coords` block.
- A recorded `finite_difference_gradient` against such a deck or echoed input is accepted (also when the level of theory states no spin treatment), stored as `finite_difference_energy` (80), and returned with `hessian_method_refined` (context `recorded`, `stored_method`, `stored_basis`, `refined_because`). A recorded `analytic` against `NumFreq`, and a recorded gradient method against `NumGrad` for a method with an analytic gradient, are still refused.

Other:

- `resolve_tau_with_assumption` and `infer_hessian_method` take `spin_treatment`, `core_treatment` and `ri_approximation` keyword arguments; `resolve_tau_with_assumption` also takes `numfreq_energy_implied`.
- `tau_method_family` can be `mp4(sdq)`, `mp4(sdtq)`, `mp5`, `mp2-f12` or `autoci`; `mp4` is no longer written. `tau_assumption_rule` gains `orca_no_analytic_gradient`, `orca_open_shell_no_gradient` and `orca_autoci_gradient_from_6`.
- `MP5`, `UMP5`, `F12-CCSD(T)`, `F12/RI-CCSD(T)` and the AUTOCI methods above now count as SCF-based, so a Gaussian `MP5` job gets the assumed `scf_convergence` of `tight`.

## 0.164.0 - 2026-11-09

`HessianSource` gains `parsed_log_archive`: a Cartesian Hessian read from a Gaussian log's archive block, for logs that print no `Force constants in Cartesian coordinates` block. The matrix is stored only when it is consistent with the archive's own atoms (rigid translations and rotations annihilated, using the archive gradient) and the atoms match the stored input geometry. No upload field changes.

Four reasons join `hessian_not_stored_unparseable` (a warning; the artifact is stored either way): `archive_block_unreadable`, `archive_frame_inconsistent`, `archive_atoms_unsupported` and `archive_composite_geometry_mismatch` (a composite job whose final block is at a different geometry from its frequency step, as in G1, G2, G3 and CBS-APNO).

## 0.163.0 - 2026-11-09

An xtb output file is recognised: `detect_software_from_text` returns `"xtb"` for `* xtb version N` and the boxed `x T B` title, and for xtb's `g98.out` (a Gaussian-98-layout file naming the xtb code). `SoftwareName` gains `"xtb"`. Nothing is parsed from an xtb log. An xtb file is accepted as an `output_log`.

Ten artifact refusals gain codes (all 422, with `context`): `output_log_signature_unrecognized` (`supported`, `window_bytes`), `artifact_empty`, `artifact_too_large`, `artifact_sha256_mismatch`, `artifact_size_mismatch`, `artifact_not_utf8_text`, `artifact_invalid_base64` (the filename is in `context.depositor_supplied`), `artifact_total_size_too_large`, `artifact_encoded_payload_too_large` and `artifact_total_encoded_payload_too_large`.

## 0.162.0 - 2026-11-09

One refusal code is added, `reaction_family_conflict` (422), and one existing refusal gains a code. A reaction deposited under a family different from the one it is already recorded under was refused with an uncoded `validation_error` whose text quoted both family names, including depositor text. It is still refused; it now carries `reaction_family_conflict`, and the message names a family only when it comes from the seeded `reaction_family` vocabulary (as `context.recorded_family` and `context.supplied_family`; depositor text is never repeated). All three conflict shapes are refused: seeded against seeded, raw against raw, and seeded against raw in either order (before, a deposit that named a seeded family for a reaction recorded under raw text, or the reverse, was accepted). A computed-reaction bundle's `reaction_family` now applies to the bundle's own reaction only: a kinetics fit for another reaction no longer has it stamped on, or refused because of it. A network solve whose `bath_gas` mole fractions do not sum to 1 within 1e-9 now carries `kinetics_bath_gas_mole_fraction_sum`, the code a kinetics rate's bath gas already used, with the field `solve.bath_gas` and the sum and tolerance in `context`. One rule, one code, one tolerance. The message text changes; no model or payload shape changed.

## 0.161.0 - 2026-11-07

One refusal is narrowed and gains a `reason`; no field is added or removed. A kinetics record's `energy_level_of_theory` names method, basis and qualifiers, and its spin treatment is judged per participant against the linked reactant, product and transition-state energies:

- every link must match the declared level on every identity field except spin (method, basis, aux/cabs basis, dispersion, solvent, solvent model, core treatment, relativistic Hamiltonian, PNO truncation, RI approximation);
- a closed-shell link (multiplicity 1, computed restricted or restricted-open, or unrestricted with a stored ⟨S²⟩ at or below 0.1) never constrains spin;
- the other links (every multiplicity above 1, and an unrestricted singlet with ⟨S²⟩ above 0.1 or none stored) must share one reference, unrestricted or restricted-open; a link of unknown spin is skipped. Otherwise `kinetics_energy_level_contradiction` carries `reason: open_shell_reference_mixed`;
- a declaration that states no spin treatment is accepted; one that states it must equal that open-shell reference (or, with no open-shell link, every known link spin), else `reason: declared_spin_differs`.

The same rule applies on `/uploads/kinetics` and bundle import, where it also decides which calculations a declared level finds: a calculation at the declared spin is preferred, a closed-shell one run restricted is eligible, and a radical with no calculation at the declared spin finds none. A violation refuses on the standalone route; on bundle import the level is stored without source calculations and `energy_level_stored_without_source_calculations` is returned, as when nothing matches. When an unrestricted singlet is counted as open-shell only because no `spin_diagnostic.s_squared` is stored for it, the refusal's context carries `s_squared_missing_for` (calculation refs) and its message asks for it. `energy_level_stored_without_source_calculations` gains the reason `contradiction` (calculations were found but contradict the declared level or each other on spin), distinct from `none` and `ambiguous`. A reaction mixing radicals computed unrestricted with closed-shell species computed restricted can therefore declare its method and basis (with or without `unrestricted`), and no longer draws `missing_level_of_theory_provenance`. Thermo and statmech declarations are unchanged. Sources: the 0.1 bound is the convention the wavefunction-reliability warning already uses (Williams et al. 2011, doi:10.1016/j.cplett.2011.01.020; Rettig et al. 2020, doi:10.1021/acs.jctc.0c00986; register entry 52). Restricted-open and unrestricted references are different levels for open-shell species (Wood et al. 2006 ROCBS-QB3, doi:10.1063/1.2335438; Henry, Sullivan and Radom 2003, G3-RAD).

Contract text on the field: "The kinetics energy level names method, basis and qualifiers. Spin treatment is per participant. When at least one participant is open-shell, closed-shell participants (singlets computed restricted, restricted-open, or unrestricted collapsing to restricted) do not constrain it; the open-shell participants must share one reference (unrestricted or restricted-open), and a stated spin treatment names that reference. When every participant is closed-shell, a stated spin treatment must match their spin treatment."

## 0.160.0 - 2026-11-07

One refusal code is added: `server_response_invalid` (500). The server failed its own response model, so the request was not at fault; nothing in it needs changing. Before this, that failure reached a client as a bare `text/plain` 500, or as a 422 that blamed the request. No model or payload shape changed.

## 0.159.0 - 2026-11-07

One refusal code is added: `anonymous_body_too_large` (413). A request without a valid credential whose body is over the anonymous allowance (1 MiB by default) is refused before the body is read; authenticate with an API key or a session to send a larger one. No model or payload shape changed.

## 0.158.0 - 2026-11-05

A level of theory no longer has a free-text `keywords` field; four typed fields take over what it was used to carry. A payload that sends `keywords` is refused like any unknown field, on every path that carries a level (an upload, a composite scheme's input level, a bundle). Nothing is mapped from it into parameters: the settings a deck or log states are read from the uploaded artifact and stored as calculation parameters.

- **Removed:** `keywords` on `LevelOfTheoryRef` and `CompositeSchemeInputLevel`.
- **Added**, each optional and part of the level's identity only when stated (a payload that omits them is the level it always was, apart from the removed key below):
  - `relativistic_hamiltonian`: `none`, `dkh0`, `dkh2`, `dkhso`, `resc`, `x2c`, `zora`, `iora`.
  - `pno_truncation`: `loose`, `normal`, `tight` (the DLPNO presets; a numeric threshold is not representable and stays a parameter).
  - `ri_approximation`: `none`, `rij`, `rijcosx`, `rijk`, `rijonx`.
  - `core_treatment` gains `window` (a partial frozen core). Which window stays on the calculation's parameters, so two different windows are one level.
  - The values are the lowercase tokens only and are case-sensitive, like `spin_treatment` and `core_treatment` (send `dkh2`, not `DKH2`); a declared calculation parameter, by contrast, is case-folded before it is compared.
  - `none` is a stated value (non-relativistic, no RI), a different level from leaving the field out. Each set is the closed value set of the parsed parameter of the same meaning (`relativistic.hamiltonian`, `pno.truncation`, `ri.approximation`, `core.frozen`).
  - Next to a `composite_scheme` they are refused like the other method fields.
- **Changed:** the warning `level_of_theory_disagrees_with_artifact` now also fires for these four fields, with `context.component` one of `relativistic_hamiltonian`, `pno_truncation`, `ri_approximation`, `core_treatment` (as well as `method` and `basis`). It fires only when the level and the uploaded deck or log both state a value and they differ (per job for a multi-job deck: the one job that fits the declared method and basis is read, and none is judged when no single job fits); a deck that says nothing about a qualifier is never a disagreement.
- **Identity:** the `"keywords"` key left the level-of-theory hash payload, so every level's `lot_hash` differs from the one a server before this version computed. A deployment is reseeded rather than re-keyed; the server's migration refuses to run on a database that already holds levels of theory.
- The producer contract's focal-point example now states its relativistic term as `relativistic_hamiltonian: dkh2`.

## 0.157.0 - 2026-11-05

The imaginary-mode tolerance (tau) now follows what an inline output log says about the Hessian, ahead of the recorded `freq.hessian_method` and the deck. Order: the method the logs REPORT the frequency job ran (ORCA only), then the input the logs ECHO, then the recorded parameter or the deck, then the program's assumed default, then `protocol_not_recorded`; a reported or echoed method gets the same tau as the same method recorded, and a recorded value that contradicts the deck, or the input a log echoes, is refused with `hessian_method_contradicts_input_deck`. A log reports a method only through a header it prints (`SCF HESSIAN` is analytic; `ORCA NUMERICAL FREQUENCIES` with displaced-gradient lines is finite differences of gradients), only from the final frequency job (no optimiser step after the last listing), only when every listing agrees, never from a file that holds several jobs, and not for a numerical gradient (`NumGrad`). An echo that cannot be bound to one job (ORCA `$new_job`/`%compound`, a Gaussian log with more than one route) reads nothing. With several logs, or a file of several ORCA runs, the method they all agree on is used. Gaussian logs report no method. New: `tckdb_schemas.hessian_method_reported`, `echo_hessian_method`, `calculation_reported_method`, `calculation_echo_method`, and `reported_method` / `echo_method` arguments on `resolve_tau_with_assumption`. The `hessian_method_disagrees_with_stored_tau_basis` warning (same code) can now carry `reported_method` in its context. No field or code added or removed; a payload with no inline output log is judged exactly as before.

## 0.156.0 - 2026-11-05

The characters trimmed from a state label or term symbol before matching are now one explicit set (`TRIM_CHARS`, shipped as `trim` in `electronic_state_examples.json`): exactly the 29 characters Python's `str.strip()` removes (every `str.isspace()` code point), which is what the write path has always trimmed, so what a deposit accepts does not change. JavaScript's `trim()` differs (it also strips U+FEFF, and not the `\x1c`-`\x1f` and `\x85` that Python does), so a frontend now trims with the same explicit set. U+FEFF stays untrimmed on both sides. No field or code added or removed.

## 0.155.0 - 2026-11-04

Packaging only. No field, code or rule is added, changed or removed; every 0.154.0 payload validates unchanged. The distribution now carries what PyPI needs: the project URLs point at `github.com/TCKDB/TCKDB`, the MIT `LICENSE` file ships in the sdist and wheel, and the build requires `setuptools>=77` (SPDX licence expression). Nothing in `tckdb_schemas` itself changed.

## 0.154.0 - 2026-11-04

The route of a Gaussian log is read correctly when it is short. One warning code is added (`output_log_job_not_bound`); no field is added or removed.

- **Fixed:** Gaussian sizes the rows of dashes round the echoed route to the route itself (43 wide for `# sp b3lyp/def2svp`, 70 for a long one). The reader only treated rows of 60 or more as the route's edges, so a short route (under 60 characters, which is common) was never closed: it ran on into the log (one measured route was 5012 characters) and the settings read from it included log text, such as `NAtoms` and `Conv`, as if they had been requested. A row of four or more dashes is now an edge too.
- New warning `output_log_job_not_bound`. An output log holds several jobs (a `--Link1--` file) and no single job could be matched to this calculation, so the settings in it were not read. One warning per log, however many times the same log is read in a request. A reader shows the calculation's grid and SCF convergence as `not_stated` rather than as the program's default while this warning stands and no row from the log exists.
- **Measured side effects** (every Gaussian text in the test corpus, before and after):
  - The tau and Hessian-method detection is unchanged on all 521 texts.
  - 59 ARC payload logs (routes of about 55 characters) no longer store about 197 junk raw rows read from log text. No canonical row changed.
  - Two Gaussian 03 composite logs now read the route the user wrote instead of an internal step's:
    - `cbs_qb3_so2oo_g03`: the route is `#CBS-QB3 opt freq` (it was `#N Geom=AllCheck Guess=Read SCRF=Check MP2/CBSB3 ...`, pair MP2/CBSB3, now none). Rows lost: `guess.strategy=read`, `output.verbosity=normal`. Rows gained: none.
    - `g3_ethylene_g03`: the route is `# G3 nosym optcyc=100 scf=tight` (it was `#N Geom=AllCheck Guess=Read SCRF=Check MP2(Full)/6-31G(d) Opt=RCFC ...`, pair MP2(Full)/6-31G(d), now none). Rows lost: `guess.strategy=read`, `output.verbosity=normal`, `core.frozen=all_electron`. Row gained: `scf.convergence=tight`.
  - `method_basis_jobs` now lists the internal-step routes of 5 composite logs. That can only drop a level-of-theory warning, never add one.

## 0.153.0 - 2026-11-04

The assumed SCF convergence is shown where a program documents one. No field is added or removed.

- `tckdb_schemas.program_default_settings` now lists an SCF convergence default, spelled as the `scf.convergence` token (`tight`). It applies to an allowlist of SCF-based methods (DFT, HF, MPn, coupled cluster incl. DLPNO/PNO/F12, QCISD, CIS/CISD, and Gaussian composite methods) and needs a declared method, program and version; molecular mechanics, semi-empirical, xTB, Hueckel, CASSCF/MRCI/NEVPT2 and unrecognised methods get none; the DFT-only limit stays for the grid. Gaussian 09 and 16: `tight` for every calculation type (manual: "In Gaussian 09, SCF=Tight is the default"). ORCA 5 and 6: `tight` for a geometry optimisation only (manual: "TightSCF in the SCF part is set as default"); the manuals give no named default for other ORCA job types, so those stay `not_stated`. Gaussian 16 `tight` rests on the unchanged G09 keyword plus an observed default G16 run (RMS density 1e-8) accepted by the owner; Gaussian 03 is not listed for want of a source. `assumed_default_setting` takes the calculation type as a new optional argument.
- The module docstring no longer says no SCF default is documented. Used when a record is read; never stored.

## 0.152.0 - 2026-11-04

A deposit can earn one more warning, and the rule for a deck against a log reverses. No field is added or removed.

- **Reversed:** where an uploaded input deck and the input a log echoes state the same setting, the **log's value is now kept** and the deck's row for it is not stored (it was the other way round). The log is the record of the run, and a deposit often has nothing else. `input_deck_disagrees_with_output_log` still warns, and its sentence now says the log's value was kept. Reading order for one setting: what the log reports, then the input the log echoes, then the uploaded deck, then a value the depositor typed.
- New warning `reported_setting_differs_from_requested`. The output log reports a value for a setting that contradicts the documented report of what was requested (an ORCA `DefGrid` against the angular grid ORCA prints, an ORCA SCF convergence keyword against the thresholds ORCA prints). Both rows are kept. `context` has `canonical_key`, `software` and, under `depositor_supplied`, `requested_value` and `reported_value`. Gaussian never earns it: its grid report is an undocumented internal code.
- New module `tckdb_schemas.program_default_settings`: the documented default grid by program and major version, spelled as the `grid.quality` tokens a request uses (Gaussian 09 `fine`, Gaussian 16 `ultrafine`, ORCA 5 and 6 `defgrid2`), and only for a DFT method. Used when a record is read; never stored. No SCF default is listed because none is documented.
## 0.151.0 - 2026-11-04

An accepted deposit can now earn three more warnings, about wavefunction diagnostics stored with a calculation. No field is added or removed.

- New warning `t1_diagnostic_above_closed_shell_threshold`. A closed-shell calculation (the multiplicity of its species or transition-state entry is 1) whose method is restricted coupled cluster with singles (an unrestricted `U` method is out of scope; CCSD, CCSD(T), their F12, local, DLPNO and PNO variants, CCSDT, QCISD) and whose `wavefunction_diagnostic.t1_diagnostic` is strictly above 0.02 earns it. 0.02 is the rule of thumb of Lee and Taylor (1989). The calculation is stored as sent and nothing about the value, its review status or any selection changes. `context` has `t1_diagnostic`, `threshold` (0.02) and `multiplicity` (1). The sentence says only that the value is above the rule of thumb and that T1 does not by itself establish multireference character. It is placed on the calculation's record; a thermo or kinetics record that cites the calculation earns no warning of its own.
- New warning `spin_contamination_above_conventional_threshold`. A calculation with a stored `spin_diagnostic.s_squared` whose distance from S(S+1) is strictly more than 0.1 earns it, where S comes from the declared multiplicity of the calculation's species or transition-state entry (never from `s_squared` or `s_squared_expected`; `s_squared_annihilated` is not used). 0.1 is a convention of the field (cited in the literature to Williams et al. 2011; Rettig et al. 2020 use a relative 10%), not a finding about the system, and the sentence says so. `context` has `s_squared`, `ideal_s_squared`, `multiplicity` and `threshold`. The calculation is stored as sent.
- New warning `spin_expected_contradicts_multiplicity`. A stored `spin_diagnostic.s_squared_expected` (the ideal value the program printed) that differs from S(S+1) of the entry's declared multiplicity by more than 0.001 earns it. It is separate from the contamination warning; both can apply to one calculation. `context` has `s_squared_expected`, `expected_from_entry` and `multiplicity`.
- An open-shell T1 and any D1 value earn no warning: programs define open-shell T1 differently and D1 has no verified threshold. Both stay stored and shown.
- The `d1_diagnostic` description now names Janssen and Nielsen (1998) as the source of D1.

## 0.150.0 - 2026-11-03

An accepted deposit can now earn four more warnings, each about a setting, a deck or a log that disagrees with what was declared. No field is added or removed.

- New warning `parameter_disagrees_with_artifact`. A parameter the deposit states (`parameters[]` with a `canonical_key` and `canonical_value`) differs from the same setting read from an uploaded input deck or output log of the same program. Only settings that can change the result are compared (`calculation_parameter_vocab.affects_scientific_result`), only when both sides state a canonical value (a deck that is silent about a setting agrees with every declaration of it), and never across programs. `freq.hessian_method` is left to `hessian_method_disagrees_with_stored_tau_basis`. `context` has `canonical_key`, `software`, `observed_in` (`input` or `output_log`) and, under `depositor_supplied`, `declared_value` and `observed_value`. Both rows are kept.
- New warning `input_deck_disagrees_with_output_log`. A deck and a log attached to one calculation state different values for one setting. The deck's value is kept; the log's row for that setting is not stored. `context` has `canonical_key`, `software` and, under `depositor_supplied`, `deck_value` and `log_value`.
- New warning `level_of_theory_disagrees_with_artifact`. The method or the basis set read from the route line or keyword line of an uploaded Gaussian or ORCA deck or log differs from the declared level of theory, compared through the same identity keys the level of theory is deduplicated by. `context` has `component` (`method` or `basis`), `software`, `observed_in` and, under `depositor_supplied`, `declared_value` and `observed_value`. The declaration is kept.
- New warning `output_log_disagrees_with_earlier_output_log`. A second output log attached to a calculation states a different value for a setting than the first. The earlier log's value is kept (a later log never silently replaces it) and the later log's row for it is not stored. `context` has `canonical_key`, `software` and, under `depositor_supplied`, `earlier_value` and `later_value`.
- Typed `parameters[]` values are folded to the form the parsers emit before they are compared (`UltraFine`, `GD3BJ`, `PCM`, `Water` agree with what a deck says); a value that is not a token of its closed set is never judged. The method and basis comparison understands spin prefixes, `PBE1PBE` for `PBE0`, RI forms, a dispersion suffix, `/Auto`, several jobs in one file (`--Link1--`, ORCA `$new_job`), and ignores a bare reference method named beside a correlated one.
- A late output log is also checked against the stored Hessian-method tolerance (`hessian_method_disagrees_with_stored_tau_basis`), like a late deck.
- The route echoed by a Gaussian log is rejoined by dropping only the one blank column in front of each wrapped line, so a wrap on a space no longer fuses two tokens (`guess=readub3lyp/def2tzvp`).
- Output logs are now read for parameters, from the input they echo. A deck and a log never erase each other's rows, and where both state a setting the deck's row is the one kept, whichever arrived first.
- `include=parameters` on the scientific calculation read, and the calculation parameter read, carry `source` (`parser`, `upload`, `curated`) on every row.

## 0.149.0 - 2026-11-03

An electronic-state label and a term symbol must now be a recognised spectroscopic shape, and are stored in one canonical spelling. Two refusal codes join the contract. No field is added or removed.

- `electronic_state_label_invalid` (422): `electronic_state_label` must be a spectroscopic letter with an optional tilde and primes (`X`, `A`, `E`, `a`, `X~`, `A'`, `A''`) or a state ordinal (`S0`, `S1`, `T1`, `D0`, `Q0`), in at most 8 characters. Symmetry species such as `1A1` belong in `term_symbol`, not here.
- `term_symbol_invalid` (422): `term_symbol` must be an atomic term (`3P`, `3P2`, `2P_{3/2}`), a linear-molecule term (`2Pi`, `3Sigma_g-`, `1Sigma_g+`, `2Pi_{3/2}`, or Greek and superscript spellings) or a polyatomic term (`1A1`, `2A'`, `1A1g`), optionally with a state letter in front (`X2Pi`, `X 1A1`, `X^2Pi`), in at most 64 characters.
- Every accepted spelling is stored as one canonical form, so `2Pi`, `^2Pi`, `^{2}Pi` and `2Π` are one value and one species entry: superscript multiplicity, Greek capitals, subscript indices and J, `′`/`″` for primes, a combining tilde (`A~` is `Ã`), and a state letter written first and followed by one space (`X ²Π`). The spelling you typed is kept in `term_symbol_raw` when it differs and you did not send one. A state label has no raw field.
- `R`, `S`, `Z`, `M`, `P` (and lower case) are refused as state letters, as are `Ra`, `Sa`, `?`, `-`, words and isotope-like text. `E`/`e` are accepted (CO E¹Π); the server writes the state part of a joined label with the word `state`, so `state E` cannot be read as the stereo descriptor `E`.
- `term_symbol_raw` is not checked: it is kept verbatim for provenance and is never joined into a displayed label.
- `tckdb_schemas.electronic_state` is the one place the grammar and the canonical form are written (`ELECTRONIC_STATE_LABEL_SOURCE`, `TERM_SYMBOL_SOURCE`, `is_valid_*`, `canonical_*`). `electronic_state_examples.json` ships in the package with accepted and rejected examples, the pattern sources, and an input to canonical-form map, for a frontend parity test.
- Entries already stored are not rewritten. The server now writes the state part of a joined entry label with the word `state` (`excited state A`, `state T1`), so a stored label that looks like a stereo descriptor cannot be misread.

## 0.148.0 - 2026-11-02

A cyclic allene in a ring of 6, 7 or 8 atoms is now read from geometry like any other allene. No field is added or removed.

- A cyclic allene in a ring of six, seven or eight atoms gets `Ra` or `Sa` from a deposited geometry, by the same rule as a ring of nine or more and as an open-chain allene: the torsion between the two CIP-higher substituents about the end-to-end axis must lie between 10 and 170 degrees. The window is unchanged. A near-planar axis (for example the racemisation transition state) is still not read and stays `?`, which `species_entry_stereo_unresolved` reports.
- A `stereo_label` of `Ra` or `Sa` is accepted for rings of 6, 7 and 8 (it was `species_stereo_label_invalid`). A label that contradicts the geometry is `species_stereo_contradiction`, as for any allene.
- `species_entry_stereo_not_represented` still covers a cyclic allene in a ring of **five or fewer** atoms (it was eight or fewer): the reason `axial_small_ring` and its sentence now say five. Rings of 4 and 5 stay `?` and refuse `Ra`/`Sa` labels because no geometry was checked for them.
- A chirality mark in the SMILES of an allene in a ring of 6, 7 or 8 atoms (for example `C1=[C@]=C(F)CCC1`) is now read as well; it was ignored while those rings were never settled. Marks on allenes in rings of five or fewer stay ignored.
- Entries already stored with `?` for such a ring are not changed; deposit a geometry or a `stereo_label` to settle them.

## 0.147.0 - 2026-11-02

An allene's chirality mark in a SMILES is now read. No field is added or removed.

- A mark on the central atom of an allene (`[C@]` / `[C@@]` between the two double bonds, as in `NC(Br)=[C@]=C(O)C`) now sets the axis token of the entry: `Ra` or `Sa`. A deposit with such a mark and no geometry is `resolved` for that element and earns no warning. A mark that contradicts a geometry or a supplied `stereo_label` is `species_stereo_contradiction`, as for any other element.
- `/` `\` marks on a cumulene with an odd number of double bonds are still not read.
- The `not_represented` reason `axial_marks` now means only: a mark on an allene that could not be read (the text is not understood or does not match the molecule). Its sentence says so.

## 0.146.0 - 2026-11-01

An accepted deposit can now earn one more warning, and the warning codes have a reference of their own. No field is added or removed.

- New warning `scan_point_coordinate_disagrees_with_geometry`. A scan point's `coordinate_values[].coordinate_value` is the internal coordinate itself, measured on that point's own geometry, in the coordinate's own unit (a bond length in angstrom, an angle or dihedral in degrees), never an offset from `start_value`. When the stored values of a scan coordinate disagree with that coordinate measured on the points' geometries, the scan is accepted as sent and the response carries one warning per coordinate series. `context` has `scan_number` (the scan's position among the request's scans, from 1), `coordinate_index`, `coordinate_kind`, `pattern` (`consistent_with_legacy_relative_axis`, `uniform_one_step_offset`, `linear_ramp`, `single_outlier`, `small_constant_offset`, `no_pattern_detected` or `insufficient_data`), `n_points_disagreeing`, `n_points_checked`, `max_residual` and `tolerance`. A scan whose values were measured from the scan's start (a relative sweep) is the usual cause; send the coordinate itself. Values that are merely rounded (the tolerance is widened to half a step of the stored decimals, unless the stored values are an evenly spaced grid, gaps allowed, which are labels and are judged tightly; declare `step_size` for a grid whose step is finer than 5 units of its last decimal, otherwise it is judged as rounded values) are not a disagreement, and a constant offset no larger than 1 degree (0.1 angstrom for a bond) is `small_constant_offset`, which names no cause. It is placed on the scan calculation's record.
- New file `tckdb_schemas/contract/WARNING_CODES.md`, shipped beside `REFUSAL_CODES.md`: every warning code an accepted deposit can return, with TCKDB's sentence and the scientific check register entry behind it, if any. Find it with `python -m tckdb_schemas.contract --warnings` or `tckdb_schemas.contract.warning_codes_path()`; read it with `warning_codes_markdown()`.
- `species_stereo_contradiction`, `term_symbol_mismatch`, `electronic_state_contradicts_method`, `freq_list_incomplete_for_geometry` and `network_state_energy_sources_partial` are now covered by scientific check register entries (51, 12, 12, 22 and 25). Their behaviour is unchanged.

## 0.145.0 - 2026-10-31

A warning's `message` is TCKDB's sentence for its `code`, never text the depositor typed. No field is added or removed.

- `UploadWarning.from_code(field=, code=, context=)` is the one way to build a warning; its `message` is `render_message(code, context)`.
- New module `tckdb_schemas.deposit_warning_messages` (`RENDERED`, `render_message`, `normalise_field`), moved from the backend. The sentence for a code is fixed, or built from `context` values (numbers, counts, TCKDB tokens).
- A label that helps you find the problem is under `context["depositor_supplied"]`, a dict of named strings you typed. Nothing in the sentence is read from it. `field` is unchanged.
- `StationaryPointFinding` gains `context` (numbers the finding was decided on), used for the warning built from a warn-tier finding. Blocking findings and their messages are unchanged.
- Match on `code`, never on `message`.
- Every remedy and consequence the old messages carried that needs no depositor text is kept in the sentence; tau's reason is rendered from the `tau_basis` token. `context.depositor_supplied` holds strings, lists of strings and lists of records of strings only; an entry that holds nothing is dropped.
- `UploadWarning.from_stored(data)` rebuilds a recorded warning's message from its code and context. `deposit_warning_messages.context_problems` states what a context may hold (every string outside `depositor_supplied` sits under a listed key).
- `normalise_field` replaces every bracket that is not a plain integer position, quoted or not, robust to embedded quotes.
- `context_problems(code, context)` holds every string outside `depositor_supplied` to a closed set per key; a context's `depositor_supplied` of a wrong shape is logged and dropped by `from_code`.
- `TauResolution` gains `assumed_software`, `assumed_family` and `assumption_rule` (the assumption table's own tokens); tau and its basis are unchanged.
- `StationaryPointFinding.context` is added; the `tau_basis` token and mode counts ride in it. `extra_imaginary_mode_count` is the count in all three transition-state tau codes.

## 0.144.0 - 2026-10-31

An unrecorded `freq.hessian_method` now resolves `tau` through the producing program's assumed default when the deposit is judged, not only when it is stored. No field is added or removed.

- `tau` is resolved from, in order: the Hessian method you record (`freq.hessian_method`, with `grid.quality` and `opt.convergence`); else the assumed default of the calculation's own software and level-of-theory method; else 50 cm-1 with `protocol_not_recorded`.
- The assumed defaults are the 2026-09-04 table: Gaussian HF, DFT, MP2, CIS, CASSCF and ORCA HF, DFT resolve to the analytic default, 30 cm-1 (`assumed_analytic_default`); Gaussian MP3, MP4, QCISD, CCSD, CISD, CID and ORCA MP2, coupled cluster, DLPNO resolve to finite differences of gradients, 50 cm-1; Gaussian CCSD(T) and QCISD(T) resolve to finite differences of energies, 80 cm-1. CIS is matched exactly: CISD and CID have numerical frequencies only. Any other program, or a method family the table does not list, keeps 50 cm-1.
- A `freq.hessian_method` that is present but not recognised (for example `numer`) keeps 50 cm-1 / `protocol_not_recorded`; it is never replaced by an assumption.
- Input decks are read before judging. A deck attached inline (`artifacts` of kind `input` on a bundle calculation: computed-species, computed-reaction, network) is parsed for Gaussian `Freq=Numer` / `EnOnly` and ORCA `NumFreq` / `AnFreq`, and the method it states beats an assumed one. Order: recorded `freq.hessian_method`, the deck, the assumed default, 50 / `protocol_not_recorded`. An ORCA DFT minimum run with `NumFreq` and a -40 cm-1 mode is accepted at 50 with the recorded `finite_difference_gradient` basis; with no deck it is refused at the assumed 30. A deck with no frequency directive says nothing.
- New refusal `hessian_method_contradicts_input_deck` (422): the recorded `freq.hessian_method` and the deck state different methods (analytic, `finite_difference_gradient` or `finite_difference_energy`; any two different parsed methods, because their tolerances differ). The message carries only TCKDB's words, the method tokens; `context` has `recorded`, `input_deck` and `calculation_key`.
- Paths that cannot see a deck at validation keep their behaviour: the standalone calculation, conformer and transition-state payloads have no `artifacts` field, and a deck uploaded afterwards through the artifact route is read only by the workflow's parameter extraction.
- ORCA release gate: before ORCA 5, meta-GGA functionals (M06 family, TPSS, SCAN and r2SCAN family, and similar) have no analytic Hessian and are assumed `assumed_finite_difference_gradient` (50); ORCA 5 and later, and plain GGA and hybrid functionals on any version, stay analytic (30). With no usable ORCA version a meta-GGA gets no assumption (50 / `protocol_not_recorded`). ORCA double-hybrid and VV10/non-local functionals (`-V`, `-NL`) are assumed `assumed_finite_difference_gradient` (50) on any version, known or not, because the ORCA 5.0.4 and 6.1 manuals document numerical frequencies for them. Source notes are in `hessian_method_assumption.py`.
- ORCA `NumFreq` together with `NumGrad` on the deck's keyword lines is a Hessian built from numerical gradients, i.e. from energies: `finite_difference_energy` (80), the value ARC records. `NumFreq` alone stays `finite_difference_gradient`. The backend ORCA parser stores the same canonical value for its `freq.hessian_method` row. Source: ORCA 5.0.4 manual, 8.3.2 and 9.24.5 \"Numerical Gradients\" (`! NumGrad` requests numerical instead of analytic gradients).
- Gaussian double-hybrid functionals (B2PLYP, mPW2PLYP, DSD-*, revDSD, PWPB95, QIDH, PBE0-DH, B2GPPLYP, wB97X-2) get no assumption (50 / `protocol_not_recorded`): the Gaussian documentation available to us names analytic frequencies for HF, DFT, MP2, CIS and CASSCF and does not settle double hybrids. A recorded method still wins.
- Deck reading, precisely: Gaussian `Freq`/`Frequency` in every spelling (`Freq=Numer`, `Freq=(..)`, `Freq(Numer)`, `Freq (Numer)`, `Freq = Numer`); `EnOnly` wins whenever present, otherwise `Numer`/`Numerical`; keywords that merely contain the letters (`FreqChk`) and other keywords' options (`SCF=(Numer)`, `Opt=CalcNumerFC`) are not read. ORCA `NumFreq` wins over `AnFreq`; a `#` comment runs to the next `#`, so keywords after the closing `#` count. The backend parsers store one `freq.hessian_method` row from this shared reading.
- The family classifier now recognises DSD-*, revDSD, PWPB95, mPW2PLYP, B2GP-PLYP, PBE-QIDH, pr2SCAN50 and similar double hybrids, and mPW1B95, BB1K, mPW1KCIS and rSCAN, as DFT so they reach their rows: ORCA double hybrids are `assumed_finite_difference_gradient` (50) as decided; Gaussian double hybrids still get no assumption.
- New modules `tckdb_schemas.hessian_method_detection` (the deck readers and keyword tables the backend parsers now import) and `tckdb_schemas.ess_software_detection` (moved from the backend).
- This loosens one band: Gaussian CCSD(T) and QCISD(T) are judged at 80 cm-1, so a minimum between 50 and 80 cm-1 that was refused is now accepted with `minimum_imaginary_modes_below_tau`, and a transition-state extra mode in that band is no longer flagged.
- A recorded method always wins over an assumption.
- Effect on a minimum: a Gaussian DFT minimum with a -40 cm-1 mode and no recorded Hessian method was accepted with `minimum_imaginary_modes_below_tau` at 50 cm-1. It is now refused as `n_imag_contradicts_minimum`, because 40 is at or above 30. Record `freq.hessian_method` (for example `Freq=Numer`) to be judged at the method you ran, or declare the entry as what it is.
- Effect on a transition state: the extra-imaginary-mode flag is judged at the same tau that is stored with the record. A flag is never a refusal. A -40 cm-1 extra mode on a Gaussian DFT saddle is now flagged (it was judged at 50 and stored at 30, so the record disagreed with its own flag).
- The stored `imaginary_mode_tau_cm1` and basis are exactly the tau and basis the deposit was judged at.
- New module `tckdb_schemas.hessian_method_assumption` owns the table (`infer_hessian_method`, `resolve_tau_with_assumption`). The backend imports it.

## 0.143.0 - 2026-10-29

`UploadWarning` gains an optional `context`. No other field is added or removed.

- `UploadWarning.context` (`dict | None`, default `null`) holds the values that triggered the warning as the check saw them, for example the declared and
  the logged charge for `charge_mismatch`. A check that states none leaves it `null`. It never carries a database id.
- The upload response is otherwise unchanged. A client that reads `field`, `code` and `message` keeps working; every warning now also carries a `context` key.
- The server stores each warning beside the record it concerns and shows it on that record's reads (`record_warnings`); see the backend changelog.

## 0.142.0 - 2026-10-29

A species entry declared a minimum is judged by the size of its imaginary modes against the protocol's tolerance, instead of being refused for any imaginary mode. One warning code joins the contract. No field is added or removed.

- `minimum_imaginary_modes_below_tau` (a warning, not a refusal): every imaginary mode on an entry declared a `minimum` is below `tau`. The record is accepted. The same record was refused as `n_imag_contradicts_minimum` before.
- `tau` is the one the transition-state rule already uses, resolved per calculation from the parameters you deposit (`freq.hessian_method`, `grid.quality`, `opt.convergence`): 15, 30, 50 or 80 cm-1 by protocol, and 50 cm-1 when the protocol is not recorded. A -15 cm-1 mode on a minimum with no recorded protocol is now accepted.
- A mode at or above `tau` is refused as before (`n_imag_contradicts_minimum`), and so is a list where any mode is at or above `tau`. Counted modes whose magnitudes were not deposited are refused too: a mode nobody measured is never assumed soft. Deposit the frequency list (`freq_result.modes`) or, for one imaginary mode, `imag_freq_cm1`.
- The refusal and the warning name the `tau` that decided them. `evaluate_species_entry_frequency` gains the optional keywords `imaginary_modes` and `tau`; without them a lone `imag_freq_cm1` with `n_imag == 1` is judged against the "protocol not recorded" row.
- A species entry declared a `minimum` that designates a reaction coordinate (`reaction_coordinate_mode_index`) is refused as `n_imag_contradicts_minimum`: a minimum has no reaction coordinate. A deposit with one imaginary mode was refused before this change as well. The refusal text also no longer calls unmeasured modes a higher-order saddle, and names a single real mode among soft ones as a transition-state case.
- Judged per frequency calculation: every frequency calculation on the entry is checked, not just the first that reports `n_imag`.
- The escape hatches for a mode at or above `tau` are unchanged: declare `species_entry_kind='vdw_complex'`, or deposit the structure as a transition state.
- A van der Waals complex keeps its own warning (`n_imag_contradicts_minimum` as a warning); the transition-state rule is unchanged.
- The server stores the `tau` that decided it on the frequency result (`imaginary_mode_tau_cm1` and its basis) and stores `imaginary_mode_structural_flag` as `false` (judged and not flagged). No column is added.

## 0.141.0 - 2026-10-29

An allene's axis and an odd cumulene's cis/trans are now part of the stereo descriptor. No field is added or removed.

- `stereo_label` (and the stored `stereo_descriptor`) accept two more whole tokens: `Ra` and `Sa`, the axis of an allene (or any cumulene with an even number of
  double bonds). `Ra` is IUPAC's `M` (a negative torsion between the two CIP-higher substituents), `Sa` is `P`. The pattern is now
  `(Ra|Sa|[RSrsEZez?-])(,(Ra|Sa|[RSrsEZez?-]))*`.
- A cumulene with an odd number of double bonds (a butatriene and longer) takes `E` or `Z`, as a double bond does.
- A supplied label may be `Ra`, `Sa` or `?` at an allene axis, `E`, `Z` or `?` at an odd cumulene; anything else is `species_stereo_label_invalid`. A label that
  contradicts the geometry is `species_stereo_contradiction`.
- The tokens are read from geometry only. SMILES marks on an allene, and `/` `\` marks on a cumulene, are not read. A cyclic allene in a ring of eight or fewer
  atoms is never read, whatever the geometry.
- `species_entry_stereo_not_represented` now means only: the symmetry cap, a cyclic allene in a ring of eight or fewer, a centre or double bond whose
  stereogenicity depends on an allene's sense, a cumulated chain with a tied pair of stereo-bearing arms at one end, and an allene or cumulene given only as SMILES marks. A `?` that a geometry or a label could settle is
  `species_entry_stereo_unresolved`.

## 0.140.0 - 2026-10-28

A network channel's `kind` is derived from the two states it joins. One refusal code joins the contract.

- `kind` on a PDep channel is optional. Omit it and the server fills it in; supply it and it must agree. well to well is `isomerization`, bimolecular or
  termolecular to well is `association`, well to bimolecular or termolecular is `dissociation`, and bimolecular or termolecular to bimolecular or termolecular is
  `exchange` (including a stereo isomerisation of a fragment set, and well-skipping channels). `tckdb_schemas.network_channel_kind.derive_channel_kind` is the rule.
- `network_channel_kind_mismatch` (422): a supplied `kind` disagrees with the kind derived from the channel's states. The detail names the channel's local key, the
  supplied kind and the derived kind.
- `stabilization` is removed from `NetworkChannelKind`; nothing used it. A payload that sends it is refused as an unknown enum value.

## 0.139.0 - 2026-10-27

Three refusal codes join the contract. No field is added or removed.

- `network_state_duplicate_participant` (422): one state of a PDep network names the same species entry twice (two species keys that resolve to one entry). It used
  to be an HTTP 500. Name the species once with its `stoichiometry`, or say what differs (stereo marks, a stereo label or a geometry).

- `reaction_entry_null_reaction` (422): a reaction's reactants and products are the same species entries and the deposit carries no transition state or
  kinetics record. A reaction between two entries of *one species* (a cis-trans isomerisation, `Z-2-butene -> E-2-butene`) is valid; give each side the
  stereo (marks, a stereo label or a geometry) that tells the isomers apart. A degenerate exchange (`H + H2 -> H2 + H`) is accepted when deposited with its
  transition state (transition-state upload, or `transition_state` in a computed-reaction bundle) or a kinetics record (kinetics upload, or `kinetics`).
- `network_state_duplicate_composition` (422): two states of one network resolve to the same species entries (two species keys that name one entry, or a
  state declared twice). It used to be an HTTP 500.

## 0.138.0 - 2026-10-26

`stereo_label` on a species entry is a stereo descriptor, not free text. Species identity no longer includes stereochemistry (DR-0038).

- **Behaviour change.** `SpeciesEntryIdentityPayload.stereo_label` accepts only descriptor tokens: `R`, `S`, `r`, `s` (pseudo-asymmetric), `E`, `Z`, `e`, `z` (pseudo-asymmetric double bond), `-` (not stereogenic here) or `?` (unknown), comma-separated,
  one per potential stereo element of the molecule in canonical order (`E`, `R,S`, `E,?`). Free text such as `"trans"` is refused at validation. The field
  is no longer a key that wins over everything: the server fills it only into elements the geometry and the SMILES stereo marks leave unresolved, and
  cross-checks it where they exist. `max_length` widens from 64 to 512 (one token per element).
- Server behaviour a depositor sees (no field change): `C/C=C/C` and `CC=CC` are one species and the stereoisomers are entries of it; SMILES marks,
  geometry and label that name different configurations are refused with `species_stereo_contradiction` (422); a label that does not fit the molecule is
  refused with `species_stereo_label_invalid` (422); an entry with any element still unknown is accepted with the `species_entry_stereo_unresolved` warning.
  An allene's axis and cis/trans across a longer cumulene are stereo elements the vocabulary cannot state yet: they are always `?` (a letter there is refused
  as `species_stereo_label_invalid`), the deposit is accepted, and the warning is `species_entry_stereo_not_represented`, a code of its own because no further
  data would settle it and both mirror-image forms of an allene share the entry.
- Read models: `SpeciesEntryScientificRecord` and the structure-search record carry `stereo_descriptor`, `stereo_resolution` and `stereo_smiles` beside
  `stereo_label`. Structure search takes `isomeric` (exact mode) to match an entry's configuration.
- The descriptor follows CIP codes: for a sigma-vinyl radical the unpaired electron ranks lowest, so `Z` has the hydrogen on the radical carbon cis to the
  substituent on the other carbon. A name taken from the precursor halide can carry the opposite letter.
## 0.137.0 - 2026-10-25

An ensemble member's relative energy states its convention.

- `ModelMember.relative_energy_convention` (optional, new): what is already folded into `relative_energy_kj_mol`. It uses the existing
  `EnergyCorrectionConvention` vocabulary that network state energies use.
- It is required with an energy and refused without one, and a convention without an energy is refused: the two are stated together or
  not at all. Nothing is assumed. `other` is not accepted for a member (it would need a note, and a member has none; the shared enum lists `other`, but it is always refused on members).
- All members of a declaration that state an energy share one convention, and when the recipe states `energy_zero` the convention agrees with it (`electronic` with `electronic_only`, `e0` with `electronic_plus_zpe`); mixed or contradicting conventions are refused.
- Refused at the route as `request_validation_error` (the declaration's existing request validation); a declaration that reaches the
  service unvalidated is refused there as `statmech_model_declaration_invalid`. No new refusal code.

## 0.136.0 - 2026-10-25

A kinetics record can state the bath gas it was obtained for. Additive: an upload that sends none is unchanged.

- `BundleKineticsIn.bath_gas` (`/uploads/computed-reaction`): a list of `{species_key, mole_fraction}`, each key naming a species of the bundle.
  `KineticsUploadRequest.bath_gas` (`/uploads/kinetics` and a kinetics contribution bundle) is a list of `{species_entry, mole_fraction}`.
- Three refusal codes (422): `kinetics_bath_gas_at_high_pressure_limit`, `kinetics_bath_gas_mole_fraction_sum` (fractions sum to 1 within 1e-9, as for a
  network solve's bath gas) and `kinetics_bath_gas_duplicate_species`. The shared check is `tckdb_schemas.fragments.kinetics_bath_gas.check_kinetics_bath_gas`.

## 0.135.0 - 2026-10-25

One refusal code joins the contract. No field is added or removed.

- `calculation_dependency_cycle` (409): a calculation dependency whose parent is already reachable from its child is refused; `context` names both calculations by public reference.
- A bundle whose `depends_on` edges form a ring is refused at validation (a 422 naming the depositor's own calculation keys), before any write, on the sync and async paths. The check is a validator, so no field changes.

## 0.134.0 - 2026-10-24

Three refusal codes join the contract. No field is added or removed.

- `check_inputs_not_supported` (422): `check_inputs` was sent to a route that does not compare inputs; it is refused, not ignored.
- `assessment_input_check_limit` (422) and `check_inputs_needs_assessments` (422): the opt-in `check_inputs=true` on the thermo and kinetics lists compares each
  stored assessment's inputs with today's, so it needs `include=assessments` and at most 10 records.

## 0.133.0 - 2026-10-24

One refusal code joins the contract. No field is added or removed.

- `lowest_energy_across_levels` (422): a `/lookup` request with `selection=lowest_energy` matched calculations at more than one level of
  theory. Absolute energies from different levels are never compared, so the lookup refuses (it used to warn and return a winner). The response
  names the levels found; the remedy is to filter to one level or to use a select endpoint.

## 0.132.0 - 2026-10-23

Four refusal codes join the contract. No field is added or removed.

- `browse_parameter_removed` (422): `collapse` or `selection_policy` was sent to a read or an export. Neither exists; the response names the
  select endpoint that answers "give me one".
- `parameter_renamed` (422): `temperature_min` / `temperature_max` on the thermo and kinetics reads are now `coverage_temperature_min_k` /
  `coverage_temperature_max_k`.
- `export_selection_ambiguous` (422) and `export_ref_unmatched` (422): the CHEMKIN export holds one record per species or reaction and never
  picks; it refuses with the ambiguous subjects and their candidate refs, or with the named refs that matched nothing.

## 0.131.0 - 2026-10-21

Review fixes to versioning PR-4. No field is added or removed; a version field is stricter.

- `ExecutionEnvironmentManifestPayload.schema_version` is refused unless it is exactly the integer `1`. Pydantic matches a `Literal[1]` by
  equality, so `true` and `1.0` were accepted before; they are not now (the JSON Schema is unchanged: still the constant `1`).
- New `tckdb_schemas.fragments.version.ExactInt`, the validator that does this for any version field written as a `Literal`.
  `VersionRef.version` was already a `StrictInt`.

## 0.130.0 - 2026-10-20

**Breaking, once: every stored or wire string that fused a name with a version is now a name beside an integer version, and every TCKDB-owned
version restarts at 1 (versioning PR-4).** Versions that belong to somebody else (an ESS release, a producer's own parser, NIST's ThermoML) are
untouched and stay text.

What changes for a producer:

- `ExecutionEnvironmentManifestPayload` (`runtime`/`software_release`/`executable`/`closure` unchanged): `schema_version: "tckdb.execution-environment.v1"`
  becomes two fields, `schema_name: "tckdb.execution-environment"` and `schema_version: 1` (an integer). The manifest's `content_digest` changes with it
  (the canonical payload carries both fields); the server recomputed the stored digests.
- Parser stamps: `parameters_parser_version: str` on a calculation becomes `parameters_parser: {name, version}` (`ParserStamp`, both text, the version
  exactly as the parser's owner spells it), and a Hessian's `parser_version: str` becomes `parser: {name, version}`.
- `bundle_version` on a contribution bundle is the integer `1` (it was the string `"0.1"`); `bundle_format` is unchanged.
- New `tckdb_schemas.fragments.version.ParserStamp`, beside `VersionRef`.

Reads gain no new endpoints. `rubric_version` is an integer wherever it appears (`DeterministicTrustSummary`, the reproducibility summary), the trust
`evidence.rubric` is the bare rubric name, and document headers (`format: {name, version}`) replace the `schema` strings of the release, archive,
export, ML-export and deposit documents; see the backend CHANGELOG for the full map.

## 0.129.0 - 2026-10-20

The contribution bundle surface is now named `ContributionBundle` in the shipped producer contract: the generated JSON Schema file is
`contract/schemas/ContributionBundle.schema.json` (it was `ContributionBundleV0.schema.json`), its `title` is `ContributionBundle`, and
`PRODUCER_CONTRACT.md` refers to the surface by the new name. The bundle's wire fields are unchanged: `bundle_format` is still
`tckdb-contribution-bundle` and `bundle_version` is still `"0.1"`, so a producer's bundle documents validate exactly as before. Only a
consumer that opens the old schema file name, or matches the old surface name, has to change.

## 0.128.0 - 2026-10-20

One new refusal code in the shipped producer contract, `unknown_manifest_schema` (409): a release manifest row names a format tag this
build has no renderer for, so the manifest operation (render, verify) is refused instead of guessing a shape. It can only be returned by
the release read and verify routes, not by an upload; no producer route and no upload payload changes.

## 0.127.0 - 2026-10-20

Twelve new refusal codes, all prefixed `structure_finding_`, and one new curator route in the shipped producer contract:
`POST /api/v1/curation/structure-evidence-findings` (a curator or admin appends a structure-evidence finding). It is a curator route, not a
producer route, so no upload payload changes; this release only adds the route and the codes to the shipped contract documents.

## 0.126.0 - 2026-10-19

One new model, `tckdb_schemas.fragments.version.VersionRef`: a stable `name` and a separate integer `version` (at least 1, a real
integer, never text). A name that already contains a version (`computed_thermo_v2`, `tckdb.archive.v1`, `manifest_0_3_0`) is refused,
because the version belongs in its own field. A name may use upper case and hyphens (a scientific rule id such as
`K-XYG3-B3LYP-BARRIER` is a name), and chemistry spellings such as `cc-pV5Z` or `VV10` are not read as versions. `embeds_version` and the shared patterns it uses are exported so the repository's guard
test and the model cannot disagree about what counts. No producer route and no upload payload changes; nothing in the existing
wire contract refers to the model yet.

## 0.125.0 - 2026-10-18

One new refusal code for the relative conformer energy comparison, `POST /scientific/species-entries/{ref}/conformers/relative-energies/compare`:
`relative_conformer_energy_compare_identical_protocols` (422; the candidate and the reference name the same level with the same effective recipe narrowing, so a
protocol would be compared with itself). A read route, so no producer route and no upload payload changes; this release only adds
the code to the shipped refusal-code reference.

## 0.124.0 - 2026-10-17

Three new refusal codes for the relative conformer energy read, `POST /scientific/species-entries/{ref}/conformers/relative-energies`:
`relative_conformer_energy_unsupported` (422; `context.reason` is `deferred_energy_kind` for enthalpy or Gibbs energy, or
`deferred_certificate` for a request for no minimum certificate), `relative_conformer_energy_population_too_large` (422; more than
500 visible conformer groups) and `relative_conformer_energy_scope_owner_mismatch` (422; `context.field` names a conformer group
that belongs to another species entry). A read route, so no producer route and no upload payload changes; this release only adds
the codes to the shipped refusal-code reference.

## 0.123.0 - 2026-10-13

One new refusal code, `superseded_record_not_approvable` (409): `PATCH /record-reviews/{type}/{id}` refuses moving a record that has
been replaced by a supersession edge back to `approved`. `POST /submissions/{id}/approve` does not return it: it leaves a replaced
member deprecated and lists it in the audit event. A curator route, so no producer route and no upload payload changes; this
release only adds the code to the shipped refusal-code reference.

## 0.122.0 - 2026-10-12

One new refusal code, `unknown_query_parameter` (422): `GET /scientific/statmech/search` now refuses a query key that is not a
declared filter instead of ignoring it. No producer route and no upload payload changes; this release only adds the code to the
shipped refusal-code reference.

## 0.121.0 - 2026-10-12

A geometry and a conformer may no longer disagree anywhere a calculation can state both (#635). Four new refusal codes:
`calculation_geometry_conformer_mismatch`, `transition_state_calculation_conformer_key`,
`transition_state_calculation_species_geometry` and `calculation_input_geometry_mismatch`.

Narrowing: payloads previously accepted are now refused. Exactly what is enforced:

- `calculation_geometry_conformer_mismatch` (422), on `/uploads/computed-reaction` and `/uploads/networks/pdep`:
  - a species `calculations[*]` entry whose `conformer_key` names one conformer and whose `geometry_key` names a geometry that
    belongs to another conformer of that species (anchored to the `conformer_key`'s conformer until now);
  - a conformer's own `calculation` (its primary) that states a `conformer_key` other than its conformer's `key`, or a
    `geometry_key` other than its conformer's geometry key. Until now the geometry key stored the output geometry on the other
    conformer's geometry row and the `conformer_key` was ignored. Stating the conformer's own keys, or neither, is unchanged.
    A key that names nothing keeps its own code (`conformer_key_undeclared`, `geometry_key_unresolved`), which a primary's
    `conformer_key` never had before.
  - A geometry key that two conformers both declare is left to the existing "geometry keys must be globally unique" rule.
  - `context` is `{field, calculation_key, conformer_key, geometry_key, geometry_conformer_key}`, local keys only.
- `transition_state_calculation_conformer_key` (422), on the same two routes: a `conformer_key` on a transition state's
  `calculation` or any of its `calculations` (a transition state has no conformers; the key was dropped).
- `transition_state_calculation_species_geometry` (422), on the same two routes: a transition-state calculation whose `geometry_key`
  names a species conformer's geometry (a saddle-point job cannot have run on a minimum). On `/uploads/computed-reaction` this
  was accepted.
- `calculation_input_geometry_mismatch` (422, raised by the workflow, where geometry rows are known): a `freq` or `sp` that states its
  geometry (`geometry_key` on `/uploads/computed-reaction`; the geometry of its conformer for a calculation inside a
  `/uploads/computed-species` conformer or an additional calculation of `/uploads/conformers`; the saddle point's geometry for an
  additional calculation of `/uploads/transition-states`) and also declares `input_geometries` that do not include that geometry, compared as
  the same structure (the rule energies are grouped by: a moved copy of an atom or a rigidly moved polyatomic structure is the same). An `opt` is exempt (its input is a starting guess). Until now the explicit inputs silently won.
  The comparison forgives only the rounding of the coordinates (about 1.7e-6 Angstrom at 6 decimals) and a rigid move or atom
  reordering of the same structure. Coordinates a program re-printed or symmetrised are a different structure and are refused:
  send the conformer's own xyz as the sp/freq input geometry.
- Unchanged: payloads whose keys agree, a lone `geometry_key` or `conformer_key`, a `geometry_key` naming no conformer's geometry
  (still `geometry_key_unresolved`).
- New helpers in `tckdb_schemas.bundle_source_rules`: `assert_calculation_geometry_matches_conformer`,
  `assert_conformer_primaries_agree`, `assert_transition_state_calculations_unanchored`,
  `conformer_key_by_unique_geometry_key` and `calculation_geometry_conformer_error`.

## 0.120.0 - 2026-10-11

The producer contract lists the two read routes of statmech selection (`POST .../scientific/statmech/select` and
`.../select/manifest`) and the refusal code `statmech_selection_scope_owner_mismatch` (422). No wire model changed.

## 0.119.0 - 2026-10-11

Review leftovers for stability sources, atomic warnings and network energy sources (#635, #631, #669, #744).

- Three new refusal codes. For an SCF stability `source_calculation_key` (#635): `scf_stability_source_cycle` (two verdicts
  that cite each other; `context.cycle` lists the keys in order, where it used to be an uncoded validation error) and
  `scf_stability_source_conformer_unanchored` (exactly one of the carrier and the named job is tied to no conformer, on
  `/uploads/computed-reaction`; it was already refused, late and as "a different conformer"). If neither is tied to a conformer
  the same-conformer rule cannot be checked, which is an absence: the link is stored as stated and the upload warns
  `scf_stability_source_conformer_unverified`. A transition state has no conformers and is never flagged. For `energy_ordering`
  evidence (#669): `ts_energy_ordering_source_type_mismatch` (an energy cited to a calculation of a type that does not carry it,
  such as an E0 from a single point; it was an uncoded validation error), sibling of `network_energy_source_type_mismatch`.
- Narrowing on `/uploads/networks/pdep` (#635): `scf_stability` no longer declares `source_calculation_key` at all, since that
  route cannot link it. A payload that sends the key, even as `null`, is now refused as an unknown field (`extra_forbidden`);
  before, `null` was accepted and a non-null key was refused by a custom message. Omit the key.
- Network energy sources (#669): a `well_freq` or `barrier_freq` source calculation must be a `freq`
  (`network_energy_source_type_mismatch`). A `composite` is now also a valid source of an `energy_ordering` energy, for
  an electronic energy and an E0 alike, as it already was for a network energy. A new warning,
  `network_energy_zpe_source_absent`, when a state or barrier energy stated with `electronic_plus_zpe`,
  `atom_and_bond_corrected` or `thermal_enthalpy_298k` cites only an `sp` or `opt` and the upload has no `freq` for that
  species or transition state.
- Warning codes (#631, #744): a single-atom `term_symbol` whose L or J is not the NIST ground term is now reported as
  `term_symbol_contradicts_ground_term`; `term_symbol_contradicts_multiplicity` is kept for a leading 2S+1 that disagrees
  with the declared multiplicity. The spin-orbit warning now says the spin-orbit-free value lies so many kJ/mol above the
  ground level. New `hessian_not_stored_not_frequency_calculation` (a `.hess` file sent for a calculation that is not a
  `freq`); an artifact whose log carries a force-constant block that yields no matrix now warns
  `hessian_not_stored_unparseable`, and inside a bundle every `hessian_not_stored_*` warning's `field` is
  `calculations['<key>'].artifacts`.

## 0.117.1 - 2026-10-11

Contract layout only (#686), plus a recorded limit.

- Shared blocks are keyed by the surfaces that own them: `Code group` and `Model group` headings and anchors are now a short
  hash of the owning surface set (`cg-3fa9c2`), independent of the order the surfaces are listed in, instead of a number by
  size and order, so adding a code no longer renumbers later groups. A link to a group of one names its only member.
- "Will be refused if" keeps a one-line gloss beside a code several surfaces share, as it already did for a code only one
  surface returns.
- A group's "Traced via" column names the surfaces that reach a code a way the rest do not (`(only on: ...)`).
- Recorded limit (#745): the same-order comparison that stops an energy duplicated on a moved copy has its own allowance of 50
  million work units per record, now charged 800 units per pair plus 30 per atom (it was 30 per atom, so a record of
  three-atom shapes could run it for about eleven seconds). Past the allowance a pair is compared only by the relabelling
  search, and failing that is counted as different.

## 0.117.0 - 2026-10-11

One new refusal code, `database_request_failed`.

- Any database error that is neither a conflict (`409`) nor an outage (`503`) -- a trigger that raises when a write
  commits, a malformed value the database refuses, a schema that has drifted -- now answers `500 database_request_failed`
  with the usual error envelope and no driver text, instead of an unformatted `text/plain` 500. Nothing from the request
  was saved. A retry of the same request meets the same refusal, so it is not a `503`.

## 0.116.0 - 2026-10-11

Breaking: the two producer write routes that took a row id in the path take the public ref only (#578).
`POST /api/v1/submissions/{submission_ref}/rights-attestations` takes the `sub_` ref (every upload, job and bundle
response returns it as `submission_ref`), and `POST /api/v1/calculations/{calculation_ref}/artifacts` takes the `calc_` ref
(`calculation_ref` beside `calculation_id`, or `calculation_key_refs` on a computed reaction). An integer id, in any spelling,
is refused with 422 `invalid_handle`; a ref of the wrong kind is still 422 `handle_type_mismatch` and an unknown ref 404
`handle_not_found`. The server's minimum supported `tckdb-client` is now 0.97.0, the first release that addresses a
calculation by its ref. No wire model changed; the route paths in the producer contract did.

## 0.114.0 - 2026-10-11

The producer contract lists three new statmech-selection rejection codes (`statmech_selection_bound_exceeded`,
`statmech_selection_unsupported`, `unknown_statmech_candidate_ref`). No wire model changed.

## 0.113.0 - 2026-10-11

A statmech record can declare the statistical model it supplies (`tckdb_schemas.statmech_declarations`), optional and stored as
made.

- `model_declaration` on `ConformerUploadStatmechPayload`, `StatmechInBundle`, `BundleStatmechIn` and the standalone
  `StatmechUploadRequest`: the scope (`atom`, `basin`, `explicit_equilibrium_ensemble`, or `ts_determination` for a
  transition-state record), who stands behind it (`literature` or `workflow_tool_release`), a recipe (energy zero, scale
  application and its contributions, vibrational scheme, projected and excluded modes, rotors and the modes they replace,
  symmetry, optical and electronic factors already counted, translational normalization, dividing surface, validity range), ensemble `members` (existing statmech records of
  the same species entry) and `sources` (a calculation by key or ref, imported literature parameters, or a frequency scale
  factor). A field left out is not stated: never unity, never projected, never counted. Version `1` only.
- New enums `StatmechModelScopeKind` and `StatmechModelSourceRole`; new refusal codes `statmech_model_declaration_invalid`,
  `statmech_model_declaration_version_unsupported`, `statmech_model_declaration_mismatch` and
  `unknown_structure_determination_ref`.

## 0.112.2 - 2026-10-11

Contract layout only: the refusal code reference now ships as its own file, `contract/REFUSAL_CODES.md`, beside
`PRODUCER_CONTRACT.md`, so the contract stays under its size ceiling (the reference was 104 KB of 686 KB).

- `PRODUCER_CONTRACT.md` keeps a short "Refusal code reference" pointer section, and every code link in it now reads
  `REFUSAL_CODES.md#c-<code>`. Each surface still lists its own refusal codes with the same one-line meanings; only the
  long per-code entries moved. Anchors are unchanged. A scientific check that names a code no producer route was
  traced to (an advisory flag, not a refusal) used to link an anchor that was never there; it is now named without a
  link.
- New `tckdb_schemas.contract.refusal_codes_path()` and `refusal_codes_markdown()`, and
  `python -m tckdb_schemas.contract --codes` (prints the path), mirroring `changelog_path()`.
- No model, enum, refusal code or rule changed.

## 0.112.1 - 2026-10-11

A network state energy stated as `electronic_plus_zpe` can now be held against an sp electronic energy plus a scaled
frequency ZPE, not only against a composite that stores an E0 (#693).

- `solve.state_energies[].source_calculation_keys[].zpe_calculation_key` (new, optional): the participant's own `freq`
  calculation whose zero-point energy is added to the energy of `calculation_key`. A non-`freq` calculation is refused
  with `network_energy_source_type_mismatch`; another species' freq with `network_energy_source_subject_mismatch`. A key
  that no calculation declares is refused like any undeclared calculation key.
- `solve.state_energies[].zpe_scale_factor` (new, optional, positive and finite): the factor the producer scaled the ZPEs
  with, to at least 4 decimals. The energy compared is `sum(stoichiometry * (E_sp + s * ZPE))`. Both fields are accepted
  only with `correction_convention: electronic_plus_zpe`.
- An atom's sp or opt counts as its E0 (no ZPE cited), read from the stored one-atom geometry of the cited calculation.
- With no `zpe_scale_factor` the state is stored as not compared (`zpe_scaling_unstated`): unity is never assumed, even
  when the unscaled sum agrees. A freq that stores no ZPE is `zpe_not_stated`; an sp and freq not at one geometry are
  `geometry_not_paired`; no ZPE is borrowed from a sibling calculation. A contradicted sum is refused with the existing
  `network_state_energy_sum_mismatch`, whose context gains `zpe_scale_factor`. No new refusal code.
- A `zpe_scale_factor` on a state where no participant cites a `zpe_calculation_key` is refused: it would scale nothing.
- `freq_zpe_hartree` (and the stored freq ZPE) is documented as the unscaled harmonic zero-point energy.
- Reads gain `zpe_calculation_ref` per state-energy source and `zpe_scale_factor` per state energy.

## 0.112.0 - 2026-10-11

New extrapolation formula `exponential_sqrt` for composite scheme terms: ORCA's SCF form
`E_SCF(X) = E_CBS + A exp(-alpha sqrt(X))` (`CompositeExtrapolationFormula.exponential_sqrt`,
`extrapolate_exponential_sqrt`). The term's `exponent` is ORCA's alpha and is required, so it is part of the scheme's
identity like an `inverse_power` exponent; the term reads the `reference` (or `total` of an HF level) component, like
`karton_martin_scf`. ORCA's optimised alpha: cc-pVnZ 4.42 (2/3) and 5.46 (3/4); pc-n 7.02 and 9.78; def2 10.39 and 7.88;
ano-pVnZ 5.41 and 4.48; saug-ano-pVnZ 5.48 and 4.18; aug-ano-pVnZ 5.12 (2/3). Reproduces the two SCF energies the ORCA
manuals print for water. Existing schemes and formulas are unchanged.

## 0.111.1 - 2026-10-11

Wording only: the refusal `energy_correction_scheme_frequency_level_not_applicable` and the `EnergyCorrectionSchemeRef`
docstring no longer say every kind other than the BAC kinds is "keyed on the energy level alone". A frequency level is
accepted only on `bac_petersson` and `bac_melius` for now; Arkane's isodesmic reference data is keyed on the
frequency//energy pair too, and the `isodesmic` kind will likely need it once it has defined content. No upload model,
enum or rule changed.

## 0.111.0 - 2026-10-11

A kinetics record's Arrhenius A-units are held to the order of the side its coefficient is a rate of (#695): the products for
a `reverse` record, the reactants for a `forward` one. A `net` record is not checked. A record that states no direction
could be either, and none is assumed: units matching EITHER side's order are accepted, units matching neither are refused
(`arrhenius_a_units_molecularity_mismatch`, whose message names both orders and asks for `direction`), and when the two
sides are the same size the check is unchanged. So the correct units of a reverse rate on a reaction with unequal sides
(the reverse of H2 -> 2H is bimolecular) are accepted, and the units of the wrong side are refused once `direction` is
stated. This covers the main line, every `multi_arrhenius` and PLOG entry, and `falloff.low_a_units` (one order above the
side). No upload model gained or lost a field.

## 0.108.1 - 2026-10-10

The producer contract now lists four models it had left out: `SchemeAtomParamPayload`, `SchemeBondParamPayload` and
`SchemeComponentParamPayload` (the inline parameter models of an energy-correction scheme) and `NetworkChannelMicroReactionIn`
(the `microreaction_paths` of a network upload `channels` entry). All four were always accepted; the generator read an unresolved
forward-reference annotation and dropped them. No upload model changed.

## 0.108.0 - 2026-10-06

The producer contract lists the six read routes of structure selection (`POST .../species-entries/{ref}/calculations/select`,
`.../conformers/select`, `.../transition-state-entries/{ref}/evidence/select`, each with `/manifest`). No upload model changed.

## 0.107.0 - 2026-10-05

New refusal code `structure_selection_manifest_too_large` (a structure decision whose replayable manifest exceeds its
bound is refused whole; service layer only). No upload model changed.

## 0.106.0 - 2026-10-05

New refusal codes `structure_selection_population_too_large`, `structure_selection_evidence_too_large`,
`structure_selection_traversal_too_deep`, `structure_selection_unsupported` and `unknown_structure_member_ref` (the bounds and
refusals of a structure assessment, service layer only). No upload model changed.

## 0.105.0 - 2026-10-05

A calculation can declare the recipe it actually ran, and a conformer or transition-state upload can declare structure
determinations (`tckdb_schemas.structure_declarations`), all optional and stored as made.

- `actual_protocol_declaration` on every calculation block (`CalculationPayload`, the bundle `CalculationIn` and
  `CalculationInBundle`): electronic state and root, spin treatment, relativistic treatment, effective core potential,
  core correlation, auxiliary basis, dispersion, solvation, constraints, material numerical approximations and the
  corrections the number includes. Each single fact is `known` (with a value), `unknown` or `not_applicable`; a fact
  left out is not stated, never read as a default or as gas phase. Version `1` only.
- `structure_determinations` on `ConformerUploadRequest`, `TransitionStateUploadRequest`, each `ConformerInBundle` of a
  computed-species upload and each `ConformerIn` and the `BundleTransitionStateIn` of a computed-reaction upload: a
  source-attributed claim about a geometry, conformer basin or saddle point that pins the upload's own calculations (by
  `key`) to the roles energy, geometry optimization, curvature, correction, connectivity and alternative characterization.
  The determination key (with its owner and source) is an identifier: stating it again with the same content resolves to the
  same determination, with different content it is refused.
- `ActualProtocolDeclaration.supporting_dois` is omitted (not stated) or a list, like the other list facts.
- `TransitionStateUploadRequest.primary_opt` and `additional_calculations` accept an optional `key` (new component
  `TransitionStateCalculationIn`), unique within the request, so a determination can name them. A plain
  `CalculationWithResultsPayload` built in Python is still accepted there (lifted, with no key).
- New enums `StructureDeterminationTargetKind`, `StructureDeterminationQuantity`, `StructureSourceRole`. New refusal
  codes `structure_declaration_invalid`, `structure_declaration_version_unsupported`, `structure_determination_invalid`
  and `structure_determination_mismatch`.

## 0.104.0 - 2026-10-04

New refusal codes `network_export_manifest_invalid`, `network_export_manifest_stale`, `network_export_choice_not_allowed`,
`network_export_representation_choice_invalid` and `network_export_unsupported_form`, and the producer contract's list of
read-only POST routes gains `POST /api/v1/scientific/networks/{network_ref}/kinetics/export-selected`. No upload model changed.

## 0.103.0 - 2026-10-04

The producer contract's list of read-only POST routes gains the two network selection routes
(`POST /api/v1/scientific/networks/{network_ref}/kinetics/select` and `.../select/manifest`). No upload model changed.

## 0.102.0 - 2026-10-04

New refusal codes `network_selection_population_too_large` and `network_selection_snapshot_too_large` (a network selection over a bound of the versioned engineering limits). No upload model changed.

## 0.101.0 - 2026-10-04

A network solve can declare its target, protocol and validation, and a fit its determination and representation
(`tckdb_schemas.network_declarations`), all optional, on `NetworkPDepUploadRequest`. New codes `network_declaration_invalid`,
`network_declaration_version_unsupported` and `unknown_network_solve_ref`. A declaration that contradicts the solve's own
bath, range, correction convention or units, or a fit's declared determination, is refused; alternates of one channel and
model kind must all declare their keys.

No wire model changes. Contract notes the read-only kinetics select endpoints.

## 0.99.0 - 2026-10-04

`BundleKineticsIn` can declare `energy_level_of_theory`, optional and stored as declared (as on `KineticsUploadRequest`). A
declared level that contradicts a linked energy calculation is refused with `kinetics_energy_level_contradiction`.

## 0.98.0 - 2026-10-04

The contract's "What changed" prints only the newest entries. Every entry now ships beside it as
`tckdb_schemas/contract/CHANGELOG.md`, which `--since` and `contract.changes_since` read. No upload model changed.
`contract.changes_since(version, text=...)`: `text`, when given, is now changelog markdown (`## <version>` headings),
not contract markdown.

## 0.97.0 - 2026-10-04

New refusal code `kinetics_selection_population_too_large` (a selection request over 500 visible records).

## 0.96.0 - 2026-10-04

Kinetics records can declare their determination, applicability and protocol (`tckdb_schemas.kinetics_declarations`),
all optional, on `KineticsUploadRequest` and `BundleKineticsIn` (which also gains `direction`). `reaction.reversible`
on `/uploads/kinetics` is optional: unstated is inherited or refused, never guessed (#598). New `kinetics_*` refusal codes and
`unknown_kinetics_determination_ref`, `unknown_network_channel`. A determination may carry a bundle-local `group`.

## 0.95.0 - 2026-10-04

A network state energy can name one source calculation per participant, and is held against their sum
(#678). One new optional field on `POST /uploads/networks/pdep`, one new block-tier refusal and two new
upload warnings. Existing payloads are accepted as before.

- **`solve.state_energies[].source_calculation_keys`** (new, optional): a list of
  `{species_key, calculation_key}`, one per participant of the state. Send it or the older
  `source_calculation_key`, never both (a plain 422). Each calculation passes the existing
  `network_energy_source_type_mismatch` type rule and must belong to *exactly* the participant it is
  listed beside, otherwise `network_energy_source_subject_mismatch`; a `species_key` that is not a
  participant of the state is refused the same way, at `...source_calculation_keys[j].species_key`.
  `source_calculation_key` is unchanged and still accepts the calculation of any one participant.
- **`energy_precision_kj_mol`** (new, optional, positive): the rounding unit of `energy_kj_mol`. State
  unrounded kJ/mol derived from hartree with 2625.499639, or state this field; precision is never
  inferred from the digits of the number. It is stored and read back.
- **`network_state_energy_sum_mismatch` (422, block).** When every participant of a state has a source,
  TCKDB compares the stated `energy_kj_mol` with `sum(stoichiometry * stored energy)` in three bands.
  Within the printed-precision tolerance `max(1e-6, 5e-7 * n)` hartree, `n = 1 + sum(stoichiometry)`, the
  energy **agrees**. Beyond it but within an honest-rounding allowance (half `energy_precision_kj_mol`,
  or half of 1 kcal/mol = 2.09 kJ/mol when not stated, per stated energy, plus `1e-6 * |energy|` on an
  absolute energy for the spread of hartree-to-kJ/mol constants) it is stored **not compared**
  (`stated_precision_unknown`) with a warning, so a value rounded to 0.1 kJ/mol, converted from kcal/mol
  to two decimals, or converted with 2625.5 or 627.509 x 4.184 is never refused; if you state
  `energy_precision_kj_mol` the rounding is accounted for and it agrees. Only beyond the allowance is it
  refused. Defined for `correction_convention: electronic_only` (an `sp`, `opt` or `composite`
  energy) and, only where every source is a `composite` storing an E0, `electronic_plus_zpe`.
  `energy_zero_convention: absolute` is compared directly. `lowest_state` and `entrance_channel` shift
  every state of the solve by one constant, so those are compared as *differences* between states
  (`n = 2 + sum(nu_i) + sum(nu_j)`); the state blamed is the outlier inconsistent with a majority of the
  others, and with two states `context` names both. `context` names the field, the state and both
  numbers; no database id.
- **`network_state_energy_sum_not_compared` (warning).** A sum that cannot be formed is never refused
  and never guessed: `atom_and_bond_corrected`, `thermal_enthalpy_298k` and `other` corrections
  (`convention_not_summable`), `electronic_plus_zpe` with an `sp`/`opt`/`freq` source
  (`zpe_not_in_source`), `separated_reactants` and `other` zeros (`energy_zero_not_comparable`), a
  source that stores no such energy (`stored_energy_not_stated`), a state alone on its shared zero
  (`no_second_state_on_the_same_zero`), and a stated number beyond printed precision but inside honest
  rounding (`stated_precision_unknown`). The outcome is stored with its reason. A state with no source
  at all is stored as not compared (`no_source_stated`) without a warning: nothing was claimed.
- **`network_state_energy_sources_partial` (warning).** A source on some but not all of a state's
  participants, which includes a single `source_calculation_key` on a multi-species state. It is
  accepted and read back as partial; no other participant's source is borrowed.
- **Reads.** `NetworkSolveStateEnergySummary` gains `sources[]` (`species_entry_ref`, `stoichiometry`,
  `calculation_ref`), `partial_sources`, `source_sum_comparison` (`agrees`, `not_compared` or null for a
  row deposited before the check), `source_sum_not_compared_reason` and `energy_precision_kj_mol`. `source_calculation_ref` is the
  older single slot, one summand of several on a multi-species state. Stored networks read as they did,
  with the new fields describing what they hold.
- Producers: the hydrazine ingester now cites every participant's single point for a multi-species state
  (`source_calculation_keys`) and a single participant's with `source_calculation_key`. A list on a
  one-participant state also fills the older single slot, so `source_calculation_ref` keeps the source.


## 0.94.0 - 2026-10-04

A Hessian's geometry, every scan point's geometry and every IRC point's geometry are now checked against the
subject their calculation is filed under (#680). No payload field is added, removed or changed.

- **Payloads that were accepted are now refused.** A wrong-element or wrong-isotope geometry at
  `hessian.geometry`, at `scan_result.points[N].geometry` or at `irc_result.points[N].geometry` (any point
  direction, including the TS-marker point and `both`) is refused with the existing codes
  `calculation_geometry_composition_mismatch` (422) and `calculation_geometry_isotope_mismatch` (422), the
  same ones already raised for input and output geometries; `context.field` names the path. Before, such a
  geometry was stored: a deuterated Hessian geometry under a protium species gave deuterium frequencies on
  reanalysis. The contract's per-route refusal lists are unchanged (the codes were already listed for these
  routes).
- A Hessian recovered from an uploaded artifact is checked too; there a mismatch never fails the upload, the
  Hessian is simply not stored.
- A species stored before the label-stripping change with isotope labels on its SMILES and no isotope key is
  read through that SMILES when its isotope content is compared.

## 0.93.0 - 2026-10-04

The same molecule with its atoms listed in another order is no longer a way round the no-optimisation
duplicate rule (#679, follow-up to #667). No field is added, removed or renamed; one payload that was
accepted is now refused, under the code the rule already used.

- **Two single points (or two composites) on one polyatomic structure are one duplicate whatever order
  the atoms are listed in (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes
  change).** With no `opt` linked, two `sp` (or two `composite`) links whose geometries are one structure
  moved rigidly are refused with `thermo_role_duplicate` / `statmech_role_duplicate` even when one lists
  its atoms in a different order, for example water with its hydrogens first, or the hydrogens of a CH3
  exchanged. The rule searches for a relabelling that lays one geometry on the other, using the
  geometries alone, and then applies the 0.80.0 comparison unchanged (Kabsch-aligned RMSD, tolerance from
  the precision of the coordinates). Only atoms of the same element and the same stated isotope are
  exchanged (`D`/`T` count as 2H/3H), and an enantiomer stays a different structure in any order.
- **The search is bounded, and past a bound a pair is treated as different** (the behaviour before this
  change): geometries over 200 atoms, a pair needing more than 256 trial alignments or examining more
  than 50,000 candidate placements, and a record's work beyond 4,096 alignments or 50 million work units
  are not decided. A duplicate energy on a reordered copy of such a structure is therefore still
  accepted. Ordinary molecules need one alignment.
- The same-order comparison of 0.80.0 and the one-atom rule of 0.74.0 are unchanged.

## 0.90.0 - 2026-10-03

No wire model changes. Contract notes the read-only thermo select endpoint.

## 0.89.0 - 2026-10-03

A `D`/`T` element spelling now means 2H/3H (#672, ADR 0022): **a `D`/`T` geometry on a protium species, previously accepted, is now refused**, `[2H]O[2H]` with it is accepted, and `isotopes` contradicting the spelling is refused as `geometry_isotope_symbol_conflict`.

## 0.87.0 - 2026-10-03

A geometry linked to a calculation is now checked against the isotopes of the subject the calculation is
filed under (#666). No payload field is added, removed or changed.

- New refusal code `calculation_geometry_isotope_mismatch` (422, ADR 0008 block), on every route that
  already runs `calculation_geometry_composition_mismatch`: thermo, statmech, conformer, computed
  species, computed reaction, transition state and network. The multiset of `(element, mass number)`
  substitutions on each calculation geometry (`geometry.isotopes`) must equal the one the species entry
  declares through its SMILES isotope labels, or, for a transition state, the sum over its reaction's
  reactants. Before, a deuterium geometry could be attached to a protium species and the reverse.
- The comparison is by count, not by atom: a calculation geometry carries no map to the species graph,
  so which atom carries a label is not checked. A `D` or `T` element spelling stays isotope-silent, as
  documented in `resolve_element_symbol`; only `geometry.isotopes` and SMILES labels count.
- Network and computed-reaction uploads: a transition state's `geometry.isotopes` is now read. It was
  accepted by the schema and dropped before the geometry was stored (contribution bundles take the
  computed-reaction route).
- A deposit whose calculation geometries carry no isotope labels under a species that declares some
  (for example `[2H]C` with an unlabelled CH4 opt input) is now refused; label the geometry.

## 0.86.0 - 2026-10-03

The layout of `PRODUCER_CONTRACT.md` changed; its content did not (#681). No field, rule, code or enum
changes. What a rule, a refusal code or a nested model says is now printed once and linked from each
surface that has it, instead of once per surface. Every surface still lists exactly the same refusal
codes, checks and nested models: a code or model several surfaces share sits in a numbered group
("Code group N", "Model group N") that each of those surfaces links, and a "Will be refused if" line
for such a code no longer repeats its sentence (the code reference has it).

## 0.85.0 - 2026-10-03

A thermo record can state what its values describe (`thermodynamic_target`) and how they were
produced (`protocol`, version 1): optional attributed claims, never inferred or defaulted, `null`
when omitted. Existing payloads are accepted unchanged. Fields and rules: `backend/schema_spec.md`,
"Thermodynamic target and protocol declarations". New codes (`thermo_target_group_*`,
`thermo_protocol_*`, `thermo_recipe_name_listed`, `thermo_declaration_invalid`) and the shared rule
`thermo_declaration_error`.

## 0.81.0 - 2026-10-03

TS energy-ordering energies are held against the stored energies (#638). No field is removed or changed;
`zpe_scale_factor` is added.

- **`ts_energy_ordering_stated_energy_mismatch` (422).** A stated energy that is not what TCKDB stores for
  its `source_calculation_key` is refused: `electronic` against the cited `sp`'s energy (or `opt`'s final
  energy), `e0` against the same participant's stored `electronic` energy plus the cited `freq`'s ZPE, when
  both are at one geometry. Tolerance `max(1e-6, 5e-7 * n)` hartree, n = 2 (electronic) or 3 (E0).
- **`zpe_scale_factor` (optional, `e0` energies only).** TCKDB stores your ZPE unscaled. An `e0` built as
  `E_electronic + s * ZPE` states `s` and is held to that sum (n = 2 + s + 100 * ZPE, which assumes `s` has
  at least four decimals: state it as multiplied, since 0.954 for a true 0.953649 can be refused). With no factor, an `e0` equal to `electronic + ZPE` agrees; any other is not refused
  but stored `not_compared` with reason `zpe_scaling_unstated`.
- **`transition_state_energy_ordering_not_compared` (warning).** An energy that cannot be compared (stored
  energy or ZPE not stated, no electronic energy to pair an E0 with, geometries not pairable) is accepted
  and reported, never read as agreement.
- Reads: compared energies gain `stored_energy_comparison`, `not_compared_reason` and `zpe_scale_factor`
  (null on earlier records). Uploads only.

## 0.80.0 - 2026-10-03

A rigidly moved copy of a polyatomic geometry is no longer a way round the no-optimisation duplicate
rule (#667, the polyatomic half of #623). No field is added, removed or renamed; one payload that was
accepted is now refused, under the code the rule already used.

- **Two single points (or two composites) on one polyatomic structure are now one duplicate, wherever
  the structure sits (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes
  change).** With no `opt` linked, two `sp` links (or two `composite` links) whose geometries are the
  same structure moved rigidly, translated and/or rotated, are refused with `thermo_role_duplicate` /
  `statmech_role_duplicate`; before, only the very same stored geometry was. "The same structure" is
  the same atoms in the same order (element and stated isotope) whose Kabsch-aligned RMSD is within
  the rounding of the coordinates as deposited (1.7e-6 Angstrom for coordinates written to six
  decimals, never more than 1.7e-4 Angstrom). An enantiomer is a different structure (a mirror image counts as the same only when a rotation superposes it atom for atom). A genuinely
  different geometry, such as another conformer or a bond length changed by more than the rounding,
  is unchanged and still accepted. The same atoms listed in a different order are still treated as
  different geometries (no canonical atom order exists for a bare geometry; deferred).
- The one-atom rule of 0.74.0 is unchanged.

## 0.79.0 - 2026-10-03

Network solve energy sources must belong to the subject they state an energy for (#668). No field or
enum member changes; the `source_calculation_key` description of a state energy and of a channel
barrier gains a sentence, and one new refusal code joins the catalogue.

- **`network_energy_source_subject_mismatch` (422).** `POST /uploads/networks/pdep` refuses a source
  cited for the wrong subject: a `state_energies[].source_calculation_key` that is not a calculation of
  a species of that state (any one of them for a bimolecular state), a `channel_barriers[]` source that
  is not a calculation of the barrier's own transition state (a species single point is the case that
  used to be stored), a `well_energy` link that is not a calculation of a species in one of the
  network's states, and a `barrier_energy` link that is not a calculation of one of its transition
  states. `context` names the field, the expected and the found kind of owner and, for a state or a
  barrier, the key of the state or transition state; no database id. Producers that cite the single
  point of the species in the state, or of the transition state (every producer known to us, the
  hydrazine ingester included) are unaffected. Uploads only: stored networks read exactly as before.

## 0.77.0 - 2026-10-03

Composite levels of theory, phase P7b (ADR 0021): a read-shape note only. No upload payload field is
added, removed or changed, and every payload accepted before is accepted unchanged.

- Reads: `LevelOfTheorySummary.label` is now always the full rendered label of the level, a string,
  never `null`: `method/basis` then each stated part of its identity, for example
  `CCSD(T)/cc-pCVTZ (core=all_electron)`. It is the same text as the notation of a record's levels and
  the ML export's label. Before, it was `null` on most reads and a slash-joined `method/basis` on a few,
  so a caller that treated it as the short form should read `display` instead (unchanged: `method/basis`).
- Reads: `LevelOfTheoryCoreBlock.label` is new, with the same text. The level-of-theory detail and browse
  reads carry it.
- The producer contract is unchanged except for its version line and this entry.

## 0.76.0 - 2026-10-03

Network solve energy sources are typed (#642). No field or enum member changes; the `source_calculation_key` of a state energy and of a channel
barrier gains a description, and one new refusal code joins the catalogue so a producer branching on
codes can learn it.

- **`network_energy_source_type_mismatch` (422).** `POST /uploads/networks/pdep` refuses a source that
  cannot carry the energy it is cited for: `state_energies[].source_calculation_key` and
  `channel_barriers[].source_calculation_key` when stated as `correction_convention: electronic_only`
  (accepted from `sp`, `opt` or `composite`), and, for any convention and for the `well_energy` and
  `barrier_energy` roles of `source_calculations`, any calculation that is not an `sp`, `opt`, `freq` or
  `composite` (so an `irc`, `scan`, `path_search` or `conf` is refused). `context` names the field, the
  stated convention or role, the accepted types and the type found. Producers that cite a single point
  (every producer known to us) are unaffected. Uploads only: stored networks read exactly as before.

## 0.75.0 - 2026-10-03

The producer contract now records that `/bundles/submit` and `/bundles/dry-run` apply the
frequency-list linearity check to the records in a bundle, and that a bundle reports the upload
warnings each record earns. Nothing a producer sends changes and every payload accepted before is
accepted unchanged; this release only adds.

- **Bundle warnings.** `/bundles/submit` and `/bundles/dry-run` return, in `messages`, the same
  upload warnings (code, message, field, order) that `POST /uploads/thermo` and
  `POST /uploads/kinetics` return for the same record, each with the upload's `local_ref`.
  The producer contract's rule listing gains `ContributionBundleV0` as a place the linearity check
  applies.

## 0.74.0 - 2026-10-03

A shifted copy of an atom is no longer a way round the no-optimisation duplicate rule (#623).
No field is added, removed or renamed; one payload that was accepted is now refused, and one
refusal changes its code.

- **Two single points (or two composites) on one atom are now one duplicate, wherever the atom sits
  (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes change).** With no
  `opt` linked, two `sp` links (or two `composite` links) on one structure are refused with
  `thermo_role_duplicate` / `statmech_role_duplicate`. For a geometry of exactly one atom the
  structure is the element (`D` and `T` count as hydrogen) and its stated isotope mass number, not the
  stored geometry: a second `sp` on a copy of the atom at another
  coordinate (`1\nH\nH 1.0 0.0 0.0` beside `1\nH\nH 0.0 0.0 0.0`) used to be a different geometry and
  returned 201, and is now refused. A geometry of two or more atoms is still its own structure, so
  two `sp` links on genuinely different polyatomic geometries are unchanged. Two single points at two
  levels on a shifted atom were never accepted: they used to be refused as
  `*_energy_level_ambiguous` and are now refused as `*_role_duplicate`, which is what the same
  geometry at two levels has always returned. The message for an atom now says "per atom".
- **The rule is stated for polyatomic sp-only links too.** It has always applied to a polyatomic
  species whose linked calculations are `sp`s with no `opt`, including on the standalone
  `/uploads/thermo` and `/uploads/statmech` routes; the workflow guide `depositing_a_thermo_record.md`
  now says so.
- **The producer contract no longer lists `atom_map_geometry_unparseable` against surfaces that only
  count atoms.** The atom counter used by the frequency-list and one-atom-primary rules swallowed
  that refusal internally but was traced as if it raised it, so eight surfaces (computed species,
  conformer, kinetics, network pressure-dependent, reactions, statmech, thermo and transport) named a
  code they cannot return. It is still listed where the atom-map rules raise it: the computed-reaction
  bundle and the transition-state upload. `/uploads/reactions` no longer lists it. Nothing a producer
  sends changes for this item.
- `xyz_block_shape` is new in `tckdb_schemas.fragments.reaction_atom_map`: it splits an XYZ block into
  its declared atom count and coordinate lines and never raises. `parse_xyz_elements` is built on it
  and behaves as before.

## 0.73.0 - 2026-10-03

Composite levels of theory, phase P7a (ADR 0021): the wire package gains the closed-form
coefficients of the linear extrapolation formulas. Nothing a producer sends changes and every
payload accepted before is accepted unchanged; this release only adds.

- **`composite_formulas.extrapolation_coefficients(formula, cardinals, exponent)`.** The signed
  weights `c_i` with `E_CBS = sum_i c_i * E_i` for the linear formulas (`inverse_power`,
  `inverse_power_shifted_half`, `karton_martin_scf`), in ascending cardinal order. They sum to 1 and
  their absolute values are what `extrapolation_weights` already returned. For
  `exponential_three_point`, whose limit is a ratio of differences of the energies and so has no fixed
  weights, it returns `None`; a degenerate or mis-shaped input raises `ExtrapolationError` like its
  siblings.
- **`composite_formulas.is_linear_formula(formula)`.** `True` for the three two-point formulas,
  `False` for `exponential_three_point`.
- The producer contract is unchanged except for its version line.

## 0.72.0 - 2026-10-02

Composite levels of theory, phase P5 (ADR 0021): a composite energy you build
yourself, from your own recipe and the single points you ran, is deposited as an
`assembled` composite. Every payload accepted before is accepted unchanged; this
release only adds. See the "Worked payloads" section of the producer contract
for two complete examples (CCSD(T)/CBS from a TZ/QZ pair, and a focal-point sum).

- **`level_of_theory.composite_scheme`.** A level of theory now carries exactly
  one of `method` and `composite_scheme`. `method` stays required whenever
  `composite_scheme` is absent, so every existing payload is unchanged;
  `method` is now optional in the schema only because the other member exists.
  Both, or neither, is refused (`level_of_theory_method_with_composite_scheme`,
  `level_of_theory_requires_method_or_composite_scheme`). The definition:
  `kind` (`extrapolation` or `additive`; `named_method` is the server's own and
  is refused with `composite_scheme_named_method_not_sendable`), `terms[]` and an
  optional `literature`. A term has a local `key`, an `operation` (`base`,
  `value`, `extrapolation`, `difference`; `empirical` is refused), an
  `energy_component`, a `formula` and `exponent` on an extrapolation, and
  `inputs[]`, each a `slot` (`value`, `high`, `low`, `cardinal`), an ordinary
  `level_of_theory` and a declared `cardinal_number`. The total is the sum of the
  terms. Formulas: `inverse_power` and `inverse_power_shifted_half` (with an
  exponent), `karton_martin_scf` and `exponential_three_point` (without). Shape
  mistakes are `composite_scheme_malformed` with `context.rule` naming which; a
  nested composite, or a named composite method such as CBS-QB3 used as an input
  level, is `composite_scheme_nested`.
- **Identity.** The server resolves the input levels, hashes the definition and
  names the level of theory itself (for example
  `CBS[ref:CCSD(T)/cc-pVQZ + corr:CCSD(T)/cc-pV{T,Q}Z; inverse_power x=3; n=3,4]`).
  Formula, exponent, cardinal numbers, the input levels (with `core_treatment`)
  and the term order are identity; term keys and literature are not.
- **Assembled composites.** `composite_result.assembly: "assembled"` is accepted
  with `inputs[]`: each names a `term_key`, a `slot` (and `cardinal_number` on an
  extrapolation) and the calculation by a bundle-local `calculation_key` or a
  `calc_...` `calculation_ref`, never a database id. `inputs` on a `program_run`
  is refused (`composite_inputs_require_assembled`); an assembled composite at a
  level that carries no inline scheme keeps the code
  `composite_assembled_not_accepted`, now meaning exactly that.
  `software_release` is optional on an assembled composite and required on every
  other calculation (`calculation_software_release_required`, a coded refusal in
  place of the schema's missing-field error); a `program_run` composite still
  needs it.
- **Server-side refusals** (422): `composite_input_missing`,
  `composite_input_slot_unknown`, `composite_input_duplicate`,
  `composite_input_reference_invalid`, `composite_input_edge_is_derived` (the
  `composite_input` dependency role is written by the server and cannot be
  declared in `depends_on`), `composite_input_type_invalid` (an input is not a
  single point or optimisation), `composite_input_level_mismatch`,
  `composite_input_owner_mismatch`, `composite_input_geometry_mismatch`,
  `composite_total_required` (an assembled composite deposits its total; there is
  no unverifiable-by-absence), `composite_assembled_cannot_be_primary` (an
  assembled composite is not the run that produced a geometry; send it as an
  additional calculation) and `composite_total_mismatch` (the deposited total is not
  what the scheme gives for the inputs' stored energies, beyond
  `max(1e-6, 5e-7 * (1 + sum |weight|))` hartree, the sum over the stored numbers
  consumed: weight 1 for a value, base or difference input, the extrapolation's own
  weight for an extrapolated one, so `max(1e-6, 5e-7 * n)` when every weight is 1).
  TCKDB recomputes the total only to check it; it never stores the recomputed value.
  Warnings: `composite_input_geometry_undeclared` and `composite_total_unverifiable`
  (a needed energy or component is not stated, the correlation convention cannot be
  determined, or an extrapolation is degenerate).
- **Correlation and triples.** A `correlation` term reads the whole correlation
  energy, (T) included: the stored `correlation` where it already includes (T)
  (ORCA: `reference + correlation` equals the energy), `correlation + triples`
  where triples are stored separately (Molpro). The new
  `EnergyComponentKind.correlation_excluding_triples` reads the CCSD part: the
  stored `correlation` under Molpro's convention, `correlation - triples` under
  ORCA's. The textbook scheme (CCSD correlation extrapolated, (T) at a smaller
  basis as its own `triples` term) is written with it and counts (T) once. It is
  derived and never a stored single-point component (`sp_energy_component_derived`).
  The convention is read off the stored row; where it cannot be read the total is
  unverifiable, never guessed.
- **Term order is not identity.** The total is a sum, so terms sent in any order are
  one scheme; stored positions are canonical and the positions in
  `composite_result.terms` are mapped to them. A `cardinal_number` on a slot that is
  not a `cardinal` slot is refused (`composite_scheme_malformed`,
  `rule: cardinal_on_non_cardinal_slot`).
- **New public modules.** `tckdb_schemas.composite_formulas` (the four formulas,
  checked against the correlation energies printed in the ORCA manuals),
  `composite_total` (the recomputation, pure arithmetic a producer can run before
  sending) and `composite_worked_examples`.
- **New enums mirrored from the server:** `CompositeSchemeKind`,
  `CompositeTermOperation`, `CompositeExtrapolationFormula`,
  `CompositeInputSlot`, and `CalculationDependencyRole.composite_input`.

## 0.71.0 - 2026-10-01

Composite levels of theory, phase P3b (ADR 0021): a `composite` calculation's
deposited energy is compared with the Gaussian output log attached to it. No
payload field changes and every payload accepted before is accepted unchanged;
this release only adds three warnings to upload responses.

- **New warning `composite_energy_log_available`** (informational): the
  `composite_result` stated no energy and the attached log's summary block
  states E0 and the recipe ZPE. Nothing is filled; the message carries the
  numbers so the producer can send them.
- **New warnings** (the upload is accepted; the deposited values are kept
  exactly as sent, and nothing is filled from the log):
  `composite_energy_log_mismatch` (an attached Gaussian output log's summary
  block states a different `e0_hartree`, `recipe_zpe_hartree` or
  `electronic_energy_hartree` than the `composite_result`, beyond printed
  precision, `max(1e-6, 5e-7 * n)` hartree) and `composite_log_method_mismatch`
  (the log is a different composite method from the calculation's level of
  theory; the energies are then not compared).
- **What ARC (or any producer) must attach:** the Gaussian output log of the
  composite run as an `output_log` artifact on the `composite` calculation.
  Logs of CBS-QB3, ROCBS-QB3, CBS-4M and G3 are read; other composite methods,
  G4 and G4MP2 included, are not compared (no warning either way). G4 and G4MP2
  are declined because the Gaussian 16 Rev A.03 summary labels are shifted by one
  pair, so the number under `G4(0 K)` is not E0.
- An `sp` at a composite level whose attached log is a composite job still gets
  no single-point energy, as before.

## 0.70.0 - 2026-10-01

Composite levels of theory, phase P3a (ADR 0021): a calculation of type
`composite` records one program-run composite energy such as CBS-QB3 or G4.
Every payload accepted before is accepted unchanged; this release only adds.

- **New calculation type `composite`, with a `composite_result` block.**
  `type: "composite"` is accepted on the conformer upload, the thermo and
  statmech uploads' inline calculations, and the computed-species and
  computed-reaction bundles (the shared bundle-local calculation shape that the
  network upload also uses carries the block too, but that route has no test
  for it yet). It carries `composite_result`: `assembly`
  (`program_run`, or `assembled`, which the server refuses until user-built
  schemes arrive), the optional energies `electronic_energy_hartree` (ZPE-free,
  every recipe term included), `e0_hartree` (0 K, including the recipe's scaled
  zero-point energy) and `recipe_zpe_hartree`, and an optional `terms` list of
  `{term_position, value_hartree}`. A `null` energy means not stated. TCKDB never
  stores a total it computed itself.
- **The two always come together.** `composite_result` on any other type is
  refused with `composite_result_requires_composite_type`, and a `composite`
  calculation without it with `composite_type_requires_composite_result`.
- **Two arithmetic checks, blocking, to printed precision.** The tolerance is
  `max(1e-6, 5e-7 * n)` hartree, where `n` counts the rounded numbers in the
  equation (Gaussian prints each to six decimals): 3 for e0, `len(terms) + 1`
  for the terms. With all three energies
  present, `e0_hartree` must equal `electronic_energy_hartree +
  recipe_zpe_hartree` (`composite_e0_inconsistent`); with terms given and the
  total present, the terms must sum to `electronic_energy_hartree`
  (`composite_terms_do_not_sum`).
- **Server-side refusals** (422): `composite_assembled_not_accepted` (the
  assembled form arrives with user schemes), `composite_level_not_scheme_bound`
  (the level of theory must be a catalogued named method such as `CBS-QB3`),
  `composite_program_run_requires_software`, and
  `composite_term_position_unknown` (a term names a position the scheme does not
  have; a named method has none, so any position is accepted there).
- **A composite may be a conformer's primary calculation.** A species of two or
  more atoms may send, as `primary_calculation` / `calculation`, a program-run
  named composite that produced the geometry, in place of the `opt` (the `opt`
  rule is otherwise unchanged). With no `output_geometries` declared, the
  conformer's geometry is its final output, as for an `opt`.
- **A freq, sp or scan may depend on a composite that has an output geometry**
  (`depends_on` role `freq_on`, `single_point_on`, `scan_parent`), as on an `opt`.
- **Two energies refused.** A statmech or thermo record that links both an `sp`
  and a `composite` as its energy is refused with
  `statmech_energy_sp_and_composite_linked` /
  `thermo_energy_sp_and_composite_linked`. The existing role-consistency rules
  (`*_role_duplicate`, `*_sp_geometry_mismatch`, `*_energy_level_requires_sp`,
  `*_energy_level_ambiguous`, `*_energy_level_contradiction`) now apply to a
  linked `composite` as to an `sp`, and the energy level of a record is the
  composite's when one is linked (composite, then sp, then opt, then imported).
- **New warnings** (the upload is accepted): `named_composite_deposited_as_opt`
  and `named_composite_deposited_as_sp` (an `opt` or `sp` at a named composite
  method's level: send it as `composite`; refused once producers can),
  `composite_role_on_non_composite_calculation` (the source role `composite` on
  a calculation of another type) and
  `composite_frequency_level_differs_from_recipe` (a linked `freq` at a level
  other than the composite scheme's own frequency level).
- **Reads:** a calculation read returns `composite` (assembly, energies, terms)
  under `results`; a levels summary gains `geometry_source` and
  `frequency_source` (`composite_recipe` when the level is the named method's own
  internal level). Server responses, not producer payloads.
- Not in this release: assembled composites and user-built schemes, energy
  components on a single point, a Gaussian composite-log parser.

## 0.69.0 - 2026-10-01

Composite levels of theory, phase P4 (ADR 0021): energy components on single
points, and a core-treatment field on the level of theory. Both are additions;
every payload accepted before is accepted unchanged and means the same thing.

- **`sp_energy_components[]` on single points.** `CalculationWithResultsPayload`,
  the computed-species `CalculationInBundle` and the flat `CalculationIn` (the
  computed-reaction and network bundles) take a list of
  `{component, value_hartree}`, where `component` is one of `total`,
  `reference` (the SCF / HF energy), `correlation`, `triples`, `dboc` or
  `scalar_relativistic`. The value is what the program printed. Five refusals,
  each with a code and context:
  - `sp_energy_component_not_on_sp`: the calculation is not a single point.
  - `sp_energy_components_require_energy`: the single point's electronic energy
    is not stated. The parts come from the same output as the energy, so state
    it; a log can no longer fill the energy in after the parts were checked.
  - `sp_energy_component_duplicate`: one value per component.
  - `sp_energy_component_total_mismatch`: a `total` must equal the energy
    within 1e-6 Eh.
  - `sp_energy_components_do_not_sum`: `reference + correlation` must equal the
    energy within 1e-6 Eh. When a `triples` component is also sent,
    `reference + correlation + triples` may match instead (ORCA's correlation
    energy already includes (T); Molpro prints CCSD and (T) separately). The
    refusal reports both sums. On F12 methods, `reference` must include the
    CABS-singles correction if the program's total does.

  The server compares and never stores a value it computed. A single-point read
  returns the components under `results.sp.energy_components`.
- **`LevelOfTheoryRef.core_treatment`** (optional): `frozen_core` or
  `all_electron`. State it only when the run says so. It is part of the level's
  identity **only when stated**, so a payload that omits it resolves to exactly
  the level it always did. Frozen-core and all-electron CCSD(T)/cc-pCVTZ are now
  two levels instead of one. A partial treatment (an energy window, Gaussian's
  `FC=1`) has no value yet; leave the field out and describe it in `keywords`.
- New public enums `CoreTreatment` and `EnergyComponentKind`.
- Reads: `LevelOfTheorySummary` and the level-of-theory detail carry
  `core_treatment` (`null` = not stated).

## 0.68.0 - 2026-10-01

Composite levels of theory, phase P2 (ADR 0021): the server now records the
recipe behind a named composite method. No upload payload field is added,
removed or changed, and every payload accepted before is accepted unchanged.

- **A level of theory that names a catalogued composite method is bound to its
  recipe.** `CBS-QB3`, `G4`, `W1U` and the other methods in the server's
  catalogue (`cbsqb3` and the other aliases reach the same one) now resolve to a
  level of theory bound to a `named_method` composite scheme. The level's hash
  and ref are unchanged, so a level sent before and after is the same row. A
  method that is not in the catalogue is bound to nothing.
- **Reads gain the recipe.** `GET /scientific/composite-schemes/{ref}` returns a
  scheme (`csch_...` ref), and every level-of-theory summary on a read carries
  `composite_scheme` (`{composite_scheme_ref, kind, name}`), `null` for an
  ordinary level. These are server responses, not producer payloads.
- **New public-ref prefix `csch_`** (content-derived: the same recipe has the
  same ref on every instance).
- Not in this release: sending a scheme of your own, the `composite`
  calculation type, and energy components. Those are later phases and change
  the upload contract when they land.

## 0.67.0 - 2026-10-01

`level_of_theory.method` guards for named composite methods (ADR 0021). Every
payload that was accepted and meant one method is unchanged.

- **`//` is refused in `method`.** `LevelOfTheoryRef.method` containing `//`
  (ARC's `energy//geometry` shorthand, `"ccsd(t)-f12/cc-pvtz-f12//b3lyp/def2tzvp"`)
  is refused with code `level_of_theory_method_is_compound` and `context`
  `{field: "method", value}`. It is two levels of theory, not one method: send
  the single-point and the optimization levels as separate calculations, each
  with its own level of theory. A single `/` is still accepted.
- **Correction-table names warn.** A named composite method followed by a
  correction-table label or a year (`cbs-qb3-paraskevas`, `cbsqb32023`) is
  accepted with an upload warning `level_of_theory_method_names_correction_table`
  at `...level_of_theory.method`. These names select Arkane AEC/BAC parameters,
  not a method: the calculation that ran is CBS-QB3. The name is stored as sent,
  as a separate level of theory from the method, and is never aliased. Send the
  method and name the table on the energy correction scheme. A later release
  will refuse these once the producers send the method.
- **New helpers.** `collect_ref_warnings` walks a validated request and returns
  the software-release version warnings and these method warnings together;
  `collect_software_release_version_warnings` is unchanged.
  `correction_table_method_stem` recognises the shape.

Server side, in the same change: `cbsqb3`, `rocbsqb3`, `cbs4m` and `cbsapno`
now key to the hyphenated spellings, and `g4(mp2)`, `g3(mp2)` and `g3(mp2)b3`
to `g4mp2`, `g3mp2` and `g3mp2b3`, so a level of theory written either way is
one row. `W1`, `W1U`, `W1BD` and `W1RO` stay four methods and `CBS-QB3` and
`ROCBS-QB3` stay two. A producer that hashes level-of-theory identity locally
must adopt the same aliases to agree with the server.

## 0.66.0 - 2026-10-01

Frequency level on correction schemes (composite-levels plan P6). One optional
field, and one new warning. A payload written against 0.64.0 is still valid and
means what it meant.

- **`EnergyCorrectionSchemeRef.frequency_level_of_theory`**
  (`LevelOfTheoryRef | null`). The level of theory the frequencies were computed
  at, for a scheme keyed on an `energy//frequency` pair. Arkane keys Petersson
  and Melius BAC (and only those; atom energies are keyed on the energy level
  alone) on `CompositeLevelOfTheory(freq=..., energy=...)`; a scheme held only one level of
  theory, so the frequency half was lost and two such schemes either collapsed
  into one row (identical tables) or were refused as a value conflict (different
  tables). Send the `energy` half in `level_of_theory` as before and the `freq`
  half here. It joins scheme identity, in both identity forms (with and without
  `data_revision`): the same energy level with two different frequency levels is
  two schemes. An absent value is a value of its own: a scheme sent without it
  never matches one that has it, keeps exactly the identity it had before the
  field existed, and keeps its public ref byte for byte. It is resolved like
  `level_of_theory`, so a level that was merged into another resolves to the one
  that holds it. Adapters: for an Arkane `energy//freq`-keyed BAC, send both halves; for
  an atom-energy scheme or a scheme keyed on one level, send nothing new.
  Three rules, each stated where it applies: the field is **refused** on any
  kind other than `bac_petersson` and `bac_melius`
  (`energy_correction_scheme_frequency_level_not_applicable`) and without
  `level_of_theory` (`energy_correction_scheme_frequency_level_without_energy_level`),
  and a frequency level that resolves to the same level of theory as the energy
  level is **stored as absent**, so one table cannot become two schemes by
  spelling its level twice.
- **`composite_delta_prefer_scheme_terms` warning.** A new applied correction
  with `application_role = "composite_delta"` is stored as sent and answered
  with this warning (field `application_role`). Focal-point deltas
  (core-valence, higher-order triples, relativistic, DBOC) are going to become
  composite-scheme terms, which can name every calculation a delta is built from
  and sit on the energy instead of the species entry. It is a warning and not a
  refusal; no payload that was accepted is refused.

Scientific reads gain `frequency_level_of_theory` beside `level_of_theory` (a
level-of-theory summary, `null` when the scheme is keyed on one level) on the
scheme detail and search records.

## 0.65.0 - 2026-10-01

A single atom may be deposited with an `sp` primary on the pressure-dependent
network bundle too (#615). Every existing payload is unchanged.

- **`POST /uploads/networks/pdep`: a one-atom conformer may send its `sp` as
  `calculation`.** The two other bundle routes accepted this already (#610);
  the network route still required an `opt`, so the H atom of a hydrazine-style
  network could not be sent honestly. The rule is the same one: a conformer
  whose geometry is exactly one atom may carry `type: "sp"` as its primary, with
  `sp_electronic_energy_hartree`; an `sp` on two or more atoms, an uncountable
  geometry, or any type other than `opt`/`sp` is still refused with the same
  message. The atom's statmech `source_calculations` link that `sp` with role
  `sp`, and the solve's `source_calculations` and `state_energies` name it. A
  relabelled `opt` on an atom is still accepted. Transition states still require
  an `opt` primary. Only the producer contract's description of the network's
  conformer changed; no field was added or removed.
- **Duplicate single points are now refused when they declare output-only
  geometry (shared rule, so `/uploads/statmech` and `/uploads/thermo` change
  too).** With no `opt` linked, two `sp` links on one geometry are refused with
  `statmech_role_duplicate` / `thermo_role_duplicate`. The rule used to read an
  `sp`'s input geometry only; it now reads its input geometry, else its output
  geometry. A deposit whose `sp` declares only an output geometry on the same
  geometry as another linked `sp` therefore changes: the same level of theory
  used to return 201 and now returns the `*_role_duplicate` refusal; a different
  level used to return `*_energy_level_ambiguous` and now returns
  `*_role_duplicate`. This is what two input-linked `sp`s already got.

## 0.64.0 - 2026-10-01

Transition-state contract additions (#621). Every field is optional and
every existing payload is unchanged.

- **Two more kinds of transition-state validation evidence.**
  `TransitionStateValidationEvidenceIn.kind` was `"irc"` only. It now also
  accepts `"energy_ordering"` (the saddle point lies above both wells) and
  `"imaginary_mode"` (what the frequency calculation found). At most one record
  per kind. An `energy_ordering` record carries `energies`, one per participant
  (`"ts"`, `"reactant:N"`, `"product:N"`), each with an `energy_kind`
  (`"electronic"` or `"e0"`, which are never compared with each other), an
  `energy_hartree` and the `source_calculation_key` it was taken from, which
  must belong to that participant. An `imaginary_mode` record carries
  `imaginary_frequency_count`, `imaginary_frequency_cm1` (negative) and
  `mode_displacement_agrees` (your displacement check's verdict, null when not
  assessed). A pass that the record's own *stated* numbers contradict is refused, a
  field is refused on a kind it does not describe, and only a passing `irc`
  record silences `transition_state_missing_irc_evidence`. `energy_ordering`
  is accepted on the computed-reaction and pressure-dependent bundles and
  refused on the standalone transition-state upload, which has no
  calculations for the wells; `imaginary_mode` binds there to the single
  `freq` additional calculation. Energies are finite and not positive (absolute,
  in hartree; zero is exact for the bare proton), `imaginary_frequency_cm1` is finite, and the database refuses
  NaN and infinities too. An `electronic` energy must come from an `sp` or
  `opt` calculation and an `e0` from a `freq`; one energy kind taken at more
  than one level of theory is accepted with a
  `transition_state_energy_ordering_mixed_levels` warning. An `imaginary_mode`
  count or frequency that disagrees with the frequency result it cites is
  refused, and a pass with more than one imaginary mode needs that result to
  designate the reaction coordinate. Stated energies are not reconciled with
  the energies stored on the cited calculations.
- **Transition-state statmech on the reaction bundle.**
  `BundleTransitionStateIn` gains `statmech`, the block a species carries on
  the computed-species route, written through the same code, so the same rules
  apply (source roles, the three energy levels, scale-factor resolution).
  Its `source_calculations` and torsion scans must be the saddle point's own
  calculations. The response gains `transition_state_statmech_id`, and a
  transition-state entry read serves it under `include=statmech`.
- **Standalone transition-state route.** `additional_calculations` now accepts
  `scan`, and `CalculationWithResultsPayload` gains `scan_result` (type `scan`
  only) so the points travel with it. That payload is shared, so this is a
  cross-route addition: a route that already accepted a `scan` calculation
  (the conformer route does not restrict its primary calculation's type) now
  stores the scan's points, and a route that does not allow `scan` is unchanged. The request gains
  `applied_energy_corrections` (no source keys or frequency scale factor, since
  the payload has no key namespace), and `atom_map`, with a `key` and a
  `geometry` on each reaction participant and a `geometry_key` for the saddle
  point, because a map counts its indices into geometries.
- **An IRC result may leave its direction and flags unstated.**
  `IRCResultPayload.direction`, `has_forward` and `has_reverse` are now
  optional. Unstated is stored and read back as null, never as `false`; a flag
  stated `false` against points of that direction is still refused.

## 0.63.0 - 2026-09-30

The Arrhenius reference temperature, and the reaction bundle's kinetics block
now takes the same evidence as the standalone route (#620). Both are additions:
a payload valid under 0.62.0 is valid under this release, byte for byte.

**`t0_k`** on `BundleKineticsIn` (`POST /uploads/computed-reaction`) and on
`KineticsUploadRequest` (`POST /uploads/kinetics`): the reference temperature
T0 of the scalar rate, in K, meaning `k = A (T/T0)^n exp(-Ea/RT)`. It defaults
to 1 K, which is the plain `A T^n` form and what every record deposited
before this release meant. Before, a producer that fitted with T0 = 298 K had
to send `A / 298^n` and the T0 it fitted with was lost. It must satisfy
`0 < t0_k <= 10000`. It applies to the record's own `a`, `n` and `reported_ea`
of a modified-Arrhenius rate. Falloff (`lindemann`, `troe`, `sri`), `plog`,
`chebyshev` and `multi_arrhenius` records are always at 1 K and the standalone
route refuses any other `t0_k` on them: their low-pressure limit or child rows
carry no T0 of their own (Arkane and RMG give each falloff limit an independent
T0), so one value would silently mis-state part of the rate. The server stores `a` as sent (it is A at T0, not A rescaled) and
serves `t0_k` back; a consumer that evaluates k(T) from a stored `a`, `n` and
`ea_kj_mol` must use it.

**`interpretation_assignments`, `tunneling_application`, `network_kinetics_ref`**
on `BundleKineticsIn`. They are the standalone route's own models and cross-field
checks, now defined once in `tckdb_schemas.fragments.kinetics_evidence`
(`KineticsInterpretationAssignmentUpload`, `KineticsTunnelingApplicationUpload`,
`ConformerSelectionContentRef`), and the server writes them with the same
persistence code, so the two routes refuse the same interpretation and tunneling
mistakes with the same codes (a coded 404 for an unknown statmech, transition
state, calculation, artifact or network ref, the same 422 for an incomplete
interpretation set or a tunneling block that disagrees with `tunneling_model`).
Two things follow from how a bundle is built and are part of the contract:
every reference is the public ref of a record deposited *earlier* (a bundle
cannot cite a statmech, transition state or calculation it is itself creating,
because the depositor cannot know its ref in advance), and a cited transition
state must be a transition state of this rate's reaction: a reaction entry of
the same reaction with the same structure participants (same species entries,
either direction), so the excited-state or isotopologue entry's transition state
is refused. A bundle mints its own reaction entry, so the entry itself can
never match. The bundle's `tunneling_model` is filled from
`tunneling_application.model` when only the evidence is sent, as on the
standalone route.

Three wire enums are now mirrored here, so the evidence models can live in this
package: `KineticsEnsemblePolicy`, `KineticsStandardStateConvention` and
`KineticsDegeneracyInterpretation`, with the same members and order as the
backend's.

## 0.62.0 - 2026-09-30

Four changes for correction-scheme provenance (#619), all optional and
backward compatible: a payload written against 0.61.0 is still valid and means
what it meant.

- **`EnergyCorrectionSchemeRef.data_revision`** (`str | null`, at most 200
  characters). The revision of the data that holds the parameter tables, for
  example the RMG-database commit holding Arkane's atom-energy and BAC tables.
  When present it joins the scheme's identity and the workflow-tool build stops
  being part of it: identity is `(kind, name, level_of_theory,
  source_literature, software, data_revision)`. Two builds that read the same
  revision's tables are one scheme (the first depositor's build is kept as
  provenance), and a new revision is a new scheme, so a one-parameter change in
  a new database revision is no longer refused as a value conflict. When absent
  the identity is exactly what it was, including the tool build, so every scheme
  already deposited keeps its identity and its public ref. A deposit with a
  revision never matches one without, even when the tables are identical. A value
  of 7 to 64 hex digits is lower-cased as a git commit; any other value is kept
  as written. Adapters: send the RMG-database commit here, keep stamping the tool
  release, and expect the tool release to stop splitting schemes.
- **`EnergyCorrectionSchemeRef.atom_params_applied_as`** (`subtracted` |
  `added`, new enum `AtomParamApplication`). How the scheme's `atom_params`
  enter the corrected energy. It covers every entry of `atom_params` and nothing
  else, requires `atom_params` to be present, and is not inferred when omitted.
  A value that differs from the one stored on the matched scheme is refused like
  a differing parameter value; a value sent for a row that has none is stored.
  Arkane's `atom_energy` tables are `subtracted` (`count * value` is removed
  from the energy), its `atom_hf` tables are `added`, and its `atom_thermal`
  tables are `subtracted`, because Arkane applies
  `+ count * (atom_hf - atom_thermal)` per atom. The first deposit of a scheme
  fixes its sign: a later differing value is refused.
- **`SchemeAtomParamPayload` now documents its meaning and unit.** `value` is in
  the scheme's `units`. For `kind=atom_energy` it is the level's atomic energy of
  `element`; `atom_hf` is the atom's experimental enthalpy of formation;
  `atom_thermal` its thermal enthalpy increment; `soc` its spin-orbit correction.
  No field changed; the contract text did.
- **`energy_level_of_theory` is now stored.** On thermo and statmech blocks
  (every bundle root, `/uploads/thermo`, `/uploads/statmech`, and the statmech
  nested in `/uploads/conformers`) the declared level was checked against the
  linked calculations and discarded. It is now stored as declared and read back as
  `levels.declared_energy` on the scientific thermo and statmech reads. It is
  separate from `levels.energy`, which is still derived from the linked
  calculations on every read. It stays `null` when nothing was declared, and is
  never back-filled. No request field changed.

Scientific reads gain `levels.declared_energy`,
`energy_correction_scheme.data_revision` and
`energy_correction_scheme.atom_params_applied_as` (all `null` when not stated).

## 0.61.0 - 2026-09-30

Four things the two species-bearing bundles, `POST /api/v1/uploads/computed-species`
and `POST /api/v1/uploads/computed-reaction`, could not say (#622), all additive.

- **Transport.** `ComputedSpeciesUploadRequest.transport` and, per species,
  `BundleSpeciesIn.transport` take a `TransportInBundle`: exactly the standalone
  `POST /api/v1/uploads/transport` content (`sigma_angstrom` and
  `epsilon_over_k_k` together, `dipole_debye`, `polarizability_angstrom3`,
  `rotational_relaxation`, `scientific_origin`, `literature`, `software_release`,
  `workflow_tool_release`, `note`, at least one property) plus
  `source_calculations`, a list of `{calculation_key, role}` naming the bundle's
  own calculations (`role` is `full_transport`, `dipole`, `polarizability` or
  `supporting_geometry`). It lands on the same species entry as that species'
  thermo and statmech, and is append-only. A source calculation must belong to
  the same species. Provenance follows thermo: the block's own release wins,
  and where it names none the bundle's fills in (`workflow_tool_release` on
  both bundles, `analysis_software_release` as well on the reaction bundle).
  The responses name what was written: `ComputedSpeciesUploadResult.transport`
  (`{transport_id, transport_ref}`) and `transport_ids` with `transport_refs`
  (`trn_...`, same order) on the reaction bundle's response. A source
  calculation of another species on the reaction bundle is refused with the
  coded `transport_source_calculation_owner_mismatch` (422, `context` has
  `field`, `target`, `owner_kind`), now a code a depositor can receive.
  Provenance gaps are annotated as warnings under `transport.` /
  `species['<key>'].transport.`.
- **Rejected rotors.** `invalidated_reason` on `StatmechTorsionInBundle` and on
  the reaction bundle's `BundleStatmechTorsionIn`, the same field the conformer
  route's `StatmechTorsionIn` has. It is stored and read back under the
  statmech's torsions.
- **Who measured the SCF stability.** `SCFStabilityContent.source_calculation_key`
  names the calculation (job) in the same bundle that measured the verdict, when
  it is not the calculation the block is attached to. It may point at a
  calculation declared later in the payload. Rules: it must name a declared
  calculation; of the same species entry (or of the transition state, for a
  transition-state calculation), else the coded
  `scf_stability_source_calculation_owner_mismatch`; on the same conformer as
  the carrier (reaction bundle: the same `conformer_key`, or the conformer of
  its `geometry_key`, where both are stated), else the coded
  `scf_stability_source_geometry_mismatch` (422, `context` has `field`, `key`,
  `carrier_key`), the sibling of `thermo_sp_geometry_mismatch`; it may not name
  the carrier itself or close a cycle with other blocks' keys. A measuring job
  at a different level of theory than the carrier is accepted with the upload
  warning `scf_stability_source_level_mismatch`. The key is stored in the
  existing `calc_scf_stability.source_calculation_id` and read back as
  `source_calculation_ref`. No stability calculation type was added: the
  measuring job keeps the type it has. `SCFStabilityPayload` (the primitive
  routes, which name a calculation by id) and `SCFStabilityContent` now share
  `SCFStabilityBase`, so the key is not in the primitive routes' schemas and is
  refused there as an unknown field. It is also refused on
  `POST /api/v1/uploads/networks/pdep`, which has no pass to link it.
- **Contract prose.** `SoftwareReleaseRef` now says what `version`, `revision`
  and `build` hold and that all three are part of release identity (`revision`
  is the vendor label such as Gaussian `C.02`, or the commit hash for analysis
  software; `build` is a compile or packaging variant). There is still no
  `git_commit` on `SoftwareReleaseRef`; adding one needs a column and a change
  to release identity, and is deferred. `PathSearchResultPayload.converged` now
  says what it means for each method and that an output file existing is not a
  convergence verdict. `PathSearchPointPayload.is_climbing_image` stays a plain
  `bool` defaulting to `false`: `false` still reads as both "not a climbing image"
  and "not stated", because a tri-state value needs the stored column to accept
  NULL, which is a migration and is deferred.

## 0.60.0 - 2026-09-30

`electronic_levels` is now accepted on the bundle statmech blocks,
`StatmechInBundle` (`/uploads/computed-species`) and `BundleStatmechIn`
(`/uploads/computed-reaction`), with the same element shape
(`ElectronicLevelIn`: `level_index`, `energy_cm1`, `degeneracy`) and the same
rule as `/uploads/statmech` (`level_index` unique within a statmech). Additive
and optional: it defaults to no levels, and both bundle roots keep the same
field set (#609). The network PDep upload's species and transition-state statmech blocks share the backend persist path, so they now accept and store `electronic_levels` too. Before this, either block refused it with 422
`extra_forbidden`, so a single-atom bundle (ARC's O and Cl atoms) had no way to
carry its electronic partition function. The backend also gains upload
warnings for a one-atom species whose ground term is not S:
`missing_atomic_electronic_levels`, `missing_atomic_spin_orbit_correction`,
`atomic_electronic_degeneracy_contradicts_term`, and `term_symbol_contradicts_multiplicity`
for a term symbol whose leading 2S+1 disagrees with the declared multiplicity.
`missing_statmech_frequency_source` now decides "single atom" from the
geometry, `rigid_rotor_kind` or the species identity rather than from the
absence of rotational constants (#608).

## 0.59.0 - 2026-09-30

A single atom may be deposited with an `sp` primary (#610), on both
`POST /api/v1/uploads/computed-species` and
`POST /api/v1/uploads/computed-reaction`. Both routes required the conformer's
primary calculation to be an `opt`, and an atom has no geometry to optimise, so
ARC's adapter relabelled the atom's single point as an "optimisation" (same log
and energy, `converged=false`, and a level of theory and program that were not
the ones used). The rule is now: a conformer whose own XYZ has exactly one atom
may send `type: "sp"` as its primary; a conformer of two or more atoms still
needs `opt`, and a one-atom primary of any type other than `opt` or `sp` is
still refused. The refusal for an `sp` on two or more atoms now says how many
atoms the geometry has. A relabelled `opt` on an atom is still accepted, so no
existing producer breaks. The server stores the atom's `sp` with the conformer
geometry as both its input and its final output, so the conformer reads back
with a geometry; a further `sp` on the atom gets no inferred
`single_point_on` edge and no `dependency_edge_not_inferred` warning, since the
atom has no `opt` for the edge to name. With no `opt` linked, two `sp` links
on one geometry (thermo or statmech) are refused `thermo_role_duplicate` /
`statmech_role_duplicate`, as two on one optimisation always were; the
`/uploads/conformers` route gives a one-atom `sp` primary the same geometry
link. What an atom should send is in the
producer contract, under the two conformer primary-calculation rules. The
wire shape gains nothing: no field is added, removed or renamed.

## 0.58.0 - 2026-09-30

Producer contract only; no model in this package changes. `POST /api/v1/bundles/dry-run`
and `POST /api/v1/bundles/submit` now cap one bundle (#586): a request body
over 5 MiB is refused `bundle_too_large` (413, `context.max_bytes`, and
`context.given_bytes` when the request declared its length) before it is
parsed, and a bundle with more than 500 thermo plus kinetics records is
refused `bundle_too_many_records` (422, `context.max_records`,
`context.records`). Both caps are operator settings; the defaults are far
above any bundle measured. The dry run also has its own, tighter rate bucket
(10 per minute per credential by default), still answered `429
rate_limit_exceeded`.

## 0.57.0 - 2026-09-30

Every upload, job and bundle response now names its submission by public ref
as well as row id: `submission_ref` (`sub_...`) beside `submission_id`. In this
package that is `ComputedSpeciesUploadResult.submission_ref`, and
`CalculationUploadRefInBundle.calculation_ref` (`calc_...`) beside
`calculation_id`. Both are optional and additive. The two routes that took a
row id in the path, `POST /api/v1/submissions/{submission_id}/rights-attestations`
and `POST /api/v1/calculations/{calculation_id}/artifacts`, now accept either
the integer or the ref there (a `handle_type_mismatch` 422 for a ref of the
wrong kind, 404 `handle_not_found` for an unknown one, in either form); the integer is deprecated, not removed.

## 0.56.0 - 2026-09-29

`reversible` on the reaction of `POST /api/v1/uploads/transition-states`
(`TSReactionUpload`) is now optional and defaults to `true`, matching
`POST /api/v1/uploads/computed-reaction`. Omitted means `true` on both
routes: a transition state belongs to an elementary step, and an elementary
step is reversible by microscopic reversibility. Send `false` only to state
that the step is irreversible. A payload that already sends the field is
unaffected. Both routes now carry the same description of the field in the
contract. (#583)

## 0.55.0 - 2026-09-29

Producer contract only; no model in this package changes. New refusal code
on `POST /api/v1/submissions/{submission_ref}/supersede`:
`submission_supersede_not_owner` (403). The caller must have created both
submissions -- the one in the path and the one named by `new_submission_ref`
-- or hold the curator or admin role. An unknown ref is still 404.

## 0.54.0 - 2026-09-29

The producer contract now shows the enthalpy-reference rule
(`enthalpy_reference_error`) as reached from `POST /api/v1/bundles/dry-run`
as well as `POST /api/v1/bundles/submit` (#577). A dry run used to skip
every check inside the thermo and kinetics upload workflows, so a bundle
could pass it and then be refused on submit; it now rehearses submit and
reports the refusal submit would give, with the same `code` and message,
as an `error` entry in `messages` (and `bundle_valid: false`). No wire
model changed.

New refusal code on `POST /api/v1/bundles/dry-run` only:
`dry_run_contended` (503, `Retry-After: 1`, `context.reason` is
`lock_timeout` or `deadlock`). The rehearsal gives way to a concurrent
deposit writing the same records rather than delay or deadlock it; nothing
was decided about the bundle, so retry the dry run.

## 0.53.0 - 2026-09-29

Producer contract only; no model in this package changes. The submission
supersede surface is now `POST /api/v1/submissions/{submission_ref}/supersede`
with body `{"new_submission_ref": "sub_..."}` (issue #571): both submissions
are named by public ref, and a row id is refused with 422 in the path and in
the body. **Breaking for that route:** the 0.52.0 contract's
`new_submission_id` (an integer) is no longer accepted. A submission's ref is
`public_ref` on a submission read.

## 0.52.0 - 2026-09-29

Ship a **producer contract** inside the package:
`tckdb_schemas/contract/PRODUCER_CONTRACT.md` plus one JSON Schema per
upload payload under `tckdb_schemas/contract/schemas/`. It states, per
upload route, the payload fields (types, units, allowed values,
constraints), the rules the payload models and the upload workflows
enforce, the refusal codes the route can return, and a minimal valid
example. It is generated from the backend's routes, models and code
catalogue (`backend/scripts/generate_producer_contract.py`) and CI refuses a
stale copy. Read it with `python -m tckdb_schemas.contract --print`, see
what moved since the version you target with `--since <version>`, and load a
schema with `tckdb_schemas.contract.json_schema("ThermoUploadRequest")`.

Also new: `tckdb_schemas.producer_rule.producer_rule`, a marker for a
shared rule a workflow applies outside the payload model; the contract
prints every marked function a route reaches. `enthalpy_reference_error` is
marked, and its docstring now states the rule in a producer's terms.
`ThermoStateFields` gains field descriptions for `phase`,
`enthalpy_reference_kind` and `reference_pressure_bar`, and the four upload
request models this package defines declare a minimal valid example
(`json_schema_extra["examples"]`). No field, value or validation behaviour
changes: every 0.51.0 payload validates identically.

## 0.51.0 - 2026-09-27

`enthalpy_reference.enthalpy_reference_error` now counts a tabulated Gibbs
energy (`points[*].g_kj_mol`) as enthalpy content. A stored G is
H(T) - T*S(T) on the record's enthalpy zero, so it carries H's reference.
**Behaviour change for depositors:** a thermo deposit whose points carry `g`
must now declare `enthalpy_reference_kind`, like one that carries an
enthalpy; before, an undeclared G-only (or G-and-S) deposit was accepted,
and declaring `formation_298k` on it was refused as
`enthalpy_declaration_without_content`. Now the undeclared one is refused
with `enthalpy_declaration_absent` and the declared one is accepted. Codes
and messages are unchanged. Existing stored records are not revisited.

## 0.50.0 - 2026-09-27

Add `enthalpy_reference.shared_enthalpy_reference(kinds)`, the rule for
combining enthalpies from several thermo records: every term must declare
its `enthalpy_reference_kind`, and all must declare the same one. It returns
the shared kind, or declines with `enthalpy_reference_unrecorded` (any term
undeclared) or `enthalpy_reference_mixed` (terms disagree), exported as
`ENTHALPY_REFERENCE_UNRECORDED` / `ENTHALPY_REFERENCE_MIXED`. An empty
collection raises `ValueError` rather than reporting a vacuous shared basis.
The ML reaction export's `delta_h298` already applied this rule privately
and now calls it; its reason values are unchanged. Additive: no existing
name, value or behaviour changes.

## 0.49.0 - 2026-09-24

Stop defaulting `reference_pressure_bar` to 1 bar on computed thermo uploads
(issue #529). ARC computes entropy at 1 atm via a hardcoded translational
partition function and records no pressure anywhere in its output, so the
default was stamping every computed deposit with a standard state its own
numbers were not computed at. An omitted pressure now stays unrecorded, for
every scientific origin. `phase` keeps defaulting to `gas` for computed
uploads: unlike the pressure convention, gas is a direct consequence of the
ideal-gas statistical mechanics every current computed producer uses, not
an arbitrary convention a producer could plausibly have gotten wrong.
Existing stored records still carry the old 1 bar default; correcting them
is a separate, undecided question.

## 0.47.0 - 2026-09-23

Explicit enthalpy reference declarations for thermo deposits and grouped reference reads.
No inferred defaults or legacy backfill.## 0.137.0 - 2026-10-25

## 0.137.0 - 2026-10-25

An ensemble member's relative energy states its convention.

- `ModelMember.relative_energy_convention` (optional, new): what is already folded into `relative_energy_kj_mol`. It uses the existing
  `EnergyCorrectionConvention` vocabulary that network state energies use.
- It is required with an energy and refused without one, and a convention without an energy is refused: the two are stated together or
  not at all. Nothing is assumed. `other` is not accepted for a member (it would need a note, and a member has none; the shared enum lists `other`, but it is always refused on members).
- All members of a declaration that state an energy share one convention, and when the recipe states `energy_zero` the convention agrees with it (`electronic` with `electronic_only`, `e0` with `electronic_plus_zpe`); mixed or contradicting conventions are refused.
- Refused at the route as `request_validation_error` (the declaration's existing request validation); a declaration that reaches the
  service unvalidated is refused there as `statmech_model_declaration_invalid`. No new refusal code.

## 0.136.0 - 2026-10-25

A kinetics record can state the bath gas it was obtained for. Additive: an upload that sends none is unchanged.

- `BundleKineticsIn.bath_gas` (`/uploads/computed-reaction`): a list of `{species_key, mole_fraction}`, each key naming a species of the bundle.
  `KineticsUploadRequest.bath_gas` (`/uploads/kinetics` and a kinetics contribution bundle) is a list of `{species_entry, mole_fraction}`.
- Three refusal codes (422): `kinetics_bath_gas_at_high_pressure_limit`, `kinetics_bath_gas_mole_fraction_sum` (fractions sum to 1 within 1e-9, as for a
  network solve's bath gas) and `kinetics_bath_gas_duplicate_species`. The shared check is `tckdb_schemas.fragments.kinetics_bath_gas.check_kinetics_bath_gas`.

## 0.135.0 - 2026-10-25

One refusal code joins the contract. No field is added or removed.

- `calculation_dependency_cycle` (409): a calculation dependency whose parent is already reachable from its child is refused; `context` names both calculations by public reference.
- A bundle whose `depends_on` edges form a ring is refused at validation (a 422 naming the depositor's own calculation keys), before any write, on the sync and async paths. The check is a validator, so no field changes.

## 0.134.0 - 2026-10-24

Three refusal codes join the contract. No field is added or removed.

- `check_inputs_not_supported` (422): `check_inputs` was sent to a route that does not compare inputs; it is refused, not ignored.
- `assessment_input_check_limit` (422) and `check_inputs_needs_assessments` (422): the opt-in `check_inputs=true` on the thermo and kinetics lists compares each
  stored assessment's inputs with today's, so it needs `include=assessments` and at most 10 records.

## 0.133.0 - 2026-10-24

One refusal code joins the contract. No field is added or removed.

- `lowest_energy_across_levels` (422): a `/lookup` request with `selection=lowest_energy` matched calculations at more than one level of
  theory. Absolute energies from different levels are never compared, so the lookup refuses (it used to warn and return a winner). The response
  names the levels found; the remedy is to filter to one level or to use a select endpoint.

## 0.132.0 - 2026-10-23

Four refusal codes join the contract. No field is added or removed.

- `browse_parameter_removed` (422): `collapse` or `selection_policy` was sent to a read or an export. Neither exists; the response names the
  select endpoint that answers "give me one".
- `parameter_renamed` (422): `temperature_min` / `temperature_max` on the thermo and kinetics reads are now `coverage_temperature_min_k` /
  `coverage_temperature_max_k`.
- `export_selection_ambiguous` (422) and `export_ref_unmatched` (422): the CHEMKIN export holds one record per species or reaction and never
  picks; it refuses with the ambiguous subjects and their candidate refs, or with the named refs that matched nothing.

## 0.131.0 - 2026-10-21

Review fixes to versioning PR-4. No field is added or removed; a version field is stricter.

- `ExecutionEnvironmentManifestPayload.schema_version` is refused unless it is exactly the integer `1`. Pydantic matches a `Literal[1]` by
  equality, so `true` and `1.0` were accepted before; they are not now (the JSON Schema is unchanged: still the constant `1`).
- New `tckdb_schemas.fragments.version.ExactInt`, the validator that does this for any version field written as a `Literal`.
  `VersionRef.version` was already a `StrictInt`.

## 0.130.0 - 2026-10-20

**Breaking, once: every stored or wire string that fused a name with a version is now a name beside an integer version, and every TCKDB-owned
version restarts at 1 (versioning PR-4).** Versions that belong to somebody else (an ESS release, a producer's own parser, NIST's ThermoML) are
untouched and stay text.

What changes for a producer:

- `ExecutionEnvironmentManifestPayload` (`runtime`/`software_release`/`executable`/`closure` unchanged): `schema_version: "tckdb.execution-environment.v1"`
  becomes two fields, `schema_name: "tckdb.execution-environment"` and `schema_version: 1` (an integer). The manifest's `content_digest` changes with it
  (the canonical payload carries both fields); the server recomputed the stored digests.
- Parser stamps: `parameters_parser_version: str` on a calculation becomes `parameters_parser: {name, version}` (`ParserStamp`, both text, the version
  exactly as the parser's owner spells it), and a Hessian's `parser_version: str` becomes `parser: {name, version}`.
- `bundle_version` on a contribution bundle is the integer `1` (it was the string `"0.1"`); `bundle_format` is unchanged.
- New `tckdb_schemas.fragments.version.ParserStamp`, beside `VersionRef`.

Reads gain no new endpoints. `rubric_version` is an integer wherever it appears (`DeterministicTrustSummary`, the reproducibility summary), the trust
`evidence.rubric` is the bare rubric name, and document headers (`format: {name, version}`) replace the `schema` strings of the release, archive,
export, ML-export and deposit documents; see the backend CHANGELOG for the full map.

## 0.129.0 - 2026-10-20

The contribution bundle surface is now named `ContributionBundle` in the shipped producer contract: the generated JSON Schema file is
`contract/schemas/ContributionBundle.schema.json` (it was `ContributionBundleV0.schema.json`), its `title` is `ContributionBundle`, and
`PRODUCER_CONTRACT.md` refers to the surface by the new name. The bundle's wire fields are unchanged: `bundle_format` is still
`tckdb-contribution-bundle` and `bundle_version` is still `"0.1"`, so a producer's bundle documents validate exactly as before. Only a
consumer that opens the old schema file name, or matches the old surface name, has to change.

## 0.128.0 - 2026-10-20

One new refusal code in the shipped producer contract, `unknown_manifest_schema` (409): a release manifest row names a format tag this
build has no renderer for, so the manifest operation (render, verify) is refused instead of guessing a shape. It can only be returned by
the release read and verify routes, not by an upload; no producer route and no upload payload changes.

## 0.127.0 - 2026-10-20

Twelve new refusal codes, all prefixed `structure_finding_`, and one new curator route in the shipped producer contract:
`POST /api/v1/curation/structure-evidence-findings` (a curator or admin appends a structure-evidence finding). It is a curator route, not a
producer route, so no upload payload changes; this release only adds the route and the codes to the shipped contract documents.

## 0.126.0 - 2026-10-19

One new model, `tckdb_schemas.fragments.version.VersionRef`: a stable `name` and a separate integer `version` (at least 1, a real
integer, never text). A name that already contains a version (`computed_thermo_v2`, `tckdb.archive.v1`, `manifest_0_3_0`) is refused,
because the version belongs in its own field. A name may use upper case and hyphens (a scientific rule id such as
`K-XYG3-B3LYP-BARRIER` is a name), and chemistry spellings such as `cc-pV5Z` or `VV10` are not read as versions. `embeds_version` and the shared patterns it uses are exported so the repository's guard
test and the model cannot disagree about what counts. No producer route and no upload payload changes; nothing in the existing
wire contract refers to the model yet.

## 0.125.0 - 2026-10-18

One new refusal code for the relative conformer energy comparison, `POST /scientific/species-entries/{ref}/conformers/relative-energies/compare`:
`relative_conformer_energy_compare_identical_protocols` (422; the candidate and the reference name the same level with the same effective recipe narrowing, so a
protocol would be compared with itself). A read route, so no producer route and no upload payload changes; this release only adds
the code to the shipped refusal-code reference.

## 0.124.0 - 2026-10-17

Three new refusal codes for the relative conformer energy read, `POST /scientific/species-entries/{ref}/conformers/relative-energies`:
`relative_conformer_energy_unsupported` (422; `context.reason` is `deferred_energy_kind` for enthalpy or Gibbs energy, or
`deferred_certificate` for a request for no minimum certificate), `relative_conformer_energy_population_too_large` (422; more than
500 visible conformer groups) and `relative_conformer_energy_scope_owner_mismatch` (422; `context.field` names a conformer group
that belongs to another species entry). A read route, so no producer route and no upload payload changes; this release only adds
the codes to the shipped refusal-code reference.

## 0.123.0 - 2026-10-13

One new refusal code, `superseded_record_not_approvable` (409): `PATCH /record-reviews/{type}/{id}` refuses moving a record that has
been replaced by a supersession edge back to `approved`. `POST /submissions/{id}/approve` does not return it: it leaves a replaced
member deprecated and lists it in the audit event. A curator route, so no producer route and no upload payload changes; this
release only adds the code to the shipped refusal-code reference.

## 0.122.0 - 2026-10-12

One new refusal code, `unknown_query_parameter` (422): `GET /scientific/statmech/search` now refuses a query key that is not a
declared filter instead of ignoring it. No producer route and no upload payload changes; this release only adds the code to the
shipped refusal-code reference.

## 0.121.0 - 2026-10-12

A geometry and a conformer may no longer disagree anywhere a calculation can state both (#635). Four new refusal codes:
`calculation_geometry_conformer_mismatch`, `transition_state_calculation_conformer_key`,
`transition_state_calculation_species_geometry` and `calculation_input_geometry_mismatch`.

Narrowing: payloads previously accepted are now refused. Exactly what is enforced:

- `calculation_geometry_conformer_mismatch` (422), on `/uploads/computed-reaction` and `/uploads/networks/pdep`:
  - a species `calculations[*]` entry whose `conformer_key` names one conformer and whose `geometry_key` names a geometry that
    belongs to another conformer of that species (anchored to the `conformer_key`'s conformer until now);
  - a conformer's own `calculation` (its primary) that states a `conformer_key` other than its conformer's `key`, or a
    `geometry_key` other than its conformer's geometry key. Until now the geometry key stored the output geometry on the other
    conformer's geometry row and the `conformer_key` was ignored. Stating the conformer's own keys, or neither, is unchanged.
    A key that names nothing keeps its own code (`conformer_key_undeclared`, `geometry_key_unresolved`), which a primary's
    `conformer_key` never had before.
  - A geometry key that two conformers both declare is left to the existing "geometry keys must be globally unique" rule.
  - `context` is `{field, calculation_key, conformer_key, geometry_key, geometry_conformer_key}`, local keys only.
- `transition_state_calculation_conformer_key` (422), on the same two routes: a `conformer_key` on a transition state's
  `calculation` or any of its `calculations` (a transition state has no conformers; the key was dropped).
- `transition_state_calculation_species_geometry` (422), on the same two routes: a transition-state calculation whose `geometry_key`
  names a species conformer's geometry (a saddle-point job cannot have run on a minimum). On `/uploads/computed-reaction` this
  was accepted.
- `calculation_input_geometry_mismatch` (422, raised by the workflow, where geometry rows are known): a `freq` or `sp` that states its
  geometry (`geometry_key` on `/uploads/computed-reaction`; the geometry of its conformer for a calculation inside a
  `/uploads/computed-species` conformer or an additional calculation of `/uploads/conformers`; the saddle point's geometry for an
  additional calculation of `/uploads/transition-states`) and also declares `input_geometries` that do not include that geometry, compared as
  the same structure (the rule energies are grouped by: a moved copy of an atom or a rigidly moved polyatomic structure is the same). An `opt` is exempt (its input is a starting guess). Until now the explicit inputs silently won.
  The comparison forgives only the rounding of the coordinates (about 1.7e-6 Angstrom at 6 decimals) and a rigid move or atom
  reordering of the same structure. Coordinates a program re-printed or symmetrised are a different structure and are refused:
  send the conformer's own xyz as the sp/freq input geometry.
- Unchanged: payloads whose keys agree, a lone `geometry_key` or `conformer_key`, a `geometry_key` naming no conformer's geometry
  (still `geometry_key_unresolved`).
- New helpers in `tckdb_schemas.bundle_source_rules`: `assert_calculation_geometry_matches_conformer`,
  `assert_conformer_primaries_agree`, `assert_transition_state_calculations_unanchored`,
  `conformer_key_by_unique_geometry_key` and `calculation_geometry_conformer_error`.

## 0.120.0 - 2026-10-11

The producer contract lists the two read routes of statmech selection (`POST .../scientific/statmech/select` and
`.../select/manifest`) and the refusal code `statmech_selection_scope_owner_mismatch` (422). No wire model changed.

## 0.119.0 - 2026-10-11

Review leftovers for stability sources, atomic warnings and network energy sources (#635, #631, #669, #744).

- Three new refusal codes. For an SCF stability `source_calculation_key` (#635): `scf_stability_source_cycle` (two verdicts
  that cite each other; `context.cycle` lists the keys in order, where it used to be an uncoded validation error) and
  `scf_stability_source_conformer_unanchored` (exactly one of the carrier and the named job is tied to no conformer, on
  `/uploads/computed-reaction`; it was already refused, late and as "a different conformer"). If neither is tied to a conformer
  the same-conformer rule cannot be checked, which is an absence: the link is stored as stated and the upload warns
  `scf_stability_source_conformer_unverified`. A transition state has no conformers and is never flagged. For `energy_ordering`
  evidence (#669): `ts_energy_ordering_source_type_mismatch` (an energy cited to a calculation of a type that does not carry it,
  such as an E0 from a single point; it was an uncoded validation error), sibling of `network_energy_source_type_mismatch`.
- Narrowing on `/uploads/networks/pdep` (#635): `scf_stability` no longer declares `source_calculation_key` at all, since that
  route cannot link it. A payload that sends the key, even as `null`, is now refused as an unknown field (`extra_forbidden`);
  before, `null` was accepted and a non-null key was refused by a custom message. Omit the key.
- Network energy sources (#669): a `well_freq` or `barrier_freq` source calculation must be a `freq`
  (`network_energy_source_type_mismatch`). A `composite` is now also a valid source of an `energy_ordering` energy, for
  an electronic energy and an E0 alike, as it already was for a network energy. A new warning,
  `network_energy_zpe_source_absent`, when a state or barrier energy stated with `electronic_plus_zpe`,
  `atom_and_bond_corrected` or `thermal_enthalpy_298k` cites only an `sp` or `opt` and the upload has no `freq` for that
  species or transition state.
- Warning codes (#631, #744): a single-atom `term_symbol` whose L or J is not the NIST ground term is now reported as
  `term_symbol_contradicts_ground_term`; `term_symbol_contradicts_multiplicity` is kept for a leading 2S+1 that disagrees
  with the declared multiplicity. The spin-orbit warning now says the spin-orbit-free value lies so many kJ/mol above the
  ground level. New `hessian_not_stored_not_frequency_calculation` (a `.hess` file sent for a calculation that is not a
  `freq`); an artifact whose log carries a force-constant block that yields no matrix now warns
  `hessian_not_stored_unparseable`, and inside a bundle every `hessian_not_stored_*` warning's `field` is
  `calculations['<key>'].artifacts`.

## 0.117.1 - 2026-10-11

Contract layout only (#686), plus a recorded limit.

- Shared blocks are keyed by the surfaces that own them: `Code group` and `Model group` headings and anchors are now a short
  hash of the owning surface set (`cg-3fa9c2`), independent of the order the surfaces are listed in, instead of a number by
  size and order, so adding a code no longer renumbers later groups. A link to a group of one names its only member.
- "Will be refused if" keeps a one-line gloss beside a code several surfaces share, as it already did for a code only one
  surface returns.
- A group's "Traced via" column names the surfaces that reach a code a way the rest do not (`(only on: ...)`).
- Recorded limit (#745): the same-order comparison that stops an energy duplicated on a moved copy has its own allowance of 50
  million work units per record, now charged 800 units per pair plus 30 per atom (it was 30 per atom, so a record of
  three-atom shapes could run it for about eleven seconds). Past the allowance a pair is compared only by the relabelling
  search, and failing that is counted as different.

## 0.117.0 - 2026-10-11

One new refusal code, `database_request_failed`.

- Any database error that is neither a conflict (`409`) nor an outage (`503`) -- a trigger that raises when a write
  commits, a malformed value the database refuses, a schema that has drifted -- now answers `500 database_request_failed`
  with the usual error envelope and no driver text, instead of an unformatted `text/plain` 500. Nothing from the request
  was saved. A retry of the same request meets the same refusal, so it is not a `503`.

## 0.116.0 - 2026-10-11

Breaking: the two producer write routes that took a row id in the path take the public ref only (#578).
`POST /api/v1/submissions/{submission_ref}/rights-attestations` takes the `sub_` ref (every upload, job and bundle
response returns it as `submission_ref`), and `POST /api/v1/calculations/{calculation_ref}/artifacts` takes the `calc_` ref
(`calculation_ref` beside `calculation_id`, or `calculation_key_refs` on a computed reaction). An integer id, in any spelling,
is refused with 422 `invalid_handle`; a ref of the wrong kind is still 422 `handle_type_mismatch` and an unknown ref 404
`handle_not_found`. The server's minimum supported `tckdb-client` is now 0.97.0, the first release that addresses a
calculation by its ref. No wire model changed; the route paths in the producer contract did.

## 0.114.0 - 2026-10-11

The producer contract lists three new statmech-selection rejection codes (`statmech_selection_bound_exceeded`,
`statmech_selection_unsupported`, `unknown_statmech_candidate_ref`). No wire model changed.

## 0.113.0 - 2026-10-11

A statmech record can declare the statistical model it supplies (`tckdb_schemas.statmech_declarations`), optional and stored as
made.

- `model_declaration` on `ConformerUploadStatmechPayload`, `StatmechInBundle`, `BundleStatmechIn` and the standalone
  `StatmechUploadRequest`: the scope (`atom`, `basin`, `explicit_equilibrium_ensemble`, or `ts_determination` for a
  transition-state record), who stands behind it (`literature` or `workflow_tool_release`), a recipe (energy zero, scale
  application and its contributions, vibrational scheme, projected and excluded modes, rotors and the modes they replace,
  symmetry, optical and electronic factors already counted, translational normalization, dividing surface, validity range), ensemble `members` (existing statmech records of
  the same species entry) and `sources` (a calculation by key or ref, imported literature parameters, or a frequency scale
  factor). A field left out is not stated: never unity, never projected, never counted. Version `1` only.
- New enums `StatmechModelScopeKind` and `StatmechModelSourceRole`; new refusal codes `statmech_model_declaration_invalid`,
  `statmech_model_declaration_version_unsupported`, `statmech_model_declaration_mismatch` and
  `unknown_structure_determination_ref`.

## 0.112.2 - 2026-10-11

Contract layout only: the refusal code reference now ships as its own file, `contract/REFUSAL_CODES.md`, beside
`PRODUCER_CONTRACT.md`, so the contract stays under its size ceiling (the reference was 104 KB of 686 KB).

- `PRODUCER_CONTRACT.md` keeps a short "Refusal code reference" pointer section, and every code link in it now reads
  `REFUSAL_CODES.md#c-<code>`. Each surface still lists its own refusal codes with the same one-line meanings; only the
  long per-code entries moved. Anchors are unchanged. A scientific check that names a code no producer route was
  traced to (an advisory flag, not a refusal) used to link an anchor that was never there; it is now named without a
  link.
- New `tckdb_schemas.contract.refusal_codes_path()` and `refusal_codes_markdown()`, and
  `python -m tckdb_schemas.contract --codes` (prints the path), mirroring `changelog_path()`.
- No model, enum, refusal code or rule changed.

## 0.112.1 - 2026-10-11

A network state energy stated as `electronic_plus_zpe` can now be held against an sp electronic energy plus a scaled
frequency ZPE, not only against a composite that stores an E0 (#693).

- `solve.state_energies[].source_calculation_keys[].zpe_calculation_key` (new, optional): the participant's own `freq`
  calculation whose zero-point energy is added to the energy of `calculation_key`. A non-`freq` calculation is refused
  with `network_energy_source_type_mismatch`; another species' freq with `network_energy_source_subject_mismatch`. A key
  that no calculation declares is refused like any undeclared calculation key.
- `solve.state_energies[].zpe_scale_factor` (new, optional, positive and finite): the factor the producer scaled the ZPEs
  with, to at least 4 decimals. The energy compared is `sum(stoichiometry * (E_sp + s * ZPE))`. Both fields are accepted
  only with `correction_convention: electronic_plus_zpe`.
- An atom's sp or opt counts as its E0 (no ZPE cited), read from the stored one-atom geometry of the cited calculation.
- With no `zpe_scale_factor` the state is stored as not compared (`zpe_scaling_unstated`): unity is never assumed, even
  when the unscaled sum agrees. A freq that stores no ZPE is `zpe_not_stated`; an sp and freq not at one geometry are
  `geometry_not_paired`; no ZPE is borrowed from a sibling calculation. A contradicted sum is refused with the existing
  `network_state_energy_sum_mismatch`, whose context gains `zpe_scale_factor`. No new refusal code.
- A `zpe_scale_factor` on a state where no participant cites a `zpe_calculation_key` is refused: it would scale nothing.
- `freq_zpe_hartree` (and the stored freq ZPE) is documented as the unscaled harmonic zero-point energy.
- Reads gain `zpe_calculation_ref` per state-energy source and `zpe_scale_factor` per state energy.

## 0.112.0 - 2026-10-11

New extrapolation formula `exponential_sqrt` for composite scheme terms: ORCA's SCF form
`E_SCF(X) = E_CBS + A exp(-alpha sqrt(X))` (`CompositeExtrapolationFormula.exponential_sqrt`,
`extrapolate_exponential_sqrt`). The term's `exponent` is ORCA's alpha and is required, so it is part of the scheme's
identity like an `inverse_power` exponent; the term reads the `reference` (or `total` of an HF level) component, like
`karton_martin_scf`. ORCA's optimised alpha: cc-pVnZ 4.42 (2/3) and 5.46 (3/4); pc-n 7.02 and 9.78; def2 10.39 and 7.88;
ano-pVnZ 5.41 and 4.48; saug-ano-pVnZ 5.48 and 4.18; aug-ano-pVnZ 5.12 (2/3). Reproduces the two SCF energies the ORCA
manuals print for water. Existing schemes and formulas are unchanged.

## 0.111.1 - 2026-10-11

Wording only: the refusal `energy_correction_scheme_frequency_level_not_applicable` and the `EnergyCorrectionSchemeRef`
docstring no longer say every kind other than the BAC kinds is "keyed on the energy level alone". A frequency level is
accepted only on `bac_petersson` and `bac_melius` for now; Arkane's isodesmic reference data is keyed on the
frequency//energy pair too, and the `isodesmic` kind will likely need it once it has defined content. No upload model,
enum or rule changed.

## 0.111.0 - 2026-10-11

A kinetics record's Arrhenius A-units are held to the order of the side its coefficient is a rate of (#695): the products for
a `reverse` record, the reactants for a `forward` one. A `net` record is not checked. A record that states no direction
could be either, and none is assumed: units matching EITHER side's order are accepted, units matching neither are refused
(`arrhenius_a_units_molecularity_mismatch`, whose message names both orders and asks for `direction`), and when the two
sides are the same size the check is unchanged. So the correct units of a reverse rate on a reaction with unequal sides
(the reverse of H2 -> 2H is bimolecular) are accepted, and the units of the wrong side are refused once `direction` is
stated. This covers the main line, every `multi_arrhenius` and PLOG entry, and `falloff.low_a_units` (one order above the
side). No upload model gained or lost a field.

## 0.108.1 - 2026-10-10

The producer contract now lists four models it had left out: `SchemeAtomParamPayload`, `SchemeBondParamPayload` and
`SchemeComponentParamPayload` (the inline parameter models of an energy-correction scheme) and `NetworkChannelMicroReactionIn`
(the `microreaction_paths` of a network upload `channels` entry). All four were always accepted; the generator read an unresolved
forward-reference annotation and dropped them. No upload model changed.

## 0.108.0 - 2026-10-06

The producer contract lists the six read routes of structure selection (`POST .../species-entries/{ref}/calculations/select`,
`.../conformers/select`, `.../transition-state-entries/{ref}/evidence/select`, each with `/manifest`). No upload model changed.

## 0.107.0 - 2026-10-05

New refusal code `structure_selection_manifest_too_large` (a structure decision whose replayable manifest exceeds its
bound is refused whole; service layer only). No upload model changed.

## 0.106.0 - 2026-10-05

New refusal codes `structure_selection_population_too_large`, `structure_selection_evidence_too_large`,
`structure_selection_traversal_too_deep`, `structure_selection_unsupported` and `unknown_structure_member_ref` (the bounds and
refusals of a structure assessment, service layer only). No upload model changed.

## 0.105.0 - 2026-10-05

A calculation can declare the recipe it actually ran, and a conformer or transition-state upload can declare structure
determinations (`tckdb_schemas.structure_declarations`), all optional and stored as made.

- `actual_protocol_declaration` on every calculation block (`CalculationPayload`, the bundle `CalculationIn` and
  `CalculationInBundle`): electronic state and root, spin treatment, relativistic treatment, effective core potential,
  core correlation, auxiliary basis, dispersion, solvation, constraints, material numerical approximations and the
  corrections the number includes. Each single fact is `known` (with a value), `unknown` or `not_applicable`; a fact
  left out is not stated, never read as a default or as gas phase. Version `1` only.
- `structure_determinations` on `ConformerUploadRequest`, `TransitionStateUploadRequest`, each `ConformerInBundle` of a
  computed-species upload and each `ConformerIn` and the `BundleTransitionStateIn` of a computed-reaction upload: a
  source-attributed claim about a geometry, conformer basin or saddle point that pins the upload's own calculations (by
  `key`) to the roles energy, geometry optimization, curvature, correction, connectivity and alternative characterization.
  The determination key (with its owner and source) is an identifier: stating it again with the same content resolves to the
  same determination, with different content it is refused.
- `ActualProtocolDeclaration.supporting_dois` is omitted (not stated) or a list, like the other list facts.
- `TransitionStateUploadRequest.primary_opt` and `additional_calculations` accept an optional `key` (new component
  `TransitionStateCalculationIn`), unique within the request, so a determination can name them. A plain
  `CalculationWithResultsPayload` built in Python is still accepted there (lifted, with no key).
- New enums `StructureDeterminationTargetKind`, `StructureDeterminationQuantity`, `StructureSourceRole`. New refusal
  codes `structure_declaration_invalid`, `structure_declaration_version_unsupported`, `structure_determination_invalid`
  and `structure_determination_mismatch`.

## 0.104.0 - 2026-10-04

New refusal codes `network_export_manifest_invalid`, `network_export_manifest_stale`, `network_export_choice_not_allowed`,
`network_export_representation_choice_invalid` and `network_export_unsupported_form`, and the producer contract's list of
read-only POST routes gains `POST /api/v1/scientific/networks/{network_ref}/kinetics/export-selected`. No upload model changed.

## 0.103.0 - 2026-10-04

The producer contract's list of read-only POST routes gains the two network selection routes
(`POST /api/v1/scientific/networks/{network_ref}/kinetics/select` and `.../select/manifest`). No upload model changed.

## 0.102.0 - 2026-10-04

New refusal codes `network_selection_population_too_large` and `network_selection_snapshot_too_large` (a network selection over a bound of the versioned engineering limits). No upload model changed.

## 0.101.0 - 2026-10-04

A network solve can declare its target, protocol and validation, and a fit its determination and representation
(`tckdb_schemas.network_declarations`), all optional, on `NetworkPDepUploadRequest`. New codes `network_declaration_invalid`,
`network_declaration_version_unsupported` and `unknown_network_solve_ref`. A declaration that contradicts the solve's own
bath, range, correction convention or units, or a fit's declared determination, is refused; alternates of one channel and
model kind must all declare their keys.

No wire model changes. Contract notes the read-only kinetics select endpoints.

## 0.99.0 - 2026-10-04

`BundleKineticsIn` can declare `energy_level_of_theory`, optional and stored as declared (as on `KineticsUploadRequest`). A
declared level that contradicts a linked energy calculation is refused with `kinetics_energy_level_contradiction`.

## 0.98.0 - 2026-10-04

The contract's "What changed" prints only the newest entries. Every entry now ships beside it as
`tckdb_schemas/contract/CHANGELOG.md`, which `--since` and `contract.changes_since` read. No upload model changed.
`contract.changes_since(version, text=...)`: `text`, when given, is now changelog markdown (`## <version>` headings),
not contract markdown.

## 0.97.0 - 2026-10-04

New refusal code `kinetics_selection_population_too_large` (a selection request over 500 visible records).

## 0.96.0 - 2026-10-04

Kinetics records can declare their determination, applicability and protocol (`tckdb_schemas.kinetics_declarations`),
all optional, on `KineticsUploadRequest` and `BundleKineticsIn` (which also gains `direction`). `reaction.reversible`
on `/uploads/kinetics` is optional: unstated is inherited or refused, never guessed (#598). New `kinetics_*` refusal codes and
`unknown_kinetics_determination_ref`, `unknown_network_channel`. A determination may carry a bundle-local `group`.

## 0.95.0 - 2026-10-04

A network state energy can name one source calculation per participant, and is held against their sum
(#678). One new optional field on `POST /uploads/networks/pdep`, one new block-tier refusal and two new
upload warnings. Existing payloads are accepted as before.

- **`solve.state_energies[].source_calculation_keys`** (new, optional): a list of
  `{species_key, calculation_key}`, one per participant of the state. Send it or the older
  `source_calculation_key`, never both (a plain 422). Each calculation passes the existing
  `network_energy_source_type_mismatch` type rule and must belong to *exactly* the participant it is
  listed beside, otherwise `network_energy_source_subject_mismatch`; a `species_key` that is not a
  participant of the state is refused the same way, at `...source_calculation_keys[j].species_key`.
  `source_calculation_key` is unchanged and still accepts the calculation of any one participant.
- **`energy_precision_kj_mol`** (new, optional, positive): the rounding unit of `energy_kj_mol`. State
  unrounded kJ/mol derived from hartree with 2625.499639, or state this field; precision is never
  inferred from the digits of the number. It is stored and read back.
- **`network_state_energy_sum_mismatch` (422, block).** When every participant of a state has a source,
  TCKDB compares the stated `energy_kj_mol` with `sum(stoichiometry * stored energy)` in three bands.
  Within the printed-precision tolerance `max(1e-6, 5e-7 * n)` hartree, `n = 1 + sum(stoichiometry)`, the
  energy **agrees**. Beyond it but within an honest-rounding allowance (half `energy_precision_kj_mol`,
  or half of 1 kcal/mol = 2.09 kJ/mol when not stated, per stated energy, plus `1e-6 * |energy|` on an
  absolute energy for the spread of hartree-to-kJ/mol constants) it is stored **not compared**
  (`stated_precision_unknown`) with a warning, so a value rounded to 0.1 kJ/mol, converted from kcal/mol
  to two decimals, or converted with 2625.5 or 627.509 x 4.184 is never refused; if you state
  `energy_precision_kj_mol` the rounding is accounted for and it agrees. Only beyond the allowance is it
  refused. Defined for `correction_convention: electronic_only` (an `sp`, `opt` or `composite`
  energy) and, only where every source is a `composite` storing an E0, `electronic_plus_zpe`.
  `energy_zero_convention: absolute` is compared directly. `lowest_state` and `entrance_channel` shift
  every state of the solve by one constant, so those are compared as *differences* between states
  (`n = 2 + sum(nu_i) + sum(nu_j)`); the state blamed is the outlier inconsistent with a majority of the
  others, and with two states `context` names both. `context` names the field, the state and both
  numbers; no database id.
- **`network_state_energy_sum_not_compared` (warning).** A sum that cannot be formed is never refused
  and never guessed: `atom_and_bond_corrected`, `thermal_enthalpy_298k` and `other` corrections
  (`convention_not_summable`), `electronic_plus_zpe` with an `sp`/`opt`/`freq` source
  (`zpe_not_in_source`), `separated_reactants` and `other` zeros (`energy_zero_not_comparable`), a
  source that stores no such energy (`stored_energy_not_stated`), a state alone on its shared zero
  (`no_second_state_on_the_same_zero`), and a stated number beyond printed precision but inside honest
  rounding (`stated_precision_unknown`). The outcome is stored with its reason. A state with no source
  at all is stored as not compared (`no_source_stated`) without a warning: nothing was claimed.
- **`network_state_energy_sources_partial` (warning).** A source on some but not all of a state's
  participants, which includes a single `source_calculation_key` on a multi-species state. It is
  accepted and read back as partial; no other participant's source is borrowed.
- **Reads.** `NetworkSolveStateEnergySummary` gains `sources[]` (`species_entry_ref`, `stoichiometry`,
  `calculation_ref`), `partial_sources`, `source_sum_comparison` (`agrees`, `not_compared` or null for a
  row deposited before the check), `source_sum_not_compared_reason` and `energy_precision_kj_mol`. `source_calculation_ref` is the
  older single slot, one summand of several on a multi-species state. Stored networks read as they did,
  with the new fields describing what they hold.
- Producers: the hydrazine ingester now cites every participant's single point for a multi-species state
  (`source_calculation_keys`) and a single participant's with `source_calculation_key`. A list on a
  one-participant state also fills the older single slot, so `source_calculation_ref` keeps the source.


## 0.94.0 - 2026-10-04

A Hessian's geometry, every scan point's geometry and every IRC point's geometry are now checked against the
subject their calculation is filed under (#680). No payload field is added, removed or changed.

- **Payloads that were accepted are now refused.** A wrong-element or wrong-isotope geometry at
  `hessian.geometry`, at `scan_result.points[N].geometry` or at `irc_result.points[N].geometry` (any point
  direction, including the TS-marker point and `both`) is refused with the existing codes
  `calculation_geometry_composition_mismatch` (422) and `calculation_geometry_isotope_mismatch` (422), the
  same ones already raised for input and output geometries; `context.field` names the path. Before, such a
  geometry was stored: a deuterated Hessian geometry under a protium species gave deuterium frequencies on
  reanalysis. The contract's per-route refusal lists are unchanged (the codes were already listed for these
  routes).
- A Hessian recovered from an uploaded artifact is checked too; there a mismatch never fails the upload, the
  Hessian is simply not stored.
- A species stored before the label-stripping change with isotope labels on its SMILES and no isotope key is
  read through that SMILES when its isotope content is compared.

## 0.93.0 - 2026-10-04

The same molecule with its atoms listed in another order is no longer a way round the no-optimisation
duplicate rule (#679, follow-up to #667). No field is added, removed or renamed; one payload that was
accepted is now refused, under the code the rule already used.

- **Two single points (or two composites) on one polyatomic structure are one duplicate whatever order
  the atoms are listed in (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes
  change).** With no `opt` linked, two `sp` (or two `composite`) links whose geometries are one structure
  moved rigidly are refused with `thermo_role_duplicate` / `statmech_role_duplicate` even when one lists
  its atoms in a different order, for example water with its hydrogens first, or the hydrogens of a CH3
  exchanged. The rule searches for a relabelling that lays one geometry on the other, using the
  geometries alone, and then applies the 0.80.0 comparison unchanged (Kabsch-aligned RMSD, tolerance from
  the precision of the coordinates). Only atoms of the same element and the same stated isotope are
  exchanged (`D`/`T` count as 2H/3H), and an enantiomer stays a different structure in any order.
- **The search is bounded, and past a bound a pair is treated as different** (the behaviour before this
  change): geometries over 200 atoms, a pair needing more than 256 trial alignments or examining more
  than 50,000 candidate placements, and a record's work beyond 4,096 alignments or 50 million work units
  are not decided. A duplicate energy on a reordered copy of such a structure is therefore still
  accepted. Ordinary molecules need one alignment.
- The same-order comparison of 0.80.0 and the one-atom rule of 0.74.0 are unchanged.

## 0.90.0 - 2026-10-03

No wire model changes. Contract notes the read-only thermo select endpoint.

## 0.89.0 - 2026-10-03

A `D`/`T` element spelling now means 2H/3H (#672, ADR 0022): **a `D`/`T` geometry on a protium species, previously accepted, is now refused**, `[2H]O[2H]` with it is accepted, and `isotopes` contradicting the spelling is refused as `geometry_isotope_symbol_conflict`.

## 0.87.0 - 2026-10-03

A geometry linked to a calculation is now checked against the isotopes of the subject the calculation is
filed under (#666). No payload field is added, removed or changed.

- New refusal code `calculation_geometry_isotope_mismatch` (422, ADR 0008 block), on every route that
  already runs `calculation_geometry_composition_mismatch`: thermo, statmech, conformer, computed
  species, computed reaction, transition state and network. The multiset of `(element, mass number)`
  substitutions on each calculation geometry (`geometry.isotopes`) must equal the one the species entry
  declares through its SMILES isotope labels, or, for a transition state, the sum over its reaction's
  reactants. Before, a deuterium geometry could be attached to a protium species and the reverse.
- The comparison is by count, not by atom: a calculation geometry carries no map to the species graph,
  so which atom carries a label is not checked. A `D` or `T` element spelling stays isotope-silent, as
  documented in `resolve_element_symbol`; only `geometry.isotopes` and SMILES labels count.
- Network and computed-reaction uploads: a transition state's `geometry.isotopes` is now read. It was
  accepted by the schema and dropped before the geometry was stored (contribution bundles take the
  computed-reaction route).
- A deposit whose calculation geometries carry no isotope labels under a species that declares some
  (for example `[2H]C` with an unlabelled CH4 opt input) is now refused; label the geometry.

## 0.86.0 - 2026-10-03

The layout of `PRODUCER_CONTRACT.md` changed; its content did not (#681). No field, rule, code or enum
changes. What a rule, a refusal code or a nested model says is now printed once and linked from each
surface that has it, instead of once per surface. Every surface still lists exactly the same refusal
codes, checks and nested models: a code or model several surfaces share sits in a numbered group
("Code group N", "Model group N") that each of those surfaces links, and a "Will be refused if" line
for such a code no longer repeats its sentence (the code reference has it).

## 0.85.0 - 2026-10-03

A thermo record can state what its values describe (`thermodynamic_target`) and how they were
produced (`protocol`, version 1): optional attributed claims, never inferred or defaulted, `null`
when omitted. Existing payloads are accepted unchanged. Fields and rules: `backend/schema_spec.md`,
"Thermodynamic target and protocol declarations". New codes (`thermo_target_group_*`,
`thermo_protocol_*`, `thermo_recipe_name_listed`, `thermo_declaration_invalid`) and the shared rule
`thermo_declaration_error`.

## 0.81.0 - 2026-10-03

TS energy-ordering energies are held against the stored energies (#638). No field is removed or changed;
`zpe_scale_factor` is added.

- **`ts_energy_ordering_stated_energy_mismatch` (422).** A stated energy that is not what TCKDB stores for
  its `source_calculation_key` is refused: `electronic` against the cited `sp`'s energy (or `opt`'s final
  energy), `e0` against the same participant's stored `electronic` energy plus the cited `freq`'s ZPE, when
  both are at one geometry. Tolerance `max(1e-6, 5e-7 * n)` hartree, n = 2 (electronic) or 3 (E0).
- **`zpe_scale_factor` (optional, `e0` energies only).** TCKDB stores your ZPE unscaled. An `e0` built as
  `E_electronic + s * ZPE` states `s` and is held to that sum (n = 2 + s + 100 * ZPE, which assumes `s` has
  at least four decimals: state it as multiplied, since 0.954 for a true 0.953649 can be refused). With no factor, an `e0` equal to `electronic + ZPE` agrees; any other is not refused
  but stored `not_compared` with reason `zpe_scaling_unstated`.
- **`transition_state_energy_ordering_not_compared` (warning).** An energy that cannot be compared (stored
  energy or ZPE not stated, no electronic energy to pair an E0 with, geometries not pairable) is accepted
  and reported, never read as agreement.
- Reads: compared energies gain `stored_energy_comparison`, `not_compared_reason` and `zpe_scale_factor`
  (null on earlier records). Uploads only.

## 0.80.0 - 2026-10-03

A rigidly moved copy of a polyatomic geometry is no longer a way round the no-optimisation duplicate
rule (#667, the polyatomic half of #623). No field is added, removed or renamed; one payload that was
accepted is now refused, under the code the rule already used.

- **Two single points (or two composites) on one polyatomic structure are now one duplicate, wherever
  the structure sits (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes
  change).** With no `opt` linked, two `sp` links (or two `composite` links) whose geometries are the
  same structure moved rigidly, translated and/or rotated, are refused with `thermo_role_duplicate` /
  `statmech_role_duplicate`; before, only the very same stored geometry was. "The same structure" is
  the same atoms in the same order (element and stated isotope) whose Kabsch-aligned RMSD is within
  the rounding of the coordinates as deposited (1.7e-6 Angstrom for coordinates written to six
  decimals, never more than 1.7e-4 Angstrom). An enantiomer is a different structure (a mirror image counts as the same only when a rotation superposes it atom for atom). A genuinely
  different geometry, such as another conformer or a bond length changed by more than the rounding,
  is unchanged and still accepted. The same atoms listed in a different order are still treated as
  different geometries (no canonical atom order exists for a bare geometry; deferred).
- The one-atom rule of 0.74.0 is unchanged.

## 0.79.0 - 2026-10-03

Network solve energy sources must belong to the subject they state an energy for (#668). No field or
enum member changes; the `source_calculation_key` description of a state energy and of a channel
barrier gains a sentence, and one new refusal code joins the catalogue.

- **`network_energy_source_subject_mismatch` (422).** `POST /uploads/networks/pdep` refuses a source
  cited for the wrong subject: a `state_energies[].source_calculation_key` that is not a calculation of
  a species of that state (any one of them for a bimolecular state), a `channel_barriers[]` source that
  is not a calculation of the barrier's own transition state (a species single point is the case that
  used to be stored), a `well_energy` link that is not a calculation of a species in one of the
  network's states, and a `barrier_energy` link that is not a calculation of one of its transition
  states. `context` names the field, the expected and the found kind of owner and, for a state or a
  barrier, the key of the state or transition state; no database id. Producers that cite the single
  point of the species in the state, or of the transition state (every producer known to us, the
  hydrazine ingester included) are unaffected. Uploads only: stored networks read exactly as before.

## 0.77.0 - 2026-10-03

Composite levels of theory, phase P7b (ADR 0021): a read-shape note only. No upload payload field is
added, removed or changed, and every payload accepted before is accepted unchanged.

- Reads: `LevelOfTheorySummary.label` is now always the full rendered label of the level, a string,
  never `null`: `method/basis` then each stated part of its identity, for example
  `CCSD(T)/cc-pCVTZ (core=all_electron)`. It is the same text as the notation of a record's levels and
  the ML export's label. Before, it was `null` on most reads and a slash-joined `method/basis` on a few,
  so a caller that treated it as the short form should read `display` instead (unchanged: `method/basis`).
- Reads: `LevelOfTheoryCoreBlock.label` is new, with the same text. The level-of-theory detail and browse
  reads carry it.
- The producer contract is unchanged except for its version line and this entry.

## 0.76.0 - 2026-10-03

Network solve energy sources are typed (#642). No field or enum member changes; the `source_calculation_key` of a state energy and of a channel
barrier gains a description, and one new refusal code joins the catalogue so a producer branching on
codes can learn it.

- **`network_energy_source_type_mismatch` (422).** `POST /uploads/networks/pdep` refuses a source that
  cannot carry the energy it is cited for: `state_energies[].source_calculation_key` and
  `channel_barriers[].source_calculation_key` when stated as `correction_convention: electronic_only`
  (accepted from `sp`, `opt` or `composite`), and, for any convention and for the `well_energy` and
  `barrier_energy` roles of `source_calculations`, any calculation that is not an `sp`, `opt`, `freq` or
  `composite` (so an `irc`, `scan`, `path_search` or `conf` is refused). `context` names the field, the
  stated convention or role, the accepted types and the type found. Producers that cite a single point
  (every producer known to us) are unaffected. Uploads only: stored networks read exactly as before.

## 0.75.0 - 2026-10-03

The producer contract now records that `/bundles/submit` and `/bundles/dry-run` apply the
frequency-list linearity check to the records in a bundle, and that a bundle reports the upload
warnings each record earns. Nothing a producer sends changes and every payload accepted before is
accepted unchanged; this release only adds.

- **Bundle warnings.** `/bundles/submit` and `/bundles/dry-run` return, in `messages`, the same
  upload warnings (code, message, field, order) that `POST /uploads/thermo` and
  `POST /uploads/kinetics` return for the same record, each with the upload's `local_ref`.
  The producer contract's rule listing gains `ContributionBundleV0` as a place the linearity check
  applies.

## 0.74.0 - 2026-10-03

A shifted copy of an atom is no longer a way round the no-optimisation duplicate rule (#623).
No field is added, removed or renamed; one payload that was accepted is now refused, and one
refusal changes its code.

- **Two single points (or two composites) on one atom are now one duplicate, wherever the atom sits
  (shared rule, so `/uploads/thermo`, `/uploads/statmech` and both bundle routes change).** With no
  `opt` linked, two `sp` links (or two `composite` links) on one structure are refused with
  `thermo_role_duplicate` / `statmech_role_duplicate`. For a geometry of exactly one atom the
  structure is the element (`D` and `T` count as hydrogen) and its stated isotope mass number, not the
  stored geometry: a second `sp` on a copy of the atom at another
  coordinate (`1\nH\nH 1.0 0.0 0.0` beside `1\nH\nH 0.0 0.0 0.0`) used to be a different geometry and
  returned 201, and is now refused. A geometry of two or more atoms is still its own structure, so
  two `sp` links on genuinely different polyatomic geometries are unchanged. Two single points at two
  levels on a shifted atom were never accepted: they used to be refused as
  `*_energy_level_ambiguous` and are now refused as `*_role_duplicate`, which is what the same
  geometry at two levels has always returned. The message for an atom now says "per atom".
- **The rule is stated for polyatomic sp-only links too.** It has always applied to a polyatomic
  species whose linked calculations are `sp`s with no `opt`, including on the standalone
  `/uploads/thermo` and `/uploads/statmech` routes; the workflow guide `depositing_a_thermo_record.md`
  now says so.
- **The producer contract no longer lists `atom_map_geometry_unparseable` against surfaces that only
  count atoms.** The atom counter used by the frequency-list and one-atom-primary rules swallowed
  that refusal internally but was traced as if it raised it, so eight surfaces (computed species,
  conformer, kinetics, network pressure-dependent, reactions, statmech, thermo and transport) named a
  code they cannot return. It is still listed where the atom-map rules raise it: the computed-reaction
  bundle and the transition-state upload. `/uploads/reactions` no longer lists it. Nothing a producer
  sends changes for this item.
- `xyz_block_shape` is new in `tckdb_schemas.fragments.reaction_atom_map`: it splits an XYZ block into
  its declared atom count and coordinate lines and never raises. `parse_xyz_elements` is built on it
  and behaves as before.

## 0.73.0 - 2026-10-03

Composite levels of theory, phase P7a (ADR 0021): the wire package gains the closed-form
coefficients of the linear extrapolation formulas. Nothing a producer sends changes and every
payload accepted before is accepted unchanged; this release only adds.

- **`composite_formulas.extrapolation_coefficients(formula, cardinals, exponent)`.** The signed
  weights `c_i` with `E_CBS = sum_i c_i * E_i` for the linear formulas (`inverse_power`,
  `inverse_power_shifted_half`, `karton_martin_scf`), in ascending cardinal order. They sum to 1 and
  their absolute values are what `extrapolation_weights` already returned. For
  `exponential_three_point`, whose limit is a ratio of differences of the energies and so has no fixed
  weights, it returns `None`; a degenerate or mis-shaped input raises `ExtrapolationError` like its
  siblings.
- **`composite_formulas.is_linear_formula(formula)`.** `True` for the three two-point formulas,
  `False` for `exponential_three_point`.
- The producer contract is unchanged except for its version line.

## 0.72.0 - 2026-10-02

Composite levels of theory, phase P5 (ADR 0021): a composite energy you build
yourself, from your own recipe and the single points you ran, is deposited as an
`assembled` composite. Every payload accepted before is accepted unchanged; this
release only adds. See the "Worked payloads" section of the producer contract
for two complete examples (CCSD(T)/CBS from a TZ/QZ pair, and a focal-point sum).

- **`level_of_theory.composite_scheme`.** A level of theory now carries exactly
  one of `method` and `composite_scheme`. `method` stays required whenever
  `composite_scheme` is absent, so every existing payload is unchanged;
  `method` is now optional in the schema only because the other member exists.
  Both, or neither, is refused (`level_of_theory_method_with_composite_scheme`,
  `level_of_theory_requires_method_or_composite_scheme`). The definition:
  `kind` (`extrapolation` or `additive`; `named_method` is the server's own and
  is refused with `composite_scheme_named_method_not_sendable`), `terms[]` and an
  optional `literature`. A term has a local `key`, an `operation` (`base`,
  `value`, `extrapolation`, `difference`; `empirical` is refused), an
  `energy_component`, a `formula` and `exponent` on an extrapolation, and
  `inputs[]`, each a `slot` (`value`, `high`, `low`, `cardinal`), an ordinary
  `level_of_theory` and a declared `cardinal_number`. The total is the sum of the
  terms. Formulas: `inverse_power` and `inverse_power_shifted_half` (with an
  exponent), `karton_martin_scf` and `exponential_three_point` (without). Shape
  mistakes are `composite_scheme_malformed` with `context.rule` naming which; a
  nested composite, or a named composite method such as CBS-QB3 used as an input
  level, is `composite_scheme_nested`.
- **Identity.** The server resolves the input levels, hashes the definition and
  names the level of theory itself (for example
  `CBS[ref:CCSD(T)/cc-pVQZ + corr:CCSD(T)/cc-pV{T,Q}Z; inverse_power x=3; n=3,4]`).
  Formula, exponent, cardinal numbers, the input levels (with `core_treatment`)
  and the term order are identity; term keys and literature are not.
- **Assembled composites.** `composite_result.assembly: "assembled"` is accepted
  with `inputs[]`: each names a `term_key`, a `slot` (and `cardinal_number` on an
  extrapolation) and the calculation by a bundle-local `calculation_key` or a
  `calc_...` `calculation_ref`, never a database id. `inputs` on a `program_run`
  is refused (`composite_inputs_require_assembled`); an assembled composite at a
  level that carries no inline scheme keeps the code
  `composite_assembled_not_accepted`, now meaning exactly that.
  `software_release` is optional on an assembled composite and required on every
  other calculation (`calculation_software_release_required`, a coded refusal in
  place of the schema's missing-field error); a `program_run` composite still
  needs it.
- **Server-side refusals** (422): `composite_input_missing`,
  `composite_input_slot_unknown`, `composite_input_duplicate`,
  `composite_input_reference_invalid`, `composite_input_edge_is_derived` (the
  `composite_input` dependency role is written by the server and cannot be
  declared in `depends_on`), `composite_input_type_invalid` (an input is not a
  single point or optimisation), `composite_input_level_mismatch`,
  `composite_input_owner_mismatch`, `composite_input_geometry_mismatch`,
  `composite_total_required` (an assembled composite deposits its total; there is
  no unverifiable-by-absence), `composite_assembled_cannot_be_primary` (an
  assembled composite is not the run that produced a geometry; send it as an
  additional calculation) and `composite_total_mismatch` (the deposited total is not
  what the scheme gives for the inputs' stored energies, beyond
  `max(1e-6, 5e-7 * (1 + sum |weight|))` hartree, the sum over the stored numbers
  consumed: weight 1 for a value, base or difference input, the extrapolation's own
  weight for an extrapolated one, so `max(1e-6, 5e-7 * n)` when every weight is 1).
  TCKDB recomputes the total only to check it; it never stores the recomputed value.
  Warnings: `composite_input_geometry_undeclared` and `composite_total_unverifiable`
  (a needed energy or component is not stated, the correlation convention cannot be
  determined, or an extrapolation is degenerate).
- **Correlation and triples.** A `correlation` term reads the whole correlation
  energy, (T) included: the stored `correlation` where it already includes (T)
  (ORCA: `reference + correlation` equals the energy), `correlation + triples`
  where triples are stored separately (Molpro). The new
  `EnergyComponentKind.correlation_excluding_triples` reads the CCSD part: the
  stored `correlation` under Molpro's convention, `correlation - triples` under
  ORCA's. The textbook scheme (CCSD correlation extrapolated, (T) at a smaller
  basis as its own `triples` term) is written with it and counts (T) once. It is
  derived and never a stored single-point component (`sp_energy_component_derived`).
  The convention is read off the stored row; where it cannot be read the total is
  unverifiable, never guessed.
- **Term order is not identity.** The total is a sum, so terms sent in any order are
  one scheme; stored positions are canonical and the positions in
  `composite_result.terms` are mapped to them. A `cardinal_number` on a slot that is
  not a `cardinal` slot is refused (`composite_scheme_malformed`,
  `rule: cardinal_on_non_cardinal_slot`).
- **New public modules.** `tckdb_schemas.composite_formulas` (the four formulas,
  checked against the correlation energies printed in the ORCA manuals),
  `composite_total` (the recomputation, pure arithmetic a producer can run before
  sending) and `composite_worked_examples`.
- **New enums mirrored from the server:** `CompositeSchemeKind`,
  `CompositeTermOperation`, `CompositeExtrapolationFormula`,
  `CompositeInputSlot`, and `CalculationDependencyRole.composite_input`.

## 0.71.0 - 2026-10-01

Composite levels of theory, phase P3b (ADR 0021): a `composite` calculation's
deposited energy is compared with the Gaussian output log attached to it. No
payload field changes and every payload accepted before is accepted unchanged;
this release only adds three warnings to upload responses.

- **New warning `composite_energy_log_available`** (informational): the
  `composite_result` stated no energy and the attached log's summary block
  states E0 and the recipe ZPE. Nothing is filled; the message carries the
  numbers so the producer can send them.
- **New warnings** (the upload is accepted; the deposited values are kept
  exactly as sent, and nothing is filled from the log):
  `composite_energy_log_mismatch` (an attached Gaussian output log's summary
  block states a different `e0_hartree`, `recipe_zpe_hartree` or
  `electronic_energy_hartree` than the `composite_result`, beyond printed
  precision, `max(1e-6, 5e-7 * n)` hartree) and `composite_log_method_mismatch`
  (the log is a different composite method from the calculation's level of
  theory; the energies are then not compared).
- **What ARC (or any producer) must attach:** the Gaussian output log of the
  composite run as an `output_log` artifact on the `composite` calculation.
  Logs of CBS-QB3, ROCBS-QB3, CBS-4M and G3 are read; other composite methods,
  G4 and G4MP2 included, are not compared (no warning either way). G4 and G4MP2
  are declined because the Gaussian 16 Rev A.03 summary labels are shifted by one
  pair, so the number under `G4(0 K)` is not E0.
- An `sp` at a composite level whose attached log is a composite job still gets
  no single-point energy, as before.

## 0.70.0 - 2026-10-01

Composite levels of theory, phase P3a (ADR 0021): a calculation of type
`composite` records one program-run composite energy such as CBS-QB3 or G4.
Every payload accepted before is accepted unchanged; this release only adds.

- **New calculation type `composite`, with a `composite_result` block.**
  `type: "composite"` is accepted on the conformer upload, the thermo and
  statmech uploads' inline calculations, and the computed-species and
  computed-reaction bundles (the shared bundle-local calculation shape that the
  network upload also uses carries the block too, but that route has no test
  for it yet). It carries `composite_result`: `assembly`
  (`program_run`, or `assembled`, which the server refuses until user-built
  schemes arrive), the optional energies `electronic_energy_hartree` (ZPE-free,
  every recipe term included), `e0_hartree` (0 K, including the recipe's scaled
  zero-point energy) and `recipe_zpe_hartree`, and an optional `terms` list of
  `{term_position, value_hartree}`. A `null` energy means not stated. TCKDB never
  stores a total it computed itself.
- **The two always come together.** `composite_result` on any other type is
  refused with `composite_result_requires_composite_type`, and a `composite`
  calculation without it with `composite_type_requires_composite_result`.
- **Two arithmetic checks, blocking, to printed precision.** The tolerance is
  `max(1e-6, 5e-7 * n)` hartree, where `n` counts the rounded numbers in the
  equation (Gaussian prints each to six decimals): 3 for e0, `len(terms) + 1`
  for the terms. With all three energies
  present, `e0_hartree` must equal `electronic_energy_hartree +
  recipe_zpe_hartree` (`composite_e0_inconsistent`); with terms given and the
  total present, the terms must sum to `electronic_energy_hartree`
  (`composite_terms_do_not_sum`).
- **Server-side refusals** (422): `composite_assembled_not_accepted` (the
  assembled form arrives with user schemes), `composite_level_not_scheme_bound`
  (the level of theory must be a catalogued named method such as `CBS-QB3`),
  `composite_program_run_requires_software`, and
  `composite_term_position_unknown` (a term names a position the scheme does not
  have; a named method has none, so any position is accepted there).
- **A composite may be a conformer's primary calculation.** A species of two or
  more atoms may send, as `primary_calculation` / `calculation`, a program-run
  named composite that produced the geometry, in place of the `opt` (the `opt`
  rule is otherwise unchanged). With no `output_geometries` declared, the
  conformer's geometry is its final output, as for an `opt`.
- **A freq, sp or scan may depend on a composite that has an output geometry**
  (`depends_on` role `freq_on`, `single_point_on`, `scan_parent`), as on an `opt`.
- **Two energies refused.** A statmech or thermo record that links both an `sp`
  and a `composite` as its energy is refused with
  `statmech_energy_sp_and_composite_linked` /
  `thermo_energy_sp_and_composite_linked`. The existing role-consistency rules
  (`*_role_duplicate`, `*_sp_geometry_mismatch`, `*_energy_level_requires_sp`,
  `*_energy_level_ambiguous`, `*_energy_level_contradiction`) now apply to a
  linked `composite` as to an `sp`, and the energy level of a record is the
  composite's when one is linked (composite, then sp, then opt, then imported).
- **New warnings** (the upload is accepted): `named_composite_deposited_as_opt`
  and `named_composite_deposited_as_sp` (an `opt` or `sp` at a named composite
  method's level: send it as `composite`; refused once producers can),
  `composite_role_on_non_composite_calculation` (the source role `composite` on
  a calculation of another type) and
  `composite_frequency_level_differs_from_recipe` (a linked `freq` at a level
  other than the composite scheme's own frequency level).
- **Reads:** a calculation read returns `composite` (assembly, energies, terms)
  under `results`; a levels summary gains `geometry_source` and
  `frequency_source` (`composite_recipe` when the level is the named method's own
  internal level). Server responses, not producer payloads.
- Not in this release: assembled composites and user-built schemes, energy
  components on a single point, a Gaussian composite-log parser.

## 0.69.0 - 2026-10-01

Composite levels of theory, phase P4 (ADR 0021): energy components on single
points, and a core-treatment field on the level of theory. Both are additions;
every payload accepted before is accepted unchanged and means the same thing.

- **`sp_energy_components[]` on single points.** `CalculationWithResultsPayload`,
  the computed-species `CalculationInBundle` and the flat `CalculationIn` (the
  computed-reaction and network bundles) take a list of
  `{component, value_hartree}`, where `component` is one of `total`,
  `reference` (the SCF / HF energy), `correlation`, `triples`, `dboc` or
  `scalar_relativistic`. The value is what the program printed. Five refusals,
  each with a code and context:
  - `sp_energy_component_not_on_sp`: the calculation is not a single point.
  - `sp_energy_components_require_energy`: the single point's electronic energy
    is not stated. The parts come from the same output as the energy, so state
    it; a log can no longer fill the energy in after the parts were checked.
  - `sp_energy_component_duplicate`: one value per component.
  - `sp_energy_component_total_mismatch`: a `total` must equal the energy
    within 1e-6 Eh.
  - `sp_energy_components_do_not_sum`: `reference + correlation` must equal the
    energy within 1e-6 Eh. When a `triples` component is also sent,
    `reference + correlation + triples` may match instead (ORCA's correlation
    energy already includes (T); Molpro prints CCSD and (T) separately). The
    refusal reports both sums. On F12 methods, `reference` must include the
    CABS-singles correction if the program's total does.

  The server compares and never stores a value it computed. A single-point read
  returns the components under `results.sp.energy_components`.
- **`LevelOfTheoryRef.core_treatment`** (optional): `frozen_core` or
  `all_electron`. State it only when the run says so. It is part of the level's
  identity **only when stated**, so a payload that omits it resolves to exactly
  the level it always did. Frozen-core and all-electron CCSD(T)/cc-pCVTZ are now
  two levels instead of one. A partial treatment (an energy window, Gaussian's
  `FC=1`) has no value yet; leave the field out and describe it in `keywords`.
- New public enums `CoreTreatment` and `EnergyComponentKind`.
- Reads: `LevelOfTheorySummary` and the level-of-theory detail carry
  `core_treatment` (`null` = not stated).

## 0.68.0 - 2026-10-01

Composite levels of theory, phase P2 (ADR 0021): the server now records the
recipe behind a named composite method. No upload payload field is added,
removed or changed, and every payload accepted before is accepted unchanged.

- **A level of theory that names a catalogued composite method is bound to its
  recipe.** `CBS-QB3`, `G4`, `W1U` and the other methods in the server's
  catalogue (`cbsqb3` and the other aliases reach the same one) now resolve to a
  level of theory bound to a `named_method` composite scheme. The level's hash
  and ref are unchanged, so a level sent before and after is the same row. A
  method that is not in the catalogue is bound to nothing.
- **Reads gain the recipe.** `GET /scientific/composite-schemes/{ref}` returns a
  scheme (`csch_...` ref), and every level-of-theory summary on a read carries
  `composite_scheme` (`{composite_scheme_ref, kind, name}`), `null` for an
  ordinary level. These are server responses, not producer payloads.
- **New public-ref prefix `csch_`** (content-derived: the same recipe has the
  same ref on every instance).
- Not in this release: sending a scheme of your own, the `composite`
  calculation type, and energy components. Those are later phases and change
  the upload contract when they land.

## 0.67.0 - 2026-10-01

`level_of_theory.method` guards for named composite methods (ADR 0021). Every
payload that was accepted and meant one method is unchanged.

- **`//` is refused in `method`.** `LevelOfTheoryRef.method` containing `//`
  (ARC's `energy//geometry` shorthand, `"ccsd(t)-f12/cc-pvtz-f12//b3lyp/def2tzvp"`)
  is refused with code `level_of_theory_method_is_compound` and `context`
  `{field: "method", value}`. It is two levels of theory, not one method: send
  the single-point and the optimization levels as separate calculations, each
  with its own level of theory. A single `/` is still accepted.
- **Correction-table names warn.** A named composite method followed by a
  correction-table label or a year (`cbs-qb3-paraskevas`, `cbsqb32023`) is
  accepted with an upload warning `level_of_theory_method_names_correction_table`
  at `...level_of_theory.method`. These names select Arkane AEC/BAC parameters,
  not a method: the calculation that ran is CBS-QB3. The name is stored as sent,
  as a separate level of theory from the method, and is never aliased. Send the
  method and name the table on the energy correction scheme. A later release
  will refuse these once the producers send the method.
- **New helpers.** `collect_ref_warnings` walks a validated request and returns
  the software-release version warnings and these method warnings together;
  `collect_software_release_version_warnings` is unchanged.
  `correction_table_method_stem` recognises the shape.

Server side, in the same change: `cbsqb3`, `rocbsqb3`, `cbs4m` and `cbsapno`
now key to the hyphenated spellings, and `g4(mp2)`, `g3(mp2)` and `g3(mp2)b3`
to `g4mp2`, `g3mp2` and `g3mp2b3`, so a level of theory written either way is
one row. `W1`, `W1U`, `W1BD` and `W1RO` stay four methods and `CBS-QB3` and
`ROCBS-QB3` stay two. A producer that hashes level-of-theory identity locally
must adopt the same aliases to agree with the server.

## 0.66.0 - 2026-10-01

Frequency level on correction schemes (composite-levels plan P6). One optional
field, and one new warning. A payload written against 0.64.0 is still valid and
means what it meant.

- **`EnergyCorrectionSchemeRef.frequency_level_of_theory`**
  (`LevelOfTheoryRef | null`). The level of theory the frequencies were computed
  at, for a scheme keyed on an `energy//frequency` pair. Arkane keys Petersson
  and Melius BAC (and only those; atom energies are keyed on the energy level
  alone) on `CompositeLevelOfTheory(freq=..., energy=...)`; a scheme held only one level of
  theory, so the frequency half was lost and two such schemes either collapsed
  into one row (identical tables) or were refused as a value conflict (different
  tables). Send the `energy` half in `level_of_theory` as before and the `freq`
  half here. It joins scheme identity, in both identity forms (with and without
  `data_revision`): the same energy level with two different frequency levels is
  two schemes. An absent value is a value of its own: a scheme sent without it
  never matches one that has it, keeps exactly the identity it had before the
  field existed, and keeps its public ref byte for byte. It is resolved like
  `level_of_theory`, so a level that was merged into another resolves to the one
  that holds it. Adapters: for an Arkane `energy//freq`-keyed BAC, send both halves; for
  an atom-energy scheme or a scheme keyed on one level, send nothing new.
  Three rules, each stated where it applies: the field is **refused** on any
  kind other than `bac_petersson` and `bac_melius`
  (`energy_correction_scheme_frequency_level_not_applicable`) and without
  `level_of_theory` (`energy_correction_scheme_frequency_level_without_energy_level`),
  and a frequency level that resolves to the same level of theory as the energy
  level is **stored as absent**, so one table cannot become two schemes by
  spelling its level twice.
- **`composite_delta_prefer_scheme_terms` warning.** A new applied correction
  with `application_role = "composite_delta"` is stored as sent and answered
  with this warning (field `application_role`). Focal-point deltas
  (core-valence, higher-order triples, relativistic, DBOC) are going to become
  composite-scheme terms, which can name every calculation a delta is built from
  and sit on the energy instead of the species entry. It is a warning and not a
  refusal; no payload that was accepted is refused.

Scientific reads gain `frequency_level_of_theory` beside `level_of_theory` (a
level-of-theory summary, `null` when the scheme is keyed on one level) on the
scheme detail and search records.

## 0.65.0 - 2026-10-01

A single atom may be deposited with an `sp` primary on the pressure-dependent
network bundle too (#615). Every existing payload is unchanged.

- **`POST /uploads/networks/pdep`: a one-atom conformer may send its `sp` as
  `calculation`.** The two other bundle routes accepted this already (#610);
  the network route still required an `opt`, so the H atom of a hydrazine-style
  network could not be sent honestly. The rule is the same one: a conformer
  whose geometry is exactly one atom may carry `type: "sp"` as its primary, with
  `sp_electronic_energy_hartree`; an `sp` on two or more atoms, an uncountable
  geometry, or any type other than `opt`/`sp` is still refused with the same
  message. The atom's statmech `source_calculations` link that `sp` with role
  `sp`, and the solve's `source_calculations` and `state_energies` name it. A
  relabelled `opt` on an atom is still accepted. Transition states still require
  an `opt` primary. Only the producer contract's description of the network's
  conformer changed; no field was added or removed.
- **Duplicate single points are now refused when they declare output-only
  geometry (shared rule, so `/uploads/statmech` and `/uploads/thermo` change
  too).** With no `opt` linked, two `sp` links on one geometry are refused with
  `statmech_role_duplicate` / `thermo_role_duplicate`. The rule used to read an
  `sp`'s input geometry only; it now reads its input geometry, else its output
  geometry. A deposit whose `sp` declares only an output geometry on the same
  geometry as another linked `sp` therefore changes: the same level of theory
  used to return 201 and now returns the `*_role_duplicate` refusal; a different
  level used to return `*_energy_level_ambiguous` and now returns
  `*_role_duplicate`. This is what two input-linked `sp`s already got.

## 0.64.0 - 2026-10-01

Transition-state contract additions (#621). Every field is optional and
every existing payload is unchanged.

- **Two more kinds of transition-state validation evidence.**
  `TransitionStateValidationEvidenceIn.kind` was `"irc"` only. It now also
  accepts `"energy_ordering"` (the saddle point lies above both wells) and
  `"imaginary_mode"` (what the frequency calculation found). At most one record
  per kind. An `energy_ordering` record carries `energies`, one per participant
  (`"ts"`, `"reactant:N"`, `"product:N"`), each with an `energy_kind`
  (`"electronic"` or `"e0"`, which are never compared with each other), an
  `energy_hartree` and the `source_calculation_key` it was taken from, which
  must belong to that participant. An `imaginary_mode` record carries
  `imaginary_frequency_count`, `imaginary_frequency_cm1` (negative) and
  `mode_displacement_agrees` (your displacement check's verdict, null when not
  assessed). A pass that the record's own *stated* numbers contradict is refused, a
  field is refused on a kind it does not describe, and only a passing `irc`
  record silences `transition_state_missing_irc_evidence`. `energy_ordering`
  is accepted on the computed-reaction and pressure-dependent bundles and
  refused on the standalone transition-state upload, which has no
  calculations for the wells; `imaginary_mode` binds there to the single
  `freq` additional calculation. Energies are finite and not positive (absolute,
  in hartree; zero is exact for the bare proton), `imaginary_frequency_cm1` is finite, and the database refuses
  NaN and infinities too. An `electronic` energy must come from an `sp` or
  `opt` calculation and an `e0` from a `freq`; one energy kind taken at more
  than one level of theory is accepted with a
  `transition_state_energy_ordering_mixed_levels` warning. An `imaginary_mode`
  count or frequency that disagrees with the frequency result it cites is
  refused, and a pass with more than one imaginary mode needs that result to
  designate the reaction coordinate. Stated energies are not reconciled with
  the energies stored on the cited calculations.
- **Transition-state statmech on the reaction bundle.**
  `BundleTransitionStateIn` gains `statmech`, the block a species carries on
  the computed-species route, written through the same code, so the same rules
  apply (source roles, the three energy levels, scale-factor resolution).
  Its `source_calculations` and torsion scans must be the saddle point's own
  calculations. The response gains `transition_state_statmech_id`, and a
  transition-state entry read serves it under `include=statmech`.
- **Standalone transition-state route.** `additional_calculations` now accepts
  `scan`, and `CalculationWithResultsPayload` gains `scan_result` (type `scan`
  only) so the points travel with it. That payload is shared, so this is a
  cross-route addition: a route that already accepted a `scan` calculation
  (the conformer route does not restrict its primary calculation's type) now
  stores the scan's points, and a route that does not allow `scan` is unchanged. The request gains
  `applied_energy_corrections` (no source keys or frequency scale factor, since
  the payload has no key namespace), and `atom_map`, with a `key` and a
  `geometry` on each reaction participant and a `geometry_key` for the saddle
  point, because a map counts its indices into geometries.
- **An IRC result may leave its direction and flags unstated.**
  `IRCResultPayload.direction`, `has_forward` and `has_reverse` are now
  optional. Unstated is stored and read back as null, never as `false`; a flag
  stated `false` against points of that direction is still refused.

## 0.63.0 - 2026-09-30

The Arrhenius reference temperature, and the reaction bundle's kinetics block
now takes the same evidence as the standalone route (#620). Both are additions:
a payload valid under 0.62.0 is valid under this release, byte for byte.

**`t0_k`** on `BundleKineticsIn` (`POST /uploads/computed-reaction`) and on
`KineticsUploadRequest` (`POST /uploads/kinetics`): the reference temperature
T0 of the scalar rate, in K, meaning `k = A (T/T0)^n exp(-Ea/RT)`. It defaults
to 1 K, which is the plain `A T^n` form and what every record deposited
before this release meant. Before, a producer that fitted with T0 = 298 K had
to send `A / 298^n` and the T0 it fitted with was lost. It must satisfy
`0 < t0_k <= 10000`. It applies to the record's own `a`, `n` and `reported_ea`
of a modified-Arrhenius rate. Falloff (`lindemann`, `troe`, `sri`), `plog`,
`chebyshev` and `multi_arrhenius` records are always at 1 K and the standalone
route refuses any other `t0_k` on them: their low-pressure limit or child rows
carry no T0 of their own (Arkane and RMG give each falloff limit an independent
T0), so one value would silently mis-state part of the rate. The server stores `a` as sent (it is A at T0, not A rescaled) and
serves `t0_k` back; a consumer that evaluates k(T) from a stored `a`, `n` and
`ea_kj_mol` must use it.

**`interpretation_assignments`, `tunneling_application`, `network_kinetics_ref`**
on `BundleKineticsIn`. They are the standalone route's own models and cross-field
checks, now defined once in `tckdb_schemas.fragments.kinetics_evidence`
(`KineticsInterpretationAssignmentUpload`, `KineticsTunnelingApplicationUpload`,
`ConformerSelectionContentRef`), and the server writes them with the same
persistence code, so the two routes refuse the same interpretation and tunneling
mistakes with the same codes (a coded 404 for an unknown statmech, transition
state, calculation, artifact or network ref, the same 422 for an incomplete
interpretation set or a tunneling block that disagrees with `tunneling_model`).
Two things follow from how a bundle is built and are part of the contract:
every reference is the public ref of a record deposited *earlier* (a bundle
cannot cite a statmech, transition state or calculation it is itself creating,
because the depositor cannot know its ref in advance), and a cited transition
state must be a transition state of this rate's reaction: a reaction entry of
the same reaction with the same structure participants (same species entries,
either direction), so the excited-state or isotopologue entry's transition state
is refused. A bundle mints its own reaction entry, so the entry itself can
never match. The bundle's `tunneling_model` is filled from
`tunneling_application.model` when only the evidence is sent, as on the
standalone route.

Three wire enums are now mirrored here, so the evidence models can live in this
package: `KineticsEnsemblePolicy`, `KineticsStandardStateConvention` and
`KineticsDegeneracyInterpretation`, with the same members and order as the
backend's.

## 0.62.0 - 2026-09-30

Four changes for correction-scheme provenance (#619), all optional and
backward compatible: a payload written against 0.61.0 is still valid and means
what it meant.

- **`EnergyCorrectionSchemeRef.data_revision`** (`str | null`, at most 200
  characters). The revision of the data that holds the parameter tables, for
  example the RMG-database commit holding Arkane's atom-energy and BAC tables.
  When present it joins the scheme's identity and the workflow-tool build stops
  being part of it: identity is `(kind, name, level_of_theory,
  source_literature, software, data_revision)`. Two builds that read the same
  revision's tables are one scheme (the first depositor's build is kept as
  provenance), and a new revision is a new scheme, so a one-parameter change in
  a new database revision is no longer refused as a value conflict. When absent
  the identity is exactly what it was, including the tool build, so every scheme
  already deposited keeps its identity and its public ref. A deposit with a
  revision never matches one without, even when the tables are identical. A value
  of 7 to 64 hex digits is lower-cased as a git commit; any other value is kept
  as written. Adapters: send the RMG-database commit here, keep stamping the tool
  release, and expect the tool release to stop splitting schemes.
- **`EnergyCorrectionSchemeRef.atom_params_applied_as`** (`subtracted` |
  `added`, new enum `AtomParamApplication`). How the scheme's `atom_params`
  enter the corrected energy. It covers every entry of `atom_params` and nothing
  else, requires `atom_params` to be present, and is not inferred when omitted.
  A value that differs from the one stored on the matched scheme is refused like
  a differing parameter value; a value sent for a row that has none is stored.
  Arkane's `atom_energy` tables are `subtracted` (`count * value` is removed
  from the energy), its `atom_hf` tables are `added`, and its `atom_thermal`
  tables are `subtracted`, because Arkane applies
  `+ count * (atom_hf - atom_thermal)` per atom. The first deposit of a scheme
  fixes its sign: a later differing value is refused.
- **`SchemeAtomParamPayload` now documents its meaning and unit.** `value` is in
  the scheme's `units`. For `kind=atom_energy` it is the level's atomic energy of
  `element`; `atom_hf` is the atom's experimental enthalpy of formation;
  `atom_thermal` its thermal enthalpy increment; `soc` its spin-orbit correction.
  No field changed; the contract text did.
- **`energy_level_of_theory` is now stored.** On thermo and statmech blocks
  (every bundle root, `/uploads/thermo`, `/uploads/statmech`, and the statmech
  nested in `/uploads/conformers`) the declared level was checked against the
  linked calculations and discarded. It is now stored as declared and read back as
  `levels.declared_energy` on the scientific thermo and statmech reads. It is
  separate from `levels.energy`, which is still derived from the linked
  calculations on every read. It stays `null` when nothing was declared, and is
  never back-filled. No request field changed.

Scientific reads gain `levels.declared_energy`,
`energy_correction_scheme.data_revision` and
`energy_correction_scheme.atom_params_applied_as` (all `null` when not stated).

## 0.61.0 - 2026-09-30

Four things the two species-bearing bundles, `POST /api/v1/uploads/computed-species`
and `POST /api/v1/uploads/computed-reaction`, could not say (#622), all additive.

- **Transport.** `ComputedSpeciesUploadRequest.transport` and, per species,
  `BundleSpeciesIn.transport` take a `TransportInBundle`: exactly the standalone
  `POST /api/v1/uploads/transport` content (`sigma_angstrom` and
  `epsilon_over_k_k` together, `dipole_debye`, `polarizability_angstrom3`,
  `rotational_relaxation`, `scientific_origin`, `literature`, `software_release`,
  `workflow_tool_release`, `note`, at least one property) plus
  `source_calculations`, a list of `{calculation_key, role}` naming the bundle's
  own calculations (`role` is `full_transport`, `dipole`, `polarizability` or
  `supporting_geometry`). It lands on the same species entry as that species'
  thermo and statmech, and is append-only. A source calculation must belong to
  the same species. Provenance follows thermo: the block's own release wins,
  and where it names none the bundle's fills in (`workflow_tool_release` on
  both bundles, `analysis_software_release` as well on the reaction bundle).
  The responses name what was written: `ComputedSpeciesUploadResult.transport`
  (`{transport_id, transport_ref}`) and `transport_ids` with `transport_refs`
  (`trn_...`, same order) on the reaction bundle's response. A source
  calculation of another species on the reaction bundle is refused with the
  coded `transport_source_calculation_owner_mismatch` (422, `context` has
  `field`, `target`, `owner_kind`), now a code a depositor can receive.
  Provenance gaps are annotated as warnings under `transport.` /
  `species['<key>'].transport.`.
- **Rejected rotors.** `invalidated_reason` on `StatmechTorsionInBundle` and on
  the reaction bundle's `BundleStatmechTorsionIn`, the same field the conformer
  route's `StatmechTorsionIn` has. It is stored and read back under the
  statmech's torsions.
- **Who measured the SCF stability.** `SCFStabilityContent.source_calculation_key`
  names the calculation (job) in the same bundle that measured the verdict, when
  it is not the calculation the block is attached to. It may point at a
  calculation declared later in the payload. Rules: it must name a declared
  calculation; of the same species entry (or of the transition state, for a
  transition-state calculation), else the coded
  `scf_stability_source_calculation_owner_mismatch`; on the same conformer as
  the carrier (reaction bundle: the same `conformer_key`, or the conformer of
  its `geometry_key`, where both are stated), else the coded
  `scf_stability_source_geometry_mismatch` (422, `context` has `field`, `key`,
  `carrier_key`), the sibling of `thermo_sp_geometry_mismatch`; it may not name
  the carrier itself or close a cycle with other blocks' keys. A measuring job
  at a different level of theory than the carrier is accepted with the upload
  warning `scf_stability_source_level_mismatch`. The key is stored in the
  existing `calc_scf_stability.source_calculation_id` and read back as
  `source_calculation_ref`. No stability calculation type was added: the
  measuring job keeps the type it has. `SCFStabilityPayload` (the primitive
  routes, which name a calculation by id) and `SCFStabilityContent` now share
  `SCFStabilityBase`, so the key is not in the primitive routes' schemas and is
  refused there as an unknown field. It is also refused on
  `POST /api/v1/uploads/networks/pdep`, which has no pass to link it.
- **Contract prose.** `SoftwareReleaseRef` now says what `version`, `revision`
  and `build` hold and that all three are part of release identity (`revision`
  is the vendor label such as Gaussian `C.02`, or the commit hash for analysis
  software; `build` is a compile or packaging variant). There is still no
  `git_commit` on `SoftwareReleaseRef`; adding one needs a column and a change
  to release identity, and is deferred. `PathSearchResultPayload.converged` now
  says what it means for each method and that an output file existing is not a
  convergence verdict. `PathSearchPointPayload.is_climbing_image` stays a plain
  `bool` defaulting to `false`: `false` still reads as both "not a climbing image"
  and "not stated", because a tri-state value needs the stored column to accept
  NULL, which is a migration and is deferred.

## 0.60.0 - 2026-09-30

`electronic_levels` is now accepted on the bundle statmech blocks,
`StatmechInBundle` (`/uploads/computed-species`) and `BundleStatmechIn`
(`/uploads/computed-reaction`), with the same element shape
(`ElectronicLevelIn`: `level_index`, `energy_cm1`, `degeneracy`) and the same
rule as `/uploads/statmech` (`level_index` unique within a statmech). Additive
and optional: it defaults to no levels, and both bundle roots keep the same
field set (#609). The network PDep upload's species and transition-state statmech blocks share the backend persist path, so they now accept and store `electronic_levels` too. Before this, either block refused it with 422
`extra_forbidden`, so a single-atom bundle (ARC's O and Cl atoms) had no way to
carry its electronic partition function. The backend also gains upload
warnings for a one-atom species whose ground term is not S:
`missing_atomic_electronic_levels`, `missing_atomic_spin_orbit_correction`,
`atomic_electronic_degeneracy_contradicts_term`, and `term_symbol_contradicts_multiplicity`
for a term symbol whose leading 2S+1 disagrees with the declared multiplicity.
`missing_statmech_frequency_source` now decides "single atom" from the
geometry, `rigid_rotor_kind` or the species identity rather than from the
absence of rotational constants (#608).

## 0.59.0 - 2026-09-30

A single atom may be deposited with an `sp` primary (#610), on both
`POST /api/v1/uploads/computed-species` and
`POST /api/v1/uploads/computed-reaction`. Both routes required the conformer's
primary calculation to be an `opt`, and an atom has no geometry to optimise, so
ARC's adapter relabelled the atom's single point as an "optimisation" (same log
and energy, `converged=false`, and a level of theory and program that were not
the ones used). The rule is now: a conformer whose own XYZ has exactly one atom
may send `type: "sp"` as its primary; a conformer of two or more atoms still
needs `opt`, and a one-atom primary of any type other than `opt` or `sp` is
still refused. The refusal for an `sp` on two or more atoms now says how many
atoms the geometry has. A relabelled `opt` on an atom is still accepted, so no
existing producer breaks. The server stores the atom's `sp` with the conformer
geometry as both its input and its final output, so the conformer reads back
with a geometry; a further `sp` on the atom gets no inferred
`single_point_on` edge and no `dependency_edge_not_inferred` warning, since the
atom has no `opt` for the edge to name. With no `opt` linked, two `sp` links
on one geometry (thermo or statmech) are refused `thermo_role_duplicate` /
`statmech_role_duplicate`, as two on one optimisation always were; the
`/uploads/conformers` route gives a one-atom `sp` primary the same geometry
link. What an atom should send is in the
producer contract, under the two conformer primary-calculation rules. The
wire shape gains nothing: no field is added, removed or renamed.

## 0.58.0 - 2026-09-30

Producer contract only; no model in this package changes. `POST /api/v1/bundles/dry-run`
and `POST /api/v1/bundles/submit` now cap one bundle (#586): a request body
over 5 MiB is refused `bundle_too_large` (413, `context.max_bytes`, and
`context.given_bytes` when the request declared its length) before it is
parsed, and a bundle with more than 500 thermo plus kinetics records is
refused `bundle_too_many_records` (422, `context.max_records`,
`context.records`). Both caps are operator settings; the defaults are far
above any bundle measured. The dry run also has its own, tighter rate bucket
(10 per minute per credential by default), still answered `429
rate_limit_exceeded`.

## 0.57.0 - 2026-09-30

Every upload, job and bundle response now names its submission by public ref
as well as row id: `submission_ref` (`sub_...`) beside `submission_id`. In this
package that is `ComputedSpeciesUploadResult.submission_ref`, and
`CalculationUploadRefInBundle.calculation_ref` (`calc_...`) beside
`calculation_id`. Both are optional and additive. The two routes that took a
row id in the path, `POST /api/v1/submissions/{submission_id}/rights-attestations`
and `POST /api/v1/calculations/{calculation_id}/artifacts`, now accept either
the integer or the ref there (a `handle_type_mismatch` 422 for a ref of the
wrong kind, 404 `handle_not_found` for an unknown one, in either form); the integer is deprecated, not removed.

## 0.56.0 - 2026-09-29

`reversible` on the reaction of `POST /api/v1/uploads/transition-states`
(`TSReactionUpload`) is now optional and defaults to `true`, matching
`POST /api/v1/uploads/computed-reaction`. Omitted means `true` on both
routes: a transition state belongs to an elementary step, and an elementary
step is reversible by microscopic reversibility. Send `false` only to state
that the step is irreversible. A payload that already sends the field is
unaffected. Both routes now carry the same description of the field in the
contract. (#583)

## 0.55.0 - 2026-09-29

Producer contract only; no model in this package changes. New refusal code
on `POST /api/v1/submissions/{submission_ref}/supersede`:
`submission_supersede_not_owner` (403). The caller must have created both
submissions -- the one in the path and the one named by `new_submission_ref`
-- or hold the curator or admin role. An unknown ref is still 404.

## 0.54.0 - 2026-09-29

The producer contract now shows the enthalpy-reference rule
(`enthalpy_reference_error`) as reached from `POST /api/v1/bundles/dry-run`
as well as `POST /api/v1/bundles/submit` (#577). A dry run used to skip
every check inside the thermo and kinetics upload workflows, so a bundle
could pass it and then be refused on submit; it now rehearses submit and
reports the refusal submit would give, with the same `code` and message,
as an `error` entry in `messages` (and `bundle_valid: false`). No wire
model changed.

New refusal code on `POST /api/v1/bundles/dry-run` only:
`dry_run_contended` (503, `Retry-After: 1`, `context.reason` is
`lock_timeout` or `deadlock`). The rehearsal gives way to a concurrent
deposit writing the same records rather than delay or deadlock it; nothing
was decided about the bundle, so retry the dry run.

## 0.53.0 - 2026-09-29

Producer contract only; no model in this package changes. The submission
supersede surface is now `POST /api/v1/submissions/{submission_ref}/supersede`
with body `{"new_submission_ref": "sub_..."}` (issue #571): both submissions
are named by public ref, and a row id is refused with 422 in the path and in
the body. **Breaking for that route:** the 0.52.0 contract's
`new_submission_id` (an integer) is no longer accepted. A submission's ref is
`public_ref` on a submission read.

## 0.52.0 - 2026-09-29

Ship a **producer contract** inside the package:
`tckdb_schemas/contract/PRODUCER_CONTRACT.md` plus one JSON Schema per
upload payload under `tckdb_schemas/contract/schemas/`. It states, per
upload route, the payload fields (types, units, allowed values,
constraints), the rules the payload models and the upload workflows
enforce, the refusal codes the route can return, and a minimal valid
example. It is generated from the backend's routes, models and code
catalogue (`backend/scripts/generate_producer_contract.py`) and CI refuses a
stale copy. Read it with `python -m tckdb_schemas.contract --print`, see
what moved since the version you target with `--since <version>`, and load a
schema with `tckdb_schemas.contract.json_schema("ThermoUploadRequest")`.

Also new: `tckdb_schemas.producer_rule.producer_rule`, a marker for a
shared rule a workflow applies outside the payload model; the contract
prints every marked function a route reaches. `enthalpy_reference_error` is
marked, and its docstring now states the rule in a producer's terms.
`ThermoStateFields` gains field descriptions for `phase`,
`enthalpy_reference_kind` and `reference_pressure_bar`, and the four upload
request models this package defines declare a minimal valid example
(`json_schema_extra["examples"]`). No field, value or validation behaviour
changes: every 0.51.0 payload validates identically.

## 0.51.0 - 2026-09-27

`enthalpy_reference.enthalpy_reference_error` now counts a tabulated Gibbs
energy (`points[*].g_kj_mol`) as enthalpy content. A stored G is
H(T) - T*S(T) on the record's enthalpy zero, so it carries H's reference.
**Behaviour change for depositors:** a thermo deposit whose points carry `g`
must now declare `enthalpy_reference_kind`, like one that carries an
enthalpy; before, an undeclared G-only (or G-and-S) deposit was accepted,
and declaring `formation_298k` on it was refused as
`enthalpy_declaration_without_content`. Now the undeclared one is refused
with `enthalpy_declaration_absent` and the declared one is accepted. Codes
and messages are unchanged. Existing stored records are not revisited.

## 0.50.0 - 2026-09-27

Add `enthalpy_reference.shared_enthalpy_reference(kinds)`, the rule for
combining enthalpies from several thermo records: every term must declare
its `enthalpy_reference_kind`, and all must declare the same one. It returns
the shared kind, or declines with `enthalpy_reference_unrecorded` (any term
undeclared) or `enthalpy_reference_mixed` (terms disagree), exported as
`ENTHALPY_REFERENCE_UNRECORDED` / `ENTHALPY_REFERENCE_MIXED`. An empty
collection raises `ValueError` rather than reporting a vacuous shared basis.
The ML reaction export's `delta_h298` already applied this rule privately
and now calls it; its reason values are unchanged. Additive: no existing
name, value or behaviour changes.

## 0.49.0 - 2026-09-24

Stop defaulting `reference_pressure_bar` to 1 bar on computed thermo uploads
(issue #529). ARC computes entropy at 1 atm via a hardcoded translational
partition function and records no pressure anywhere in its output, so the
default was stamping every computed deposit with a standard state its own
numbers were not computed at. An omitted pressure now stays unrecorded, for
every scientific origin. `phase` keeps defaulting to `gas` for computed
uploads: unlike the pressure convention, gas is a direct consequence of the
ideal-gas statistical mechanics every current computed producer uses, not
an arbitrary convention a producer could plausibly have gotten wrong.
Existing stored records still carry the old 1 bar default; correcting them
is a separate, undecided question.

## 0.47.0 - 2026-09-23

Explicit enthalpy reference declarations for thermo deposits and grouped reference reads.
No inferred defaults or legacy backfill.

## 0.34.0 — a frequency list that numbers two modes the same says so by name

**Not breaking.** No field is added, renamed or removed, and the set of
payloads that validate is byte-for-byte the set that validated under
0.33.0 — **every 0.33.0 payload validates unchanged under 0.34.0**, and
every payload 0.33.0 refused is still refused. What changes is what a
client is *told* when one is refused.

`FreqResultPayload.validate_modes_consistency` has always refused a
`modes` list carrying the same `mode_index` twice. It refused with a bare
`ValueError`, so the 422 envelope reported `code =
"request_validation_error"` (or `"validation_error"`, depending on which
handler saw it) and a client wanting to distinguish "renumber your
frequency list" from any other validation failure had to match English.

It now raises `CodedValidationError` under the new module-level constant
`W_FREQ_MODE_INDEX_NOT_UNIQUE = "freq_mode_index_not_unique"`, with
`context = {"field", "duplicate_mode_indices", "mode_count"}`. The first
sentence of the message is byte-identical to the one 0.33.0 emitted
(`message_prefix=False`), so a client matching prose is undisturbed; the
duplicated indices and the repair follow it.

Minor rather than patch because the envelope's `code` field is part of
the published contract: a consumer may now branch on this refusal, which
it could not before.

**Deliberately not a scientific check.** Its neighbour
`freq_n_imag_disagrees_with_modes` (0.28.0) is declared in the backend's
scientific check register, because two fields of one record answering the
same question about chemistry differently is a position a referee could
argue with. A repeated `mode_index` is not: it is a malformed list — a
concatenated serialisation, a producer that restarted its counter — and
asserts nothing about the potential energy surface. It is catalogued in
`app.api.code_catalogue` and reaches the client enum from there, which is
the split that module exists for.

## 0.33.0 — **BREAKING**: `CalculationPayload.literature_id` is removed

`CalculationPayload.literature_id` is **removed** and replaced by
`literature`, the inline `LiteratureUploadRequest` fragment — the same
repair 0.32.0 made to `CalculationIn`, applied to the other shared
calculation payload. The generalised no-FK-ids gate that found it now
walks every upload root discovered from the live route table, rather than
the single root it used to check.

## 0.32.0 — **BREAKING**: a calculation cites literature inline, and the reaction bundle's 201 names its statmech

**Breaking:** `CalculationIn.literature_id` is **removed**. It was a
database primary key on an upload surface — supplying it required having
already queried this database, which a depositor has not — contradicting
"no FK IDs in upload schemas". It is replaced by `literature`, the same
inline `LiteratureUploadRequest` fragment
`CalculationInBundle.literature` has always taken on the species bundle,
resolved (and deduplicated) by the workflow.

`CalculationIn` is shared, so this lands on **two** routes:

| payload | 0.31.0 | 0.32.0 |
|---|---|---|
| `computed-reaction`, calculation with `literature_id: 7` | 201, FK stored | **422** — `extra="forbid"` names the field |
| `network-pdep`, calculation with `literature_id: 7` | 201, FK stored | **422** |
| either route, calculation with inline `literature: {...}` | field did not exist | 201, row resolved-or-created and attached |
| two calculations citing the same DOI | n/a | one `literature` row, shared |

**Migrating.** Replace `"literature_id": <n>` with the citation itself:
`"literature": {"kind": "article", "title": …, "doi": …}`. The workflow
resolves an existing row by DOI/ISBN or creates one, so repeating the
same fragment across calculations does not duplicate it. A calculation
that cited nothing needs no change. The failure is a 422 naming
`literature_id`, not a silent drop — deliberately, since silently
ignoring it would discard the citation.

`calculation_in_to_with_results_payload` gains a keyword-only
`literature_id` and **raises** if handed a calculation carrying a
`literature` fragment without one. The package cannot resolve literature
(it has no database), so the caller resolves and passes the id down;
defaulting to `None` would make "cited nothing" and "the workflow forgot"
the same value.

**Additive:** `ComputedReactionUploadResult` gains `statmech_ids` and
`atom_map_id`. Both were already computed by the workflow and discarded
by pydantic's default `extra="ignore"`, so a bundle depositing kinetics,
thermo *and* statmech got a 201 naming two of the three, and the atom map
it wrote had to be fetched back through the read API. The result model is
now `extra="forbid"` so the next such omission fails loudly.

**Documentation only, no shape change:** `BundleKineticsIn`'s docstring
now records the three scientific-content fields the standalone
`/uploads/kinetics` route has and it does not
(`interpretation_assignments`, `tunneling_application`,
`network_kinetics_ref`), with the git evidence that two of the three are
drift rather than design. No field was added or removed; closing those
gaps needs bundle-local-key schemas plus workflow persistence.

## 0.31.0 — **BREAKING**: `source_conformer_key` now points at something, on every route that accepts it

**A field that cannot be wrong is not a field.** Three upload paths
accepted a value, returned 201, and did nothing with it. All three are
now either persisted or refused, and the set of payloads that validate
narrows in three places — hence the major-ish bump on a pre-1.0 line.

| payload | 0.30.0 | 0.31.0 |
|---|---|---|
| species/reaction bundle, `source_conformer_key` naming a declared conformer | 201, link **not** stored | 201, link stored |
| species/reaction bundle, `source_conformer_key` naming nothing | 201, silently ignored | **422** `applied_energy_correction_source_key_undeclared` |
| `/uploads/conformers`, `source_conformer_key` equal to `label` | 201, resolved via the label | **422**, same code |
| `/uploads/conformers`, `source_conformer_key` equal to a new `conformer_key` | field did not exist | 201, link stored |
| reaction bundle, two conformers of one species sharing a `key` | 201 | **422** |
| species bundle root `workflow_tool_release` | accepted, dropped, *and* warned about as missing | persisted as the thermo/statmech default |

**New field: `ConformerUploadRequest.conformer_key`** (optional). It is
the conformer namespace's counterpart to `ConformerCalculationIn.key`.
`source_conformer_key` previously resolved against `label`, because a
label was the only string a conformer upload attached to its conformer.
That conflated two jobs: a label is a human tag that also feeds
conformer-group matching, so renaming it for grouping reasons silently
broke a correction reference, and a depositor with no label could not
name their own conformer at all.

**Migrating a conformer upload.** If you set `source_conformer_key` to
your own `label`, add `conformer_key` with that same string; `label` is
free to keep meaning what it means. If you set neither, nothing changes.

**Migrating a bundle.** A `source_conformer_key` that names a real
`conformers[*].key` needs no change — it now does what it always looked
like it did. One that names anything else was never recording a link and
now says so. On the reaction bundle the namespace is scoped to the
species the correction sits under: a sibling species's conformer is out
of scope, which is what makes ownership true by construction rather than
by a second check. A transition state in that bundle declares no
conformers at all, so a TS-side `source_conformer_key` can never resolve
and is refused rather than ignored.

**`ComputedSpeciesUploadRequest.workflow_tool_release` is now read.** It
is the bundle-level default the `thermo` and `statmech` blocks inherit
when they name no workflow tool of their own — the precedence
`ComputedReactionUploadRequest` has applied to `literature` /
`analysis_software_release` / `workflow_tool_release` since those fields
and their reader landed. The species root declared the same field
without applying it. Nothing that omitted the field changes.

**`ComputedSpeciesUploadRequest.note` is still not persisted**, and now
says so in its own description. Unlike `workflow_tool_release` it has no
row to go to — the bundle owns no record of its own — so giving it one
changes what a bundle-level note *is*; it is not a wiring omission.

## 0.30.0 — **BREAKING**: a frequency list longer than `3N` is refused, not flagged

**This narrows the set of payloads that validate.** A payload accepted by
0.29.0 with a `freq_list_exceeds_geometry_degrees_of_freedom` warning is
refused by 0.30.0 with a 422 naming that same code. Nothing is added,
renamed or removed, and every *other* 0.29.0 payload validates unchanged
— but a rule that moves from warn to block is a breaking wire change and
is labelled one, whatever the shape of the diff.

| | 0.29.0 | 0.30.0 |
|---|---|---|
| `n_modes > 3N` | 201, `UploadWarning` with `structural_flag` | **422**, `code = freq_list_exceeds_geometry_degrees_of_freedom` |
| `n_modes == 3N` | 201, silent | 201, silent (unchanged) |
| `n_modes < 3N − 6` | 201, `UploadWarning` | 201, `UploadWarning` (unchanged) |
| `modes` omitted | 201, silent | 201, silent (unchanged) |

**Who is affected.** Only a deposit whose frequency list is longer than
the total number of Cartesian degrees of freedom of the geometry it is
attached to. The in-repo corpora were re-measured against this bound
specifically — 57 ARC statmech records and the 4 conformers of the
hydrazine pressure-dependent network — and **nothing is within six modes
of the ceiling**: the closest record sits at `3N − 6`, and the 13 records
that do violate a bound violate the *floor*, which still warns. No
existing deposit changes verdict.

**Why this one may block when its sibling may not.** `3N` is the
dimension of the nuclear coordinate space, so it is the total number of
eigenvalues the mass-weighted Hessian of an `N`-atom geometry has — every
Cartesian degree of freedom, the six rigid-body modes
a record should carry included. A longer list is not a short spectrum;
it is not a spectrum of that geometry under any harmonic treatment, grid,
coordinate system or level of theory, so no protocol takes part and no
correct deposit is refused. Both arguments that keep the *floor*
advisory fail here: every mechanism that makes a short list honest — a
partial Hessian, a frozen-atom or ONIOM region, a lumped participant —
*removes* modes, and nothing filters modes in; and deleting an over-long
list to evade the refusal loses a spectrum the check has already found is
not this geometry's, which is the repair rather than the evasion.
`freq_list_incomplete_for_geometry` is unchanged and stays a warning.

The tier now follows a scientific-check register entry
(`CHECK_FREQ_LIST_WITHIN_GEOMETRY_DEGREES_OF_FREEDOM`) rather
than an implementation, which is what 0.29.0 said the promotion was
waiting for. `RejectionCode.FREQ_LIST_EXCEEDS_GEOMETRY_DEGREES_OF_FREEDOM`
is exported by `tckdb-client` 0.38.0.

**Migrating:** attach the calculation to the geometry it actually ran on,
or deposit the geometry it ran on. If the payload was relying on the
warning, the fix is the same fix it was advising.

## 0.29.0 — a frequency list is measured against the geometry it was computed on

**Not breaking.** No field is added, renamed or removed, and **every
0.28.0 payload validates unchanged under 0.29.0**: the set of payloads
that validate is byte-for-byte the set that validated before, and every
payload 0.28.0 refused is still refused. The new judgement is entirely at
the **warning** tier — it changes what an accepted upload is *told*,
never whether it is accepted.

A record must carry "the complete signed unrounded
frequency list, never filtered", and nothing checked it. A depositor
could send three modes, all imaginary, with `n_imag = 3` and pass every
rule the package had — `freq_n_imag_disagrees_with_modes` included,
because that rule compares the imaginary count against `n_imag` and a
list of nothing but imaginary modes satisfies it exactly. The real modes
were simply absent, and absence is silent.

New module `tckdb_schemas.frequency_completeness`, with two codes:

| code | when |
|---|---|
| `freq_list_incomplete_for_geometry` | the list is shorter than the smallest complete spectrum the geometry admits |
| `freq_list_exceeds_geometry_degrees_of_freedom` | the list is longer than `3N`, so it describes motion the geometry does not have |

Both are warnings carrying `structural_flag=True`, reported through the
existing `stationary_point_findings()` surface on every published upload
request model and reaching a client as an `UploadWarning` in the 201
body.

**The bounds are deliberately the weakest ones that are certainly true.**
Linearity is never determined: a collinear molecule has `3N - 5` modes,
one *more* than `3N - 6`, so comparing against `3N - 6` accepts every
linear molecule without choosing a collinearity tolerance. For `N ≤ 2`
linearity is a fact rather than a measurement, so those take exact counts
(`N = 1` → 0 modes, `N = 2` → 1). The ceiling is `3N` and not `3N - 6`
because a record should also carry "the six translation/rotation eigenvalues
… so contamination is directly assessable" — a record carrying all `3N`
is the most complete record possible and must not be refused as
an over-count.

**Why warn and not block.** The check cannot tell a filtered list from a
genuinely shorter one: a partial-Hessian job, a frozen-atom or ONIOM
Hessian, and a lumped participant all produce fewer than `3N - 6` modes
and are correct records of what was computed. And `modes = null` is
accepted and must stay accepted, so a block's cheapest workaround is
deleting the frequency list — turning a partial list into no list at all,
which is the failure of dropping evidence to avoid a refusal. The over-long case genuinely is definitional; it warns
here because promoting it to a refusal belongs in the scientific-check
register rather than in an implementation.

Minor rather than patch because two new machine-readable warning codes
enter the published vocabulary: a consumer may now branch on them, which
it could not before. Neither is a refusal code, so no client rejection
constant changes.

## 0.28.0 — a frequency result that contradicts itself says so by name

**Not breaking.** No field is added, renamed or removed, and the set of
payloads that validate is byte-for-byte the set that validated under
0.27.0 — **every 0.27.0 payload validates unchanged under 0.28.0**, and
every payload 0.27.0 refused is still refused. What changes is what a
client is *told* when one is refused.

`FreqResultPayload.validate_modes_consistency` has always refused a
deposit whose `n_imag` disagrees with the imaginary modes in its own
`modes` list — `n_imag = 3` beside a single imaginary row is a record
that answers "how many imaginary modes?" two different ways, and neither
of the two consumers reading it is told the other exists. It refused with
a bare `ValueError`, so the 422 envelope reported
`code = "validation_error"` and a client wanting to distinguish "your
frequency list is inconsistent" (fix the payload) from any other
validation failure had to match English.

It now raises `CodedValidationError` under the new module-level constant
`W_FREQ_N_IMAG_DISAGREES_WITH_MODES = "freq_n_imag_disagrees_with_modes"`,
with `context = {"n_imag", "imaginary_mode_count", "mode_count"}`. The
first sentence of the message is byte-identical to the one 0.27.0 emitted
(`message_prefix=False`), so a client matching prose is undisturbed;
guidance naming the escape hatch follows it.

Minor rather than patch because the envelope's `code` field is part of
the published contract: a consumer may now branch on this refusal, which
it could not before.

**The asymmetry is unchanged and is the point.** `modes = null` with any
`n_imag` is still accepted — absence is incompleteness, not
contradiction, which is the same position the read API takes when it
reports `n_imag_at_or_above_tau = null` rather than `0` for a record with
no frequency list. Only a list that *disagrees* is refused.

## 0.27.0 — a statmech source link may be unique on something other than its key

Additive, and a **relaxation**: nothing is renamed, removed or newly
refused, so **every 0.26.0 payload validates unchanged under 0.27.0**.
Minor rather than patch because the accepted set genuinely grows.

`ConformerUploadStatmechPayload.validate_unique_source_calculation_pairs`
now keys uniqueness on `(calculation_key, existing_calculation_id, role)`
rather than `(calculation_key, role)`, reading the second element
reflectively.

For a payload built from this package there is no behaviour change at
all: `StatmechSourceCalcIn` has no `existing_calculation_id`, so the
second element is always `None` and the rule is the pair it always was.
The widening exists because this class is also the payload the backend
hands its statmech resolution service, and the standalone statmech upload
(`POST /api/v1/uploads/statmech`, a backend-side contract) passes a
subclass whose entries may name a calculation by row id — citing one a
*previous* request deposited — instead of by key. Under the old tuple
every such entry collapsed to `(None, role)`, so two genuinely different
calculations sharing a role, which is one rotor scan per torsion with all
of them `role='scan'`, were refused as a duplicate.

`existing_calculation_id` itself is deliberately **not** added to
`StatmechSourceCalcIn`. It is shared with the conformer and bundle paths,
which are self-contained by construction — one calc-key namespace covers
everything they deposit — so they have nothing to chain to, and
`extra="forbid"` keeps them refusing the field outright rather than
silently dropping the link.

## 0.26.0 — the reaction bundle's statmech gains the six fields the species bundle already had

Purely additive. Nothing is renamed, removed, narrowed or newly refused,
so **every 0.25.0 payload validates unchanged under 0.26.0** — this is a
minor bump, not a breaking one. What changes is what a payload is
*allowed to say*.

`BundleStatmechIn` — the per-species statmech on
`/uploads/computed-reaction` — carried 12 fields against the 18 on
`StatmechInBundle`, the same block on `/uploads/computed-species`. Both
describe the same `statmech` row, resolved by the same services, subject
to the same table constraints. Six fields were reachable from one route
and not the other:

| field | what it records |
|---|---|
| `literature` | the paper this statmech came out of |
| `software_release` | the analysis code, per species |
| `workflow_tool_release` | the workflow tool, per species |
| `rotational_constant_a_cm1` | first principal rotational constant |
| `rotational_constant_b_cm1` | second principal rotational constant |
| `rotational_constant_c_cm1` | third principal rotational constant |

All six already had columns on the `statmech` table, so this is a
contract and projection change with no migration.

`literature`, `software_release` and `workflow_tool_release` are
**per-species overrides** of the bundle-level `literature` /
`analysis_software_release` / `workflow_tool_release`, exactly as
`BundleThermoIn`'s equivalents became in 0.25.0. Absent, the bundle-level
value is used, so a 0.25.0 payload persists precisely what it did before.
The override is what makes the ordinary mixed deposit expressible: one
participant taken from a paper, the rest computed here.

Nothing is deliberately withheld. `applied_energy_corrections` — the one
field `BundleThermoIn` refuses, because `BundleSpeciesIn` already
declares it against the same species entry — is not on `StatmechInBundle`
either, so it is not part of this divergence and needs no exception.

The rotational constants are the interesting half of the history.
`BundleStatmechIn` was born narrow, like `BundleThermoIn`. But the three
constants were added to `StatmechInBundle` **only**, two days after an
earlier commit had correctly updated both models together. The habit was
right and then it lapsed, and nothing could tell a one-sided addition
from a deliberate asymmetry. `backend/tests/schemas/test_bundle_root_model_symmetry.py`
now asserts the two field sets match, with an allowlist that requires a
written reason per exemption.

Consumers do not need to change. If you are building reaction bundles by
hand and want per-species statmech provenance or rotational constants,
these are the field names; if you are not, nothing moves.

## 0.25.0 — bundle roots gain SCF stability and thermo provenance; an atomless participant may not carry coordinates

Three additions and one new refusal. Nothing is renamed or removed, so a
0.24.0 payload keeps validating — **unless** it declared a free electron
and hung a geometry off it, which is the refusal below.

**`scf_stability` now reaches the bundle roots.** The field existed only
on `CalculationWithResultsPayload`, so `/uploads/conformers`,
`/transition-states`, `/statmech`, `/thermo` and `/transport` could record
whether an SCF solution had been tested for stability, and
`/uploads/computed-species`, `/uploads/computed-reaction` and
`/networks/pdep` could not. Models are `extra="forbid"`, so a bundle that
tried got a 422 rather than a silent drop.

`SCFStabilityPayload` is now split so the bundles can carry it without
carrying database ids with it. The new `SCFStabilityContent` holds the
finding — `status`, `lowest_eigenvalue`, `instability_count`,
`instability_type`, `reoptimized_wavefunction`, `note` — and
`SCFStabilityPayload` extends it with the two FK fields
(`source_calculation_id`, `source_artifact_id`) the primitive routes
already accept as programmatic chaining. `CalculationInBundle` and the
shared `CalculationIn` take the content class; the primitive routes are
unchanged, so a 0.24.0 payload to any of them still validates.

The FK fields are not merely omitted for tidiness. A bundle names
everything by local key, `source_calculation_id` is already in the
bundle's own `_FORBIDDEN_DB_ID_FIELDS`, and a sideways local key could
not be resolved anyway: the block is persisted with the calculation it
hangs off, before that calculation's siblings exist. A depositor who
needs to cite another row has the primitive routes.

```python
{"key": "h_sp", "type": "sp", ...,
 "scf_stability": {"status": "stabilized", "instability_count": 1,
                   "reoptimized_wavefunction": True}}
```

**`BundleThermoIn` gains the provenance `ThermoInBundle` already had.**
The reaction route's per-species thermo could not record which
calculations produced it. It now takes `source_calculations`,
`literature`, `software_release`, `workflow_tool_release`,
`h298_uncertainty_kj_mol` and `s298_uncertainty_j_mol_k`, and the
workflow persists them — including the `thermo_source_calculation` rows
that route wrote none of. `software_release` / `workflow_tool_release`
override the bundle-level values for that species and fall back to them
when omitted, so existing payloads read exactly as before.

`applied_energy_corrections` is deliberately **not** added: the reaction
bundle already declares those on `BundleSpeciesIn`, against the same
resolved species entry, and a second place to say it would need a rule
for which one counts.

```python
{"key": "h", "species_entry": {...},
 "thermo": {"h298_kj_mol": 218.0,
            "source_calculations": [{"calculation_key": "h_sp", "role": "sp"}]}}
```

**An atomless participant may no longer carry coordinates.** A species
declaring `molecule_kind: electron` is now refused, as a 422 naming the
field, if it carries a conformer, or a `geometry_key`, `input_geometries`
or `output_geometries` on any of its calculations. A conformer geometry
was already refused — later, and as
`species_geometry_composition_mismatch` from mid-transaction — but a
*calculation* geometry reached no composition check on any path and was
accepted, so an electron's coordinates could be stored. Definitional:
a record of an electron's coordinates cannot be what it
says it is, so this rejects no correct calculation.

`molecule_kind: pseudo` is unaffected and stays that way. A lumped
construct's composition is *unknown*, not empty, and a geometry deposited
under one is an under-described molecule rather than a contradiction.

## 0.24.0 — **BREAKING**: statmech source calculations are named, not numbered

`ConformerUploadRequest.statmech` used to identify a supporting
calculation by its database primary key. It no longer does, and payloads
written against 0.23.0 that used those two fields will be rejected with a
422 rather than silently reinterpreted.

| Before (0.23.0) | After (0.24.0) |
|---|---|
| `statmech.source_calculations[].calculation_id: int` | `statmech.source_calculations[].calculation_key: str` |
| `statmech.torsions[].source_scan_calculation_id: int \| None` | `statmech.torsions[].source_scan_calculation_key: str \| None` |

A key names a calculation *declared in the same request*. To make that
possible, `ConformerUploadRequest.calculation` and
`additional_calculations[]` gained an optional `key`, and the request
refuses a statmech reference to a key nobody declared. This is the shape
the computed-species / computed-reaction bundles have always used; the
conformer path was the odd one out, and a row id is not something a
depositor can know.

Migrating: put a `key` on the calculation you meant, and reference it.

```python
# 0.23.0 — only writable by something that had already queried TCKDB
{"calculation": {...}, "statmech": {"source_calculations": [{"calculation_id": 4711, "role": "freq"}]}}

# 0.24.0
{"calculation": {"key": "h_sp", ...},
 "additional_calculations": [{"key": "h_freq", "type": "freq", ...}],
 "statmech": {"source_calculations": [{"calculation_key": "h_freq", "role": "freq"}]}}
```

Component renames (they affect generated client class names; the JSON
shape of the bundle paths is unchanged):

- `StatmechSourceCalculationCreate`, `StatmechSourceCalcInBundle` and the
  server-side `StatmechSourceCalculationIn` collapse into one component,
  `StatmechSourceCalcIn`. `StatmechSourceCalcInBundle` stays importable as
  an alias — from `tckdb_schemas.statmech_bits` and from both bundle
  modules — and is the same class object, so there is one component, not
  two. `StatmechSourceCalculationCreate` is **removed** from this package.
- `StatmechTorsionCreate` collapses into `StatmechTorsionIn` and is
  **removed** from this package.
- `StatmechTorsionCoordinateCreate` and `StatmechTorsionCoordinateBase`
  are removed; `StatmechTorsionCoordinateIn` is the only spelling of an
  atom quartet. They were field-for-field and validator-for-validator
  identical, so a generator emitted two classes for one concept.
- New: `ConformerCalculationIn` — a `CalculationWithResultsPayload` plus
  the optional `key`.

The two removed `*Create` names still exist inside the TCKDB server, in
`app.schemas.entities.statmech`, where they mean something different: the
row-shaped create payload that speaks `calculation_id`. They are not part
of any wire contract and never were importable from here for that purpose.

Also tightened, matching what the sibling upload paths already enforce.
Each of these used to surface as a 500 rather than a 422:

- `statmech.source_calculations` must be unique by
  `(calculation_key, role)` — it is the row's primary key.
- `statmech.torsions[].torsion_index` must be unique.
- The primary calculation may not be linked twice: once implicitly via
  `statmech.uploaded_calculation_role` and again in
  `source_calculations` under the same role. A *different* role is a
  different row and stays allowed.

## 0.23.0

Published `ConformerUploadRequest` and `TransitionStateUploadRequest`.
