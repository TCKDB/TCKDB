# Gaussian composite-method logs

Real, unedited Gaussian output logs, copied in so the parser tests do not depend on
another checkout. Used by `tests/services/test_gaussian_composite_parser.py` and
`tests/services/test_gaussian_composite_block_checks.py`.

Licences (both MIT; the licence text is the `LICENSE` / `LICENSE.txt` file at the root of each repository):

- ARC: "Copyright (c) 2018-2023, Dana Research Group, Technion -- Israel Institute of Technology"
- RMG-Py: "Copyright (c) 2002-2026 Prof. William H. Green (whgreen@mit.edu), Prof. Richard H. West (r.west@neu.edu) and the RMG Team (rmg_dev@mit.edu)"

| File | Method | Gaussian | Read? | Origin |
|---|---|---|---|---|
| `cbs_qb3_ts_c2h5no2_g16.out` | CBS-QB3 | 16 Rev B.01 | yes | ARC `arc/testing/composite/C2H5NO2__C2H5ONO.out` (d9f47ab9) |
| `cbs_qb3_ts_intra_h_migration_g09.out` | CBS-QB3 | 09 Rev D.01 | yes | ARC `arc/testing/composite/TS_intra_H_migration_CBS-QB3.out` |
| `cbs_qb3_so2oo_g03.log` | CBS-QB3 | 03 | yes | ARC `arc/testing/composite/SO2OO_CBS-QB3.log` |
| `rocbs_qb3_methanol_g16.out` | ROCBS-QB3 | 16 Rev A.03 | yes | RMG-Py `arkane/data/gaussian/rocbs-qb3_85_methanol.out` (62eb728c0) |
| `cbs_4m_methanol_g16.out` | CBS-4M | 16 Rev A.03 | yes | RMG-Py `arkane/data/gaussian/cbs-4m_85_methanol.out` |
| `g3_ethylene_g03.log` | G3 | 03 Rev B.05 | yes | RMG-Py `arkane/data/gaussian/ethylene_G3.log` |
| `g4_methanol_g16.out` | G4 | 16 Rev A.03 | **declined** | RMG-Py `arkane/data/gaussian/g4_85_methanol.out` |
| `g4mp2_methanol_g16.out` | G4MP2 | 16 Rev A.03 | **declined** | RMG-Py `arkane/data/gaussian/g4mp2_85_methanol.out` |

**The two G4 logs are kept as negative fixtures.** In them the printed labels of the summary
block are shifted by one pair: the archive states `\G4=-115.6517642` / `\G4MP2=-115.571053`
while the `(0 K)` lines state -115.648433 / -115.566778, and the manual's identity
`Energy - E0 = E(Thermal) - E(ZPE)` fails by 0.002387 / 0.030352 hartree. The parser returns
nothing for them, and the tests pin why.

**Untested (no real log was available, none was fabricated):** CBS-APNO, G3B3, G3MP2,
G3MP2B3, W1U, W1BD, W1RO. The parser returns nothing for them.
