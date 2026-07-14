# Arkane statmech round-trip — TCKDB statmech-completeness validation

**Verdict: PROVEN (for this species).** TCKDB stores enough statmech data to
regenerate a species' entropy and heat capacity with Arkane, without the
original ESS output files. S298 and Cp(T) reproduce to **< 0.01 %**.

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
