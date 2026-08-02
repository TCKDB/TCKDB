# Psi4 fixtures

Real Psi4 output logs, **truncated to the header region the parser reads**.

Everything TCKDB extracts from a Psi4 log today — the banner used by
`detect_software_from_text` and the charge/multiplicity declarations read by
`app.services.psi4_parameter_parser` — lives in the first ~170 lines. The
original files are 26 KB – 972 KB, almost all of it SCF iterations and
gradients that no wired parser touches, so each fixture is the source file cut
with `head -N` at a line chosen to sit just past the `Charge` / `Multiplicity`
block. No other edit was made (the one exception is noted below), so every byte
kept is a byte Psi4 actually wrote.

| Fixture | Source | Truncation | Charge / multiplicity |
|---|---|---|---|
| `sp_mrcc_triplet.dat` | `ARC/arc/testing/sp/psi4_mrcc.dat` (ARC test data, `ReactionMechanismGenerator/ARC`) | `head -170` | 0 / **3** |
| `sp_nh2_doublet.dat` | `MRCC-Zeus/NH2/output.dat` (M. Keslin, hydrazine study, Zeus cluster) | `head -155` | 0 / **2** |
| `opt_freq_singlet.out` | `RMG-Py/arkane/data/psi4/opt_freq.out` | `head -112` | 0 / **1** |
| `opt_freq_dft_ts_singlet.out` | `RMG-Py/arkane/data/psi4/opt_freq_dft_ts.out` | `head -120` | 0 / **1** |
| `io_error_truncated.out` | `RMG-Py/arkane/data/psi4/IO_error.out` | `head -100` | **none — deliberately cut short** |

## Why each one is here

- **`sp_mrcc_triplet.dat`** — an MRCC-driven single point, the only readily
  available real triplet. Also the reason Psi4 single-point *energy* extraction
  is not wired: the full log carries many `Total Energy` lines and picking the
  right one is method-dependent.
- **`sp_nh2_doublet.dat`** — an open-shell doublet (UHF reference). Covers the
  odd-electron case.
- **`opt_freq_singlet.out`** / **`opt_freq_dft_ts_singlet.out`** — an opt+freq
  minimum and a DFT transition-state search. Psi4 prints the same
  charge/multiplicity block regardless of method or stationary-point type; the
  full sources repeat it 23 and 62 times respectively, always in agreement.
- **`io_error_truncated.out`** — two jobs in one. Its source is a run that died
  with a `Fatal Error: PSIO Error`, and its banner is a **development build**
  (`Psi4 1.4a1.dev75`) with no `release` suffix, which is why the detection
  marker is anchored on the banner title rather than on a version line. It is
  cut at line 100, *before* the first declaration at line 115, so it also
  exercises the truncated-log path: the program is still identified as `psi4`,
  but no charge or multiplicity can be read and the reconciliation must report
  *unknown* rather than guess.

## Redaction

`sp_nh2_doublet.dat` is the only fixture sourced from unpublished data. Its
`PSIDATADIR:` line originally held a collaborator's cluster account path; that
one path was rewritten to `/home/user/`, the same placeholder the Molpro
fixtures already carry. Psi4 prints it as a runtime banner detail and no parser
reads it, so the substitution cannot change any parse result. The other four
sources are already public (ARC and RMG-Py test data) and are byte-identical to
`head -N` of their upstream files.
