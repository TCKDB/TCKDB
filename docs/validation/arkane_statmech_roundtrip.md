# Arkane statmech round-trip — TCKDB statmech-completeness validation

> **Refactored 2026-09-19 (Phase B, work package B4, C2b).** The harness now
> reads a SQLAlchemy session over the configured database instead of the
> public API over `curl` and the Pi over SSH, so it runs unchanged against a
> restored deposit database; it has a batch mode with a JSON summary; and it
> records the RMG version it actually used. The sections below this notice
> are the original single-species exhibits (methane, ethylperoxy) and their
> numbers, kept as the historical record. Nothing in them was re-measured
> here, because the corpus they were measured on is not on the machine this
> refactor was done on.

## Running the replay (current)

```
# one statmech record, or every statmech record of a species entry
conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py --statmech-ref sm_...
conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py --species-entry-ref spe_...

# every statmech record in the database, with a JSON summary
conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py --all-statmech --json-out replay.json

# assemble the decks without running Arkane (no rmg_env needed)
conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py --all-statmech --skip-arkane
```

* **Database access.** The connection comes from the `DB_USER`, `DB_PASSWORD`,
  `DB_NAME`, `DB_HOST`, `DB_PORT` environment variables that
  `Settings.database_url` (`backend/app/api/config.py`) reads, through
  `app.api.deps.SessionLocal`. Read-only: nothing is written. Because the
  database is read directly, review status is irrelevant; the old
  `min_review_status=under_review` workaround is gone with the HTTP path.
* **Arkane stays out of process.** Before anything is replayed the script asks
  the `rmg_env` conda environment (`RMG_ENV` overrides) for
  `rmgpy.__version__` and the `Arkane.py` next to the installed `rmgpy`
  (`ARKANE_ENTRY` overrides), plus the checkout's described tag, and writes
  all three into the JSON `rmg` block and every printed record. If the
  environment is missing, decks are still assembled and every record is
  reported as `arkane_skipped` with the reason; no number is fabricated.
* **Per-record status** in the JSON: `compared` (S298 and Cp(T) deviations
  against the stored thermo, plus `within_tolerance` under the declared
  tolerance of 0.5 J/mol/K on S298 and 1 % on Cp), `skipped` with a
  `skip_reason` (`no_frequencies`, `no_frequency_calculation`, `no_geometry`,
  `no_external_symmetry`, `element_mass_unavailable:<symbols>`,
  `torsion_<i>_has_no_scan`, `torsion_<i>_top_underivable:...`,
  `isotope_labelled_geometry` -- the moment-of-inertia masses are looked
  up by element symbol, so a labelled isotope is refused rather than
  silently replayed at natural abundance, ...), `arkane_skipped`, or
  `arkane_failed` with the reason: Arkane's tail, `output_unparseable`
  when `output.py` yielded neither S298 nor a Cp table, or
  `nothing_comparable` when it did but nothing stored could be checked
  against it. A record passes only when at least one check was made and
  every check passed; an undecided comparison is counted as exceeding.
* **Exit status.** `0` when every compared record is within tolerance; `1`
  when any exceeds it or Arkane failed on a record; `2` when nothing was
  compared (empty scope, everything skipped, or Arkane unavailable). The JSON
  carries every record either way.
* **Tests.** `backend/tests/scripts/test_arkane_statmech_roundtrip_deck.py`
  builds a statmech record from the Gaussian frequency fixture (12 atoms, 30
  printed frequencies, a methyl rotor with a 45-point threefold scan) and
  checks the deck the script renders: the harmonic-oscillator list (29 of 30
  frequencies, the lowest dropped for the rotor, all scaled by the stored
  factor), the external symmetry number (parametrised over 1 and 2; a deck
  that hard-codes `symmetry=1` fails the second case, which was checked by
  mutation), `opticalIsomers = 2` derived from `point_group = C1`, the
  `HinderedRotor` line with `symmetry=3` and the derived pivots/top, and that
  a record with no frequencies yields the skip reason `no_frequencies` and no
  deck. Arkane is not run by the tests.

## The two approximations (unchanged, stated)

