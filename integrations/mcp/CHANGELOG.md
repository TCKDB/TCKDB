# Changelog

## 0.10.0 - 2026-10-18

Two read-only tools for relative conformer energies: `tckdb_species_entry_relative_conformer_energies` and
`tckdb_compare_relative_conformer_energies`, wrapping `POST .../species-entries/{spe_ref}/conformers/relative-energies` and
`.../compare`. The reference conformer (`cg_`), energy kind, certificate and the level (exactly one of `lot_` and `csch_`) are
required with no default; the comparison also requires `candidate`, `reference` and `pairing`. Ref prefixes are checked before any
request. The response is returned unchanged; quote `outcome`, `basis` and `claims.statement` verbatim. The comparison is
descriptive only and every deviation depends on the named reference conformer.
