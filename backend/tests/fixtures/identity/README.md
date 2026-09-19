# Identity challenge set

A frozen, publishable set of species-identity cases with their expected
relations. Every case is resolved through the production services
(`app.services.species_resolution.resolve_species_entry`) by
`backend/tests/services/test_identity_fixtures.py`, which fails if any file
here is empty. `backend/tests/services/identity_fixtures.py` loads the set and
reports counts by class and expected outcome.

## The identity contract being tested

- **Species** (`species`) is keyed on canonical SMILES + charge + multiplicity
  (unpublished decision record DR-0031, not in the repository).
  The InChIKey is stored for lookup but is not the key, so tautomers that the
  standard InChIKey merges stay apart, and two spin states of one graph share
  an InChIKey while being two species. Multiplicity is authoritative over the
  radical count RDKit infers from a SMILES; the declared charge is validated
  against the formal charge summed from the SMILES.
- **Stereo** is classified on the species (`stereo_kind`; unpublished
  decision record DR-0018, not in the repository) and labelled on the entry
  (`species_entry.stereo_label`), perceived from the deposited 3D geometry by
  `app.chemistry.species.derive_stereo_label_from_3d`. Only configuration
  (R/S, E/Z) is labelled; rotamers of one configuration share one entry.
  An entry deposited with no geometry carries a NULL label and is a distinct
  row from a labelled one. There is no stereo backfill for entries deposited
  before the 3D-perception repair; that absence is pinned here, not hidden.
- **Isotopes** are atom-resolved and derived, never uploaded: the entry's
  `isotope_key` is the canonical SMILES of the isotope-labelled molecule and
  NULL for the all-standard species (`app.chemistry.species.canonical_isotope_key`;
  the contract is stated in `backend/docs/specs/pdep_upload_contract_v2.md`
  and in `backend/tests/services/test_species_isotope_identity.py`; no
  decision record exists for it). Isotopologues share one species; isotopomers
  are distinct entries. A deposited geometry's isotope *multiset* (how many
  atoms of each element carry which mass number) must agree with the
  identity's labels; per-atom *placement* is not checked, so an identity of
  `[2H]OC` accepts a geometry that deuterates a methyl hydrogen instead
  (`assert_geometry_isotopes_match_identity`, documented as a known false
  acceptance in `backend/docs/specs/pdep_upload_contract_v2.md`). That gap is
  pinned as a fixture case, not hidden.

## File format

One JSON file per class: `tautomers.json`, `stereo.json`,
`isotopologues.json`, `spin.json`, `charge.json`, `conformers.json`. Each is a
non-empty list of cases:

```json
{
  "id": "<class>/<slug>",
  "description": "...",
  "inputs": [
    {"smiles": "N=N", "charge": 0, "multiplicity": 1,
     "geometry": {"xyz_text": "...", "isotopes": {"3": 2}}}
  ],
  "expected": "same_species_entry | distinct_species_entries | same_species_distinct_entries | rejected",
  "rejection": {"match": "<regex the ValueError must match>"},
  "stereo_labels": ["Z", "E"],
  "isotope_keys": [null, "[2H]CO"],
  "reason": "..."
}
```

- `geometry` is optional; `isotopes` maps 1-based atom index to mass number.
- `expected` semantics, over the entries the inputs resolve to:
  - `same_species_entry`: exactly one `species_entry` row (hence one species);
  - `same_species_distinct_entries`: one `species` row, one distinct entry per
    input;
  - `distinct_species_entries`: one distinct `species` row per input (hence
    distinct entries);
  - `rejected`: exactly one input, whose resolution raises a `ValueError`
    matching `rejection.match`.
- `stereo_labels` / `isotope_keys` are optional and positional; when present
  the test asserts them against the resolved entries.

Geometries were embedded once with RDKit ETKDG (seeded) + MMFF from a
configuration-bearing SMILES and frozen here; the methanol geometry is the
one used by the isotope identity tests. Regenerating them is not part of the
test: the coordinates are the fixture.