1. **Torsional modes are removed by dropping the R lowest stored
   frequencies**, R being the number of hindered rotors, rather than by
   projecting the rotor out of the Hessian as Arkane does from ESS output.
   TCKDB stores the full unprojected 3N−6 spectrum; for the corpus species the
   torsions are the R lowest. A species whose lowest mode is not a torsion (a
   ring pucker below a methyl torsion) would be handled wrongly, which is why
   the dropped frequencies are listed in the deck and in the JSON.
2. **No atom-energy or bond-additivity corrections are applied**, so Arkane's
   H298 is an absolute `E_elec + ZPE + thermal` quantity and is not comparable
   with the stored enthalpy of formation. S298 and Cp(T), which do not depend
   on the energy reference, are the targets; H298 is reported with the caveat
   and never decides pass/fail.

Fixing either is not Phase B work.

## The two data-access findings, re-measured 2026-09-19

The original exhibits below recorded two public-API gaps: per-mode
frequencies and `statmech.optical_isomers` were not served. Both have since
been closed on the read surface:
`GET /scientific/calculations/{ref}?include=freq_modes`
(`backend/app/services/scientific_read/calculations.py`) returns the per-mode
array, and `optical_isomers` is on the statmech read schema
(`backend/app/schemas/reads/scientific_statmech.py`). The replay no longer
depends on either, since it reads the database. What remains true, and is
deliberately not fixed here:

* `statmech.optical_isomers` is **NULL for the ARC corpus**, so the replay
  derives it from the stored `point_group` (chiral groups C1/Cn/Dn/T/O/I give
  2, anything with an improper element gives 1) and records which it used.
  For a chiral species the naive default of 1 costs exactly R ln 2 =
  5.76 J/mol/K in S298, the ethylperoxy finding below.
* `statmech_torsion.top_description` is **NULL**, so the rotating top is
  derived from the stored geometry's connectivity (cut the pivot bond, take
  the side reachable from the first pivot atom). Deterministic for an acyclic
  single-bond rotor; a ring rotor is refused with
  `torsion_<i>_top_underivable`.

Moments of inertia use standard atomic weights, rmgpy's convention and the
one the stored thermo was computed with; this is deliberately not the
isotopic convention the Hessian reanalysis applies.

## What was run for the refactor

No restored deposit database exists on the workstation the refactor was done
on (the only local databases were empty), so the end-to-end run used a
scratch database migrated to head and seeded with the test fixture record
above. Arkane **did run**, in `rmg_env` with rmgpy 4.0.0 at RMG-Py
`4.0.0-6-g62eb728c0`: rmgpy computed the rotor's reduced moment of inertia
(2.556 amu·Å², 45 scan points, 5.93 kJ/mol barrier) and Fourier fit, Arkane
produced `output.py`, and the script parsed S298 and Cp(300/500/1000/1500 K)
from it. The stored thermo in that fixture is a placeholder (300 J/mol/K, a
flat NASA polynomial), so the deviations it reported (`exceeding`, exit 1)
say nothing about TCKDB's completeness; they show the pipeline is live. The
`--skip-arkane` path on the same database exited 2 with two `arkane_skipped`
records and one `skipped` (`no_frequencies`). A 12-point scan was tried first
and rmgpy's Fourier fit refused it ("negative barrier on final try with 12
terms"), which is why the fixture carries the corpus's 45-point resolution.

---

# Original exhibits (2026-08, live Pi, API + SSH harness)


**Verdict: PROVEN, for both a rigid and a floppy species.** TCKDB stores enough
statmech data to regenerate a species' entropy and heat capacity with Arkane,
without the original ESS output files.

* **Rigid** (methane, no rotors): S298 and Cp(T) reproduce to **< 0.01 %**.
* **Floppy** (ethylperoxy `CCO[O]`, 2 hindered rotors): S298 to **0.02 %** and
  Cp(T) to **< 0.06 %** — once the optical-isomer count is recovered from the
  stored `point_group` (see the floppy-species section below; the dedicated
  `optical_isomers` column is NULL, which alone costs a real 1.84 % S298 error).

