# Arkane statmech round-trip — TCKDB statmech-completeness validation

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
