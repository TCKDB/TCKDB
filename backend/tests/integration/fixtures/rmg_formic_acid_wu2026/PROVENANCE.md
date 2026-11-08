# RMG formic acid pyrolysis and oxidation model (Wu and Grinberg Dana 2026) — round-trip test fixture

A published, unmodified RMG model, used as the second real-data fixture for the
CHEMKIN round-trip test (`tests/integration/test_chemkin_round_trip_real.py`).

| File | Role |
|---|---|
| `chem_annotated.inp` | CHEMKIN mechanism: `ELEMENTS` / `SPECIES` / `THERM ALL` (inline NASA-7) / `REACTIONS` (KCAL/MOLE, MOLES) |
| `species_dictionary.txt` | RMG adjacency lists: the structure map that resolves each species name to SMILES and multiplicity |
| `chem_annotated.yaml` | The authors' own Cantera file for the same model (the only place the model's transport data lives; the test reads it only to pin the model's size) |
| `RMG_input.txt` | The RMG input that generated the model |

**Source.** J. Wu and A. Grinberg Dana, "Using RMG Out-of-the-Box for Formic
Acid Pyrolysis and Oxidation", *ACS Omega* **2026**, 11, 6371–6381,
doi:[10.1021/acsomega.5c11182](https://doi.org/10.1021/acsomega.5c11182).
The files are the paper's Supporting Information, copied unchanged
(`Cantera_simulation.txt`, an example script, is not included).

**Licence.** The article and its Supporting Information are published under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The files are
reproduced here with that attribution; they were not modified.

**Content.** 123 species and 3002 reactions. By form (Cantera 3.2.0 loading the
authors' YAML): 1288 Arrhenius, 1674 Chebyshev, 10 PLOG, 8 Troe, 4 Lindemann,
17 three-body and 1 three-body PLOG; 114 reactions flagged duplicate. The
CHEMKIN file carries 111 `DUPLICATE` lines. Several labels share a formula but
differ in structure or spin: CO2(12) is the singlet and CO2(158) the triplet;
there are two O, two CH2 and three CO3.

**Known bad `DUPLICATE` flags (five).** The CHEMKIN file fails Cantera's strict
check (`ck2yaml`, then `cantera.Solution`); the authors' YAML loads. Reaction
numbers count the reactions in file order; line numbers are lines of
`chem_annotated.inp`.

| Reaction no. (line) | Equation | Problem |
|---|---|---|
| 223 (1976) and 313 (2660) | O(6) + CO3(212) <=> O2(2) + CO2(12) | the pair is repeated without `DUPLICATE` |
| 387 (3262) and 389 (3284) | O(6) + CO3(212) <=> O2(2) + CO2(158) | the pair is repeated without `DUPLICATE` |
| 412 (3449; marker on 3459) | HOCH2O(35) <=> H(3) + HOCHO(1) | `DUPLICATE` with no partner |

Correcting those five flags makes the file load. The model is deposited and
exported unmodified: the test checks that the importer reports exactly these
five, and that the export declares the two pairs and leaves 412 unmarked.