This is a paper validation exhibit paralleling the Cantera/CHEMKIN round-trip.
Harness: [`backend/scripts/validation/arkane_statmech_roundtrip.py`](../../backend/scripts/validation/arkane_statmech_roundtrip.py).

```
conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py \
    --species-entry-ref spe_oxmflzmwl4xzkeujaj3oj3efl4
```

## The claim under test

TCKDB claims "statmech-completeness": it stores enough (geometry, energies,
harmonic frequencies, rotors, external symmetry, spin multiplicity, optical
isomers) to **regenerate** a species' thermo without the original quantum
chemistry (ESS) files. The stored corpus came from real ARC runs whose thermo
was originally computed by Arkane, so this is a **self-consistency** check: read
a species back out of TCKDB, rebuild the Arkane input from stored data alone,
re-run Arkane, and compare against the thermo TCKDB already stores.

## Species chosen

**Methane (CH4)** — `species_entry_ref = spe_oxmflzmwl4xzkeujaj3oj3efl4`.
Chosen because it is the cleanest possible round-trip: a **rigid** molecule with
**no hindered rotors** (so no rotor-scan reconstruction), high symmetry
(point group Td, external symmetry number 12), singlet (multiplicity 1). This
isolates exactly the structural/vibrational data whose completeness is being
tested. Floppy species with rotor scans exist in the corpus (e.g. ethylperoxy
`CCO[O]`, `spe_sgidibgknrjbvcetgc6xsej74q`) and are the natural follow-up.

Level of theory: geometry/frequencies at `wb97xd/def2tzvp` (Gaussian), single
point at `wb97xd/def2tzvp`, frequency scale factor 0.988, treated by Arkane 1.1.0
under ARC 1.1.0.

## Where each datum came from (API vs direct DB)

| Datum | Source | Endpoint / table |
|---|---|---|
| External symmetry number (12) | **API** | `/scientific/species-entries/{ref}/statmech` → `statmech.external_symmetry` |
| Point group (Td), linearity | **API** | same statmech record |
| Frequency scale factor (0.988) | **API** | same statmech record |
| Spin multiplicity (1) | **API** | statmech record `species.multiplicity` |
| Geometry (5 atoms, Cartesian Å) | **API** | `/scientific/geometries/{geom_ref}` |
| SP electronic energy (−40.5192788932 Eh) | **API** | `/scientific/calculations/{sp_ref}?include=results` → `results.sp` |
| ZPE (0.0448596 Eh) | **API** | `/scientific/calculations/{freq_ref}?include=results` → `results.freq` |
| **Per-mode harmonic frequencies (9 modes)** | **DB only** | `calc_freq_mode.frequency_cm1` — **not exposed by the API** |
| **`optical_isomers`** | **DB only** | `statmech.optical_isomers` — **column exists, omitted from API payload** |
| Stored thermo (S298, H298, NASA) | **API** | `/scientific/species-entries/{ref}/thermo?include=all` |

Moments of inertia are **not stored**; they are recomputed in the harness from
the stored geometry + atomic masses. That is intentional and is exactly the
completeness claim (geometry → inertia).

All reads pass `min_review_status=under_review` (the corpus is all
`under_review`; the API defaults to `approved` and would return zero rows).

## Method

An Arkane `thermo('NASA')` input is assembled purely from the stored data using
explicit statmech `modes`:

* `IdealGasTranslation(mass=…)` — molecular weight from the geometry's atoms;
* `NonlinearRotor(inertia=[Ia,Ib,Ic], symmetry=12)` — principal moments from
  the stored geometry + atomic masses;
* `HarmonicOscillator(frequencies=…)` — the 9 stored harmonic frequencies,
  scaled by the stored 0.988 factor;
* `spinMultiplicity=1`, `opticalIsomers=1`;
* `E0 = E_elec + ZPE` (no atom-energy / bond corrections applied).

Arkane is run in `rmg_env` (`python /home/calvin/code/RMG-Py/Arkane.py input.py`).

## Results

Tolerance considered a "match": **S298 within ~0.5 J/mol/K**, **Cp within ~1 %**.
Both are met by more than two orders of magnitude.

