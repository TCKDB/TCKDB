# TCKDB Scientific Knowledge

TCKDB preserves scientific identities, evidence, products, and curation history for gas-phase thermochemistry and kinetics.

## Language

**Scientific record**:
An identified scientific assertion or evidence object that can be reviewed, cited, assessed, or superseded.
_Avoid_: Row, datum

**Accepted science**:
A scientific record that has ever received explicit approval, even if its current review state later changes.
_Avoid_: Current approved row

**Supersession**:
An explicit directed statement that a new scientific record corrects or replaces an older record of the same kind without altering the older record.
_Avoid_: Overwrite, edit, replacement in place

**Reproducibility assessment**:
An append-only, rubric-versioned evaluation of whether a scientific record has insufficient evidence or is described, auditable, or rerunnable from preserved evidence.
_Avoid_: Trust score, review status

**Insufficient**:
An assessed record that does not meet every minimum requirement for the described grade.
_Avoid_: Described, unassessed

**Described**:
A record whose scientific meaning, conditions, and source attribution are sufficient to understand the assertion.

**Auditable**:
A described record whose evidence and provenance chain can be inspected through integrity-verified preserved artifacts.

**Rerunnable**:
An auditable record with the inputs, execution recipe, environment, and dependency closure needed to repeat the computation.
_Avoid_: Bitwise reproducible

**Archive**:
A versioned, integrity-checked package that preserves the declared TCKDB state and can restore it into an empty compatible database.
_Avoid_: Export projection, backup

**Export projection**:
A selected or transformed view for a consumer, which may filter candidates or omit provenance and is not a restoration format.
_Avoid_: Archive

**Operational backup**:
A deployment recovery copy of the database and object store, including operational state that the scientific archive may intentionally exclude.
_Avoid_: Archive export