### S298 (J/mol/K) — independent of the energy reference

| | Value |
|---|---|
| TCKDB stored | 186.055 |
| Arkane recomputed | 186.046 |
| abs Δ | **0.010 J/mol/K** |
| % Δ | **0.005 %** |

### Cp(T) (J/mol/K) — independent of the energy reference

| T (K) | TCKDB (NASA) | Arkane | abs Δ | % Δ |
|---:|---:|---:|---:|---:|
| 300 | 36.070 | 36.070 | 0.000 | 0.000 % |
| 500 | 45.749 | 45.748 | 0.001 | 0.002 % |
| 1000 | 70.838 | 70.839 | 0.001 | 0.002 % |
| 1500 | 85.749 | 85.747 | 0.002 | 0.002 % |

The stored and Arkane-recomputed NASA polynomials agree in a0–a4 to ~4–5
significant figures; only the a5 integration constant differs (it encodes the
enthalpy reference, see H298 below).

### H298 (kJ/mol) — secondary / stretch target

| | Value |
|---|---|
| TCKDB stored H298f (with corrections) | −78.829 |
| Arkane recomputed (no corrections) | −106 255.563 |

This large difference is **expected and is not a statmech-completeness failure.**
The stored value is an **enthalpy of formation** produced with the atom-energy /
bond-additivity correction scheme ARC used originally. The round-trip here
deliberately applies **no** corrections, so Arkane returns an **absolute**
enthalpy (E_elec + ZPE + thermal). Reproducing H298f additionally requires the
correction reference (TCKDB stores these under energy-correction schemes /
applied corrections), which is out of scope for a statmech-completeness test.
Because S298 and Cp match, the structural/vibrational data is complete; the
H298 gap is purely a correction-reference difference.

## Concrete completeness gaps found

The **data** needed for the round-trip is all present, but two load-bearing
statmech fields are **not reachable through the public read API** and had to be
read directly from the database:

1. **Per-mode harmonic frequencies** — stored in `calc_freq_mode.frequency_cm1`
   but not surfaced by any `/scientific/*` endpoint. The statmech record's
   `frequencies` section only returns `source_freq_calculation_refs` plus a note;
   following that to `/scientific/calculations/{ref}?include=results` yields
   `calc_freq_result` (ZPE, n_imag, imaginary frequency) but **not** the
   per-mode array. So the single most important statmech input — the vibrational
   spectrum — is API-invisible today.
2. **`optical_isomers`** — the `statmech.optical_isomers` column exists but is
   omitted from the statmech read payload. For methane it is additionally
   **NULL** in the DB (Arkane's default of 1 is correct here), but a chiral
   species would silently lose this and no API consumer could supply it.

These are **API-surface gaps, not schema gaps**: the values (frequencies) or the
column (`optical_isomers`) exist in the model. A downstream Arkane/thermo
regenerator built only on the public API could not currently reconstruct the
vibrational modes. Recommended follow-up (not done here — the gap is the
deliverable): expose per-mode frequencies on the freq-calculation results
payload and add `optical_isomers` to the statmech record.

## Conclusion

For methane, **TCKDB is statmech-complete**: its stored geometry, harmonic
frequencies, external symmetry number, multiplicity, and optical-isomer count
regenerate S298 and Cp(T) to within 0.01 % of the stored values — far inside the
match tolerance. H298 of formation is not reproduced only because the atom-energy
correction reference was intentionally not applied. The one actionable finding is
that two required statmech inputs (per-mode frequencies, `optical_isomers`) are
currently DB-only and should be exposed on the read API.

---

# Floppy species with hindered rotors — ethylperoxy `CCO[O]`

**Verdict: PROVEN.** TCKDB stores (or serves the primitives to derive) everything
Arkane needs to regenerate the thermo of a floppy species with internal rotation.
Reconstructing both hindered rotors from stored data reproduces **S298 to 0.02 %**
and **Cp(T) to < 0.06 %**. This is the harder case the methane exhibit flagged as
the natural follow-up: the internal-rotation treatment is where a completeness
gap was most likely, and it held.

```
conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py \
    --species-entry-ref spe_sgidibgknrjbvcetgc6xsej74q
```

## Species chosen

**Ethylperoxy radical (`CCO[O]`, CH₃CH₂OO·)** —
`species_entry_ref = spe_sgidibgknrjbvcetgc6xsej74q`, `statmech_ref =
sm_5hzn2hvewlshlm5g6bc6webxbq`. Chosen because it is a genuinely floppy,
low-symmetry (point group **C1**), doublet (multiplicity 2) radical with **two
1-D hindered rotors** — the CH₃ methyl torsion (symmetry 3) and the C–O·O
skeletal torsion (symmetry 1) — and it is present in the ARC corpus with complete
rotor scans (`has_rotor_scans = true`, `torsion_count = 2`). Level of theory:
geometry/frequencies at `b3lyp/def2tzvp` (Gaussian 16), single point at the same
level, frequency scale factor 0.999, treated by Arkane under ARC 1.1.0.

## Where each rotor datum came from (API vs DB vs derived)

Rotor reconstruction needs, per rotor: the scan **potential**, the **pivot**
atoms, the rotating-**top** atom set, and the rotor **symmetry** number.

| Datum | Source | Endpoint / table |
|---|---|---|
| Rotor **symmetry** number (3, 1) | **API** | `/scientific/species-entries/{ref}/statmech?include=torsions` → `torsions[].symmetry_number` |
| Rotor **pivots** | **API** | same torsions payload — the dihedral `coordinates[0].atom2_index`/`atom3_index` |
| Rotor **potential** (energy vs dihedral, 46 points) | **API** | `/scientific/calculations/{scan_ref}/scan` → `points[].relative_energy_kj_mol` + `coordinate_values` |
| Rotor **top** atom set | **DERIVED** | `torsions[].top_description` is **NULL**; reconstructed from stored geometry connectivity |
| Per-mode harmonic frequencies (21) | **DB only** | `calc_freq_mode.frequency_cm1` — still not on the API |
| **`optical_isomers`** (= 2) | **DERIVED** | `statmech.optical_isomers` is **NULL**; recovered from the API-served `point_group = C1` |

The full rotor scan trajectory **is** on the public API (the specialized
`GET /scientific/calculations/{ref}/scan` endpoint returns every point's
dihedral and relative energy) — a notable improvement over the methane findings,
which predicted this would be DB-only. So the single most load-bearing rotor
datum, the potential, is API-reachable.

## Method (rotor path added to the harness)

For each torsion the harness:

1. reads symmetry + pivots from the API torsions payload and the full scan
   potential from the API `/scan` endpoint;
2. **derives the rotating top** — TCKDB does not store it (`top_description`
   NULL), but for an acyclic single-bond rotor the top is a deterministic graph
   property: reconstruct the bond graph from the stored geometry (covalent-radii
   cutoff), cut the pivot bond, and take the connected side. This is
   reconstruction from stored primitives, not a guess (the same status as
   recomputing moments of inertia from geometry);
3. computes the **reduced moment of inertia** (rmgpy `option=3`, the ARC/Arkane
   default) and **fits the Fourier potential** using rmgpy itself (run in
   `rmg_env`), so both match Arkane exactly, then embeds them as literals in the
   Arkane input;
4. **drops the R torsional modes from the harmonic list** to avoid
   double-counting. TCKDB stores the full unprojected 3N−6 = 21 frequencies; the
   two torsions are the two lowest (108.3, 231.4 cm⁻¹), which are removed, giving
   19 harmonic oscillators + 2 `HinderedRotor` modes.

Assembled rotor terms: rotor 1 (CH₃) pivots (1,2), top {1,5,6,7}, σ=3,
I_red = 2.60 amu·Å², barrier 12.55 kJ/mol; rotor 2 (C–O) pivots (2,3), top
{1,2,5,6,7,8,9}, σ=1, I_red = 6.54 amu·Å², barrier 8.70 kJ/mol.

## Results

Tolerance considered a "match": **S298 within ~0.5 J/mol/K**, **Cp within ~1 %**.
Both are met with large margin.

### S298 (J/mol/K) — independent of the energy reference

| Optical-isomer count used | Arkane S298 | abs Δ vs stored (315.945) | % Δ |
|---|---:|---:|---:|
| `opticalIsomers = 1` (naïve default) | 310.118 | 5.827 | **1.844 %** |
| `opticalIsomers = 2` (derived from `point_group = C1`) | 315.879 | **0.066** | **0.021 %** |

The 1.84 % error with the naïve default is **exactly R·ln 2 = 5.76 J/mol/K** — an
entropy-only offset (Cp is unaffected either way), the fingerprint of a missing
chirality factor. Ethylperoxy's gauche minimum (C–C–O–O dihedral ≈ 62°) is
chiral, so the original Arkane run correctly used `opticalIsomers = 2`. Deriving
the count from the stored `point_group` recovers it and closes the gap.

### Cp(T) (J/mol/K) — independent of the energy reference

| T (K) | TCKDB (NASA) | Arkane | abs Δ | % Δ |
|---:|---:|---:|---:|---:|
| 300 | 73.912 | 73.869 | 0.043 | 0.059 % |
| 500 | 101.433 | 101.416 | 0.017 | 0.017 % |
| 1000 | 147.789 | 147.775 | 0.014 | 0.009 % |
| 1500 | 169.082 | 169.084 | 0.002 | 0.001 % |

Cp is reproduced to well under 0.1 % at every temperature — including 300 K,
where the two low-frequency torsional/rotor modes dominate. That the rotor
treatment lands the low-T Cp this precisely is the strongest evidence the stored
rotor data (potential + topology + symmetry) is complete and correctly assembled.

### H298 (kJ/mol) — secondary / stretch target

| | Value |
|---|---|
| TCKDB stored H298f (with corrections) | −19.532 |
| Arkane recomputed (no corrections) | −602 704.572 |

Same caveat as methane: with no atom-energy/bond corrections applied, Arkane
returns an **absolute** enthalpy, not a formation enthalpy. The difference is a
correction-reference difference, not a statmech-completeness failure.

## Concrete completeness gaps found (floppy species)

The rotor round-trip succeeds, but it surfaces three storage/serving gaps —
progressively less severe:

1. **`optical_isomers` is NULL and off the API — and it is load-bearing for
   floppy/chiral species.** Unlike methane (where the Arkane default of 1 is
   coincidentally correct), ethylperoxy is chiral, so the missing value causes a
   **real 1.84 % S298 error** if a consumer takes the naïve default. The value is
   *recoverable* here from the stored `point_group` (C1 ⇒ 2), but that inference
   is not something a generic API consumer would know to make. **Recommended: (a)
   populate `statmech.optical_isomers` for the corpus, and (b) expose it (and
   `point_group`) on the statmech read payload.**
2. **Rotor `top_description` is NULL.** The rotating-top atom set is not stored.
   It is derivable from the stored geometry for simple acyclic rotors, but for
   ring or coupled rotors that derivation is ambiguous, so storing the top
   explicitly would make the data self-describing. Symmetry and pivots *are*
   served, so this is the only rotor-topology piece missing.
3. **Per-mode harmonic frequencies remain DB-only** (`calc_freq_mode`), same as
   the methane finding — still the single most important statmech input that no
   `/scientific/*` endpoint returns.

The good news dominates: the rotor **potential** — the datum most likely to be
missing — is fully served by the `/scan` endpoint, and symmetry + pivots are on
the torsions payload. No rotor data had to be fabricated.

## Conclusion

For ethylperoxy, **TCKDB is statmech-complete for a floppy, multi-rotor,
chiral radical**: geometry, the full unprojected frequency set, both rotor scan
potentials, rotor symmetries and pivots, multiplicity, and (via `point_group`)
the optical-isomer count regenerate S298 to 0.02 % and Cp(T) to < 0.06 % of the
stored values. The load-bearing actionable finding is that **`optical_isomers`
must be populated and exposed** — for a chiral species the naïve default is
wrong by R·ln 2 (1.84 % of S298), whereas for methane it was harmlessly correct.
Storing the rotor `top` explicitly and exposing per-mode frequencies are the two
lesser follow-ups.
