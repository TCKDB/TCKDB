"""The declared vocabularies, one per closed token set a reader meets.

Every entry here is a claim, in the same sense a scientific-check
register entry is: *this token can reach a reader, and chemistry alone
does not decode it*. The first half of that claim is checked
mechanically against :mod:`app.glossary.reachability`; the second is the
judgement the declaration exists to record.

Prose obligations, so the glosses stay honest:

* Define the software term, assume the chemistry. A reader is a
  chemist; they do not know what TCKDB means by "opaque ref" and they do
  know what a saddle point is.
* Never describe behaviour the code does not have. Where a gloss states
  a rule (a threshold, an ordering, a default), it is stating one the
  source states.
* The ``example`` and ``worked_example`` fields carry the concrete case
  for the tokens whose meaning turns on something non-obvious. They are
  deliberately not on every term: an example on a token that needs none
  is noise that hides the ones that do.
"""

from __future__ import annotations

from app.db.models.common import (
    ArtifactIntegrityFinding,
    AtomMapSource,
    CalculationQuality,
    CalculationType,
    DatasetReleaseStatus,
    KineticsDirection,
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
    ReleaseArtifactKind,
    ReleaseSelectionAction,
    ReproducibilityGrade,
    ScientificOriginKind,
    TransitionStateEntryStatus,
    ValidationStatus,
)
from app.glossary import Group, Term, Vocabulary
from app.schemas.reads.scientific_common import (
    CollapseMode,
    GeometryValidationStatus,
    SelectionPolicy,
)
from app.schemas.reads.scientific_reactions import (
    ReactionDirectionQuery,
    ReactionMatchMode,
)
from app.schemas.reads.scientific_structure_search import (
    StructureQueryKind,
    StructureSearchMode,
)
from app.services.trust.models import EvidenceBadge, EvidenceOutcome, HardFailReason

__all__ = ["VOCABULARIES", "vocabularies"]


_REVIEW = Vocabulary(
    group=Group.review,
    title="Review status",
    enum=RecordReviewStatus,
    carried_by=(
        "`review.status` on a record, `trust.review_status` inside the trust "
        "fragment, the `review_summary` counts on a search response, and the "
        "`min_review_status` filter on a request"
    ),
    summary=(
        "What a **human curator** has said about one scientific record. It is "
        "the only vocabulary here that reports a person's judgement; everything "
        "under *What TCKDB can check by itself* is computed and says nothing "
        "about whether anybody has looked."
    ),
    terms=(
        Term(
            token="not_reviewed",
            means=(
                "Nobody has looked at this record. It is the state every "
                "deposit lands in, and it is a statement about TCKDB's "
                "attention, not about the science: an unreviewed record can be "
                "perfectly good."
            ),
        ),
        Term(
            token="under_review",
            means=(
                "A curator has this record open. It is entered deliberately by "
                "a curator, never by depositing, which is the whole difference "
                "from `not_reviewed`."
            ),
            example=(
                "Until 2026-08-24 every deposit was stamped `under_review` on "
                "arrival, so 1,153 records on the hosted database claimed "
                "somebody was looking at them when nobody was. Revision "
                "`c1d8f4a25b30` moved them to `not_reviewed`. If you read the "
                "hosted API before that date, `under_review` there meant "
                "nothing at all."
            ),
        ),
        Term(
            token="approved",
            means=(
                "A curator reviewed the record and accepts it. This is the "
                "floor the `curated` read profile draws at: a curated read "
                "returns approved records and nothing below them."
            ),
        ),
        Term(
            token="rejected",
            means=(
                "A curator reviewed the record and does not accept it. Absent "
                "from results unless the request asks for it with "
                "`include_rejected=true`."
            ),
        ),
        Term(
            token="deprecated",
            means=(
                "The record should no longer be used — superseded by a better "
                "one, or withdrawn. It is kept rather than deleted so an "
                "existing citation still resolves. Absent from results unless "
                "the request asks with `include_deprecated=true`."
            ),
        ),
    ),
    worked_example=(
        "When a response ranks candidates it orders them `approved`, "
        "`under_review`, `not_reviewed`, `deprecated`, `rejected` — "
        "`REVIEW_RANK` in `backend/app/schemas/reads/scientific_common.py`."
    ),
)


_TRUST_BADGE = Vocabulary(
    group=Group.trust,
    title="Trust status (the evidence badge)",
    enum=EvidenceBadge,
    carried_by="`trust.trust_status`, and `trust.evidence.label` beside it",
    summary=(
        "How much of the evidence TCKDB expects for this kind of record is "
        "actually attached. It is **completeness, not quality**: a "
        "`well_supported` rate coefficient is one whose paperwork is complete, "
        "not one TCKDB says is right. The first five are the completeness "
        "ladder, computed as a weighted ratio of the checks in the record's "
        "rubric; `hard_failed` is not on that ladder at all."
    ),
    terms=(
        Term(
            token="well_supported",
            means=(
                "Evidence completeness of 0.90 or better **and** every check "
                "the rubric marks `required` passed. The required-checks gate "
                "is separate from the ratio: a record cannot reach this badge "
                "on volume of evidence alone."
            ),
        ),
        Term(
            token="mostly_supported",
            means="Evidence completeness of 0.75 or better.",
        ),
        Term(token="partial", means="Evidence completeness of 0.50 or better."),
        Term(token="sparse", means="Evidence completeness of 0.25 or better."),
        Term(
            token="unsupported",
            means=(
                "Evidence completeness below 0.25. Incomplete, which is not the "
                "same as wrong — this is the expected badge for an old record "
                "deposited before TCKDB asked for much."
            ),
        ),
        Term(
            token="hard_failed",
            means=(
                "A discrete structural failure was found, and the badge is set "
                "by that finding rather than by the ratio. Always read "
                "`trust.evidence.hard_fail_reason` beside it: the reason says "
                "whether the record contradicts itself, or whether TCKDB can no "
                "longer produce the evidence it rests on."
            ),
        ),
    ),
)


_TRUST_OUTCOME = Vocabulary(
    group=Group.trust,
    title="Check outcomes",
    enum=EvidenceOutcome,
    carried_by=(
        "the **values** of the `trust.evidence.checks` map, whose keys are the "
        "check names listed further down"
    ),
    summary=(
        "What one deterministic check found. Four states and never a boolean: "
        "`missing` and `not_applicable` are different answers and collapsing "
        "them would penalise a record for a question nobody could ask."
    ),
    terms=(
        Term(
            token="passed",
            means="The check's condition held. Positive evidence, and it counts.",
        ),
        Term(
            token="missing",
            means=(
                "The check applied and did not pass. It counts against the "
                "record: it is inside the denominator and outside the "
                "numerator of the completeness ratio."
            ),
        ),
        Term(
            token="warning",
            means=(
                "The check applied, the underlying signal is tri-state, and the "
                "result is informational. Warning-kind checks carry zero weight, "
                "so this never moves the completeness ratio."
            ),
        ),
        Term(
            token="not_applicable",
            means=(
                "The question could not be asked of this record, so the check "
                "is excluded from **both** the numerator and the denominator — "
                "it neither helps nor hurts."
            ),
            example=(
                "When `geometry_validation_present_for_source_calculations` is "
                "`missing`, there is no validation verdict to inspect, so "
                "`geometry_validation_not_failed_for_source_calculations` is "
                "`not_applicable`: \"did it fail?\" has no answer. Rendering "
                "that as `false` would count the same absence twice."
            ),
        ),
    ),
    worked_example=(
        "Check names are written as assertions, so read the name and the value "
        "together: `\"ts_graph_or_smiles_present\": \"missing\"` means *there "
        "is no SMILES or mol blob on this transition-state entry*. Until "
        "2026-08-24 the same fact was reported as the string "
        "`ts_graph_or_smiles_present` sitting inside an array named "
        "`missing_checks`, which read as a double negative; the four arrays "
        "`passed_checks` / `missing_checks` / `warning_checks` / "
        "`not_applicable_checks` were replaced by this one map. If you are "
        "reading a client written before then, that is what those arrays were."
    ),
)


_HARD_FAIL = Vocabulary(
    group=Group.trust,
    title="Hard-fail reasons",
    enum=HardFailReason,
    carried_by="`trust.evidence.hard_fail_reason`, and only when `trust_status` is `hard_failed`",
    summary=(
        "Why a record was hard-failed. Each names one discrete, evidenced "
        "structural failure — never a low score. Six of them (`invalid_lj_pair`, "
        "`no_transport_property_present`, `no_thermo_representation_present`, "
        "`invalid_external_symmetry`, `invalid_torsion_dimension`, "
        "`multiplicity_invalid`) re-derive a rule the upload path already "
        "refuses, so seeing one means the record reached the database by some "
        "route that skipped upload validation — an archive restore, a data "
        "migration, a bulk importer or direct SQL. Treat those as an incident "
        "to trace, not as a record that merely scored badly."
    ),
    terms=(
        Term(
            token="calculation_missing",
            means="The calculation this verdict is about could not be loaded at all.",
        ),
        Term(
            token="calculation_rejected",
            means="The calculation's `quality` is `rejected` — a curator marked it unusable.",
        ),
        Term(token="kinetics_missing", means="The kinetics record could not be loaded."),
        Term(token="statmech_missing", means="The statistical-mechanics record could not be loaded."),
        Term(token="thermo_missing", means="The thermochemistry record could not be loaded."),
        Term(token="transport_missing", means="The transport record could not be loaded."),
        Term(
            token="species_entry_missing",
            means=(
                "The record does not point at the species entry it is supposed "
                "to describe, so there is nothing to attribute it to."
            ),
        ),
        Term(
            token="no_thermo_representation_present",
            means=(
                "A thermochemistry record carrying no thermochemistry: no NASA "
                "polynomial, no Wilhoit, no scalar values, no points. A "
                "backstop — the upload path refuses this."
            ),
        ),
        Term(
            token="no_transport_property_present",
            means=(
                "A transport record carrying no transport property at all. A "
                "backstop — the upload path refuses this."
            ),
        ),
        Term(
            token="invalid_lj_pair",
            means=(
                "The Lennard-Jones parameters are not a usable pair. A "
                "backstop — the upload path refuses this."
            ),
        ),
        Term(
            token="invalid_temperature_range",
            means=(
                "The record's validity range is definitionally impossible: a "
                "non-positive temperature, or a minimum above the maximum. "
                "Note that a single-temperature range (`tmin == tmax`) is legal "
                "and does **not** fire this, and there is no upper bound — "
                "shock-tube and plasma chemistry are not structurally broken."
            ),
        ),
        Term(
            token="invalid_external_symmetry",
            means=(
                "The external symmetry number is below 1. A backstop — the "
                "upload path refuses this."
            ),
        ),
        Term(
            token="invalid_torsion_dimension",
            means=(
                "A hindered-rotor torsion declares a dimension below 1. A "
                "backstop — the upload path refuses this."
            ),
        ),
        Term(
            token="geometry_validation_failed",
            means=(
                "TCKDB compared the calculation's geometry against the "
                "structure the record claims it is, and the comparison failed."
            ),
        ),
        Term(
            token="artifact_integrity_failed",
            means=(
                "The stored bytes behind one of this calculation's artifacts no "
                "longer match their digest, or are gone. This one is a "
                "statement about **TCKDB's custody of the evidence**, not about "
                "the depositor's science: the record may be perfectly good and "
                "we can no longer show you what it rests on. It reflects the "
                "latest observation per artifact, so a restored object clears "
                "it."
            ),
        ),
        Term(
            token="missing_required_identity",
            means=(
                "The kinetics record does not identify a complete reaction — no "
                "reaction entry, or a side with no participants on it."
            ),
        ),
        Term(
            token="source_calculation_hard_failed_for_required_role",
            means=(
                "A calculation this record depends on for a role it cannot do "
                "without — a reactant or product energy, the TS energy, the "
                "frequencies — is itself hard-failed. The failure is inherited, "
                "so read that calculation's own reason."
            ),
        ),
        Term(
            token="transition_state_entry_missing",
            means="The transition-state entry could not be loaded.",
        ),
        Term(
            token="transition_state_parent_missing",
            means="The entry does not point at the transition state it is an entry for.",
        ),
        Term(
            token="reaction_entry_missing",
            means="The parent transition state names no reaction entry, so the TS belongs to no reaction.",
        ),
        Term(
            token="ts_entry_status_rejected",
            means="The transition-state entry's own `status` is `rejected`.",
        ),
        Term(
            token="multiplicity_invalid",
            means="The spin multiplicity is below 1. A backstop — the upload path refuses this.",
        ),
        Term(
            token="all_source_calculations_hard_failed",
            means=(
                "Every calculation supporting this transition-state entry is "
                "itself hard-failed, so nothing is left to support it."
            ),
        ),
        Term(
            token="geometry_validation_failed_for_source_calculation",
            means="A calculation supporting this transition-state entry failed geometry validation.",
        ),
        Term(
            token="frequency_source_has_zero_imaginary_modes_for_validated_ts",
            means=(
                "A transition-state entry whose status is `optimized` or "
                "`validated`, whose frequency evidence reports no imaginary "
                "mode. The record says saddle point and the numbers say "
                "minimum."
            ),
        ),
        Term(
            token="frequency_source_reaction_coordinate_not_designated_for_validated_ts",
            means=(
                "The record reports more than one imaginary mode and does not "
                "say which one is the reaction coordinate. More than one "
                "imaginary mode is acceptable — this fires only on the missing "
                "designation, which is why it is a question about what was "
                "recorded and not about physics."
            ),
        ),
    ),
)


_REPRODUCIBILITY = Vocabulary(
    group=Group.trust,
    title="Reproducibility grade",
    enum=ReproducibilityGrade,
    carried_by="`assessments.reproducibility.grade`",
    summary=(
        "How far somebody else could get with what was deposited. An evidence "
        "ladder, independent of both the review status and the trust badge, and "
        "a statement about *completeness of the deposit* rather than a promise "
        "of bitwise-identical output. Only a calculation can be graded above "
        "`described`: a thermo or kinetics record keeps the limits of the "
        "sources behind it."
    ),
    terms=(
        Term(
            token="insufficient",
            means="The record does not even describe what was done well enough to audit.",
        ),
        Term(
            token="described",
            means=(
                "What the record is and the scientific context around it are "
                "recorded. The ceiling for every non-calculation record."
            ),
        ),
        Term(
            token="auditable",
            means=(
                "The preserved evidence can be inspected: the output bytes are "
                "there and were read back through the artifact path."
            ),
        ),
        Term(
            token="rerunnable",
            means=(
                "The deposit is complete enough to **attempt** a rerun — "
                "preserved inputs, an execution-parameter snapshot, the "
                "upstream dependency snapshot, and no warnings about artifact "
                "bytes TCKDB could not read. It is not a claim that a rerun "
                "would reproduce the numbers."
            ),
        ),
    ),
)


_READ_PROFILE = Vocabulary(
    group=Group.contract,
    title="Read profile",
    enum=ReadProfile,
    carried_by="`request.profile`, echoed on every scientific response",
    summary=(
        "Which contract the response answers under. Always echoed, never "
        "inferred: a consumer should not have to guess whether they are looking "
        "at the archive or at a curated set."
    ),
    terms=(
        Term(
            token="exploratory",
            means=(
                "Every visible candidate, each with its own review and trust "
                "state, and **no recommendation from TCKDB** about which is "
                "right. This is the default on every read surface, deliberately: "
                "on a corpus that is mostly uncurated a curated default returns "
                "an empty page and reads as a broken database."
            ),
        ),
        Term(
            token="curated",
            means=(
                "Only records at or above the `approved` review floor. Records "
                "are not annotated with any release selection that names them — "
                "for an attributed endorsement use the "
                "`/api/v1/scientific/releases/*` endpoints."
            ),
        ),
    ),
)


_PROFILE_RECOMMENDATION = Vocabulary(
    group=Group.contract,
    title="Profile recommendation",
    enum=ProfileRecommendation,
    carried_by="`request.profile_recommendation`, echoed beside `request.profile`",
    summary=(
        "Whether the records in this response carry a TCKDB recommendation. It "
        "is a separate field from the profile because none of its three values "
        "can be derived from the profile token alone."
    ),
    terms=(
        Term(
            token="none",
            means="These are candidates. TCKDB is not telling you which one to use.",
        ),
        Term(
            token="approved_floor_only",
            means=(
                "Every record shown is at or above the `approved` review floor "
                "and **nothing more is claimed**. A human accepted each of "
                "them, which is not the same as TCKDB preferring them over "
                "their siblings."
            ),
        ),
        Term(
            token="tckdb_curated_release",
            means=(
                "These records are the ones an attributed, append-only release "
                "selection names in a published release. Only the release "
                "endpoints emit this."
            ),
        ),
    ),
)


_RELEASE_STATUS = Vocabulary(
    group=Group.contract,
    title="Dataset release status",
    enum=DatasetReleaseStatus,
    carried_by="`status` on a dataset release",
    summary="Lifecycle of a citable dataset release.",
    terms=(
        Term(
            token="draft",
            means=(
                "Being assembled. Selections may still be appended, no manifest "
                "is frozen, and it is neither served under the curated profile "
                "nor citable."
            ),
        ),
        Term(
            token="published",
            means="The manifest is frozen and checksummed. The release is citable and its bytes are reproducible.",
        ),
        Term(
            token="withdrawn",
            means=(
                "It was published and later retracted. The row and its manifest "
                "are **kept** so an existing citation never dangles; the status "
                "is how a consumer is told not to rely on it."
            ),
        ),
    ),
)


_RELEASE_SELECTION_ACTION = Vocabulary(
    group=Group.contract,
    title="Release selection action",
    enum=ReleaseSelectionAction,
    carried_by="`action` on a release selection row",
    summary=(
        "What one selection row asserts. Selections are append-only and never "
        "edited: a curator changing their mind appends a row pointing at the "
        "one it replaces."
    ),
    terms=(
        Term(token="select", means="The first attributed selection of a candidate record."),
        Term(token="supersede", means="Replaces an earlier selection with a different candidate."),
        Term(
            token="withdraw",
            means=(
                "Retires an earlier selection with no replacement — the release "
                "then makes no recommendation for that subject."
            ),
        ),
    ),
)


_RELEASE_ARTIFACT_KIND = Vocabulary(
    group=Group.contract,
    title="Release artifact kind",
    enum=ReleaseArtifactKind,
    carried_by="`kind` on a file listed in a release manifest",
    summary=(
        "The role of one checksummed file inside a dataset release. A release "
        "deliberately ships the selection *and* the material needed to disagree "
        "with it."
    ),
    terms=(
        Term(token="selected_records", means="The records the release selects."),
        Term(
            token="candidate_records",
            means="The candidates that were **not** selected, so a reader can check the choice.",
        ),
        Term(token="review_history", means="The review decisions behind those records."),
        Term(token="selection_ledger", means="The append-only log of selection actions themselves."),
    ),
)


_DIRECTION = Vocabulary(
    group=Group.query,
    title="Reaction direction",
    enum=ReactionDirectionQuery,
    carried_by=(
        "the `direction` request parameter, and — the one that surprises people "
        "— `matched_direction` on every reaction and kinetics search result"
    ),
    summary=(
        "Which way round the stored equation had to be read for your query to "
        "match it. TCKDB stores a reaction in one orientation; a query naming "
        "the other side still matches, and `matched_direction` is how the "
        "response tells you that happened. It is present on every row, "
        "including the forward ones, so its absence never has to be "
        "interpreted."
    ),
    terms=(
        Term(
            token="forward",
            means=(
                "Your query matched the reaction as stored: what you asked for "
                "as reactants are that reaction's reactants."
            ),
        ),
        Term(
            token="reverse",
            means=(
                "Your query matched the reaction read backwards: **what you "
                "asked for as reactants are that reaction's products.**"
            ),
        ),
        Term(
            token="either",
            means=(
                "Match the reaction in whichever orientation works. This is the "
                "default on a request; it is not an answer a result row gives — "
                "a matched row always reports `forward` or `reverse`."
            ),
        ),
    ),
    worked_example=(
        "Search `reactants=NN` and one of the results is "
        "`[NH2] + [NH2] <=> NN` with `matched_direction: \"reverse\"`. Nothing "
        "is wrong: NN is stored as a **product** of that reaction, and reading "
        "the equation backwards puts it on the reactant side, which is what "
        "your query asked about. What `matched_direction` does **not** tell you "
        "is which way round the rate coefficients on that record run: a "
        "kinetics record carries its own `direction` (below), stated relative "
        "to the stored orientation. So read both — `matched_direction` says how "
        "TCKDB found the reaction for you, `direction` says what the numbers on "
        "it describe."
    ),
)


_KINETICS_DIRECTION = Vocabulary(
    group=Group.query,
    title="Kinetics direction",
    enum=KineticsDirection,
    carried_by="`direction` on a kinetics record, and the filter of the same name",
    summary=(
        "Which direction of the stored equation a rate coefficient describes. "
        "It is stated relative to the reaction entry's stored "
        "reactant-to-product orientation, which is what makes it a different "
        "question from `matched_direction`: that one is about your query, this "
        "one is about the numbers. A record may leave it unset — the historical "
        "default — in which case the value comes back empty and all you know is "
        "that the producer did not say."
    ),
    terms=(
        Term(
            token="forward",
            means="A fit of the rate in the stored orientation: reactants to products as written.",
        ),
        Term(
            token="reverse",
            means=(
                "A fit of the rate for the same equation run backwards. It can "
                "sit on the same reaction entry as a forward fit — Chemkin and "
                "Cantera give the two as separate expressions, so TCKDB keeps "
                "them as separate records rather than merging them."
            ),
        ),
        Term(
            token="net",
            means="A rate that already folds both directions together — an apparent or observed net rate.",
        ),
    ),
)


_MATCH_MODE = Vocabulary(
    group=Group.query,
    title="Participant match mode",
    enum=ReactionMatchMode,
    carried_by="the `match` request parameter on reaction and kinetics search",
    summary="How the species you listed are compared against a stored reaction's sides.",
    terms=(
        Term(
            token="contains",
            means=(
                "The default. Set containment per role: every species you name "
                "must appear in that role of the stored reaction, and a side you "
                "did not mention constrains nothing. Containment is by **set**, "
                "not multiset — naming a species once matches a reaction "
                "consuming two of it."
            ),
            example=(
                "`reactants=NN` alone means \"NN among the reactants, products "
                "unconstrained\" — which is what a chemist means by \"reactions "
                "of hydrazine\"."
            ),
        ),
        Term(
            token="exact",
            means=(
                "Multiset equality on both sides: precisely this equation, both "
                "sides, counts included. Ask for this when you want one specific "
                "reaction rather than a family."
            ),
        ),
    ),
)


_COLLAPSE = Vocabulary(
    group=Group.query,
    title="Collapse mode",
    enum=CollapseMode,
    carried_by="the `collapse` request parameter, echoed in `request.collapse`",
    summary="How many records per subject the response returns.",
    terms=(
        Term(token="all", means="Every eligible record, after filtering, sorting and pagination. The default."),
        Term(
            token="first",
            means=(
                "At most one record — zero or one — after filtering and "
                "sorting. Which one is decided by the selection policy below, "
                "and that choice is made at read time; it stores nothing."
            ),
        ),
    ),
)


_SELECTION_POLICY = Vocabulary(
    group=Group.query,
    title="Selection policy",
    enum=SelectionPolicy,
    carried_by="the `selection_policy` request parameter, meaningful when `collapse=first`",
    summary=(
        "Which candidate wins when the response is collapsed to one. Naming the "
        "policy makes \"show me one\" an explicit choice rather than a silent "
        "one. No policy persists a curator decision — for an attributed "
        "endorsement, use a dataset release."
    ),
    terms=(
        Term(token="default", means="The endpoint's standard ranking."),
        Term(token="latest", means="Most recently created first."),
        Term(token="most_reviewed", means="Best review status first, then most recent."),
    ),
)


_STRUCTURE_MODE = Vocabulary(
    group=Group.query,
    title="Structure search mode",
    enum=StructureSearchMode,
    carried_by="the `mode` request parameter on structure search, echoed in each match summary",
    summary="Which matching algorithm produced a structure-search hit.",
    terms=(
        Term(
            token="substructure",
            means="RDKit substructure containment: the stored molecule contains the query pattern.",
        ),
        Term(
            token="similarity",
            means=(
                "Tanimoto similarity over Morgan fingerprints. Only this mode "
                "populates `similarity_score` on a result."
            ),
        ),
        Term(token="exact", means="Equality of canonical InChIKey."),
    ),
)


_STRUCTURE_QUERY_KIND = Vocabulary(
    group=Group.query,
    title="Structure query kind",
    enum=StructureQueryKind,
    carried_by="`matched_query_kind` on a structure-search match summary",
    summary=(
        "Which of the query fields a hit came from, echoed so a caller with "
        "several query inputs can attribute a result without re-parsing the "
        "request."
    ),
    terms=(
        Term(token="smiles", means="The match came from the supplied SMILES."),
        Term(token="smarts", means="The match came from the supplied SMARTS pattern."),
        Term(token="inchi", means="The match came from the supplied InChI."),
        Term(token="inchi_key", means="The match came from the supplied InChIKey."),
    ),
)


_ORIGIN = Vocabulary(
    group=Group.provenance,
    title="Scientific origin",
    enum=ScientificOriginKind,
    carried_by="`scientific_origin` on a record, and the filter of the same name",
    summary="Where a number came from, before anything else is said about it.",
    terms=(
        Term(token="computed", means="Produced by a quantum-chemistry or kinetics calculation."),
        Term(
            token="experimental",
            means="Measured in a laboratory rather than computed or estimated.",
        ),
        Term(token="estimated", means="Estimated — group additivity, an analogy, a correlation."),
    ),
)


_CALCULATION_TYPE = Vocabulary(
    group=Group.provenance,
    title="Calculation type",
    enum=CalculationType,
    carried_by="`calculation_type` on a calculation record, and the filter of the same name",
    summary=(
        "What kind of job a stored calculation was. TCKDB records the job, not "
        "the intent: a `freq` on an unoptimised geometry is still a `freq`."
    ),
    terms=(
        Term(token="opt", means="Geometry optimisation."),
        Term(token="freq", means="Frequency (Hessian) calculation."),
        Term(token="sp", means="Single-point energy."),
        Term(token="irc", means="Intrinsic reaction coordinate following."),
        Term(token="scan", means="A scan over one or more internal coordinates."),
        Term(
            token="path_search",
            means=(
                "A reaction-path search producing a TS guess. Which algorithm "
                "ran (NEB, GSM, …) is recorded on the result row, not as a "
                "separate type."
            ),
        ),
        Term(
            token="conf",
            means=(
                "A conformer search — a job exploring the accessible "
                "conformations of one species."
            ),
        ),
    ),
)


_CALCULATION_QUALITY = Vocabulary(
    group=Group.provenance,
    title="Calculation quality",
    enum=CalculationQuality,
    carried_by="`quality` on a calculation record, and the filter of the same name",
    summary=(
        "A curation flag on one calculation, separate from the review status of "
        "the record it supports."
    ),
    terms=(
        Term(
            token="raw",
            means=(
                "The default every calculation is stored with. It means nobody "
                "has curated it, not that anything is wrong."
            ),
        ),
        Term(token="curated", means="Someone has curated this calculation and stands behind it."),
        Term(
            token="rejected",
            means=(
                "Marked unusable. Such calculations are excluded from results "
                "unless a request opts in with `include_rejected_quality=true`, "
                "and a record resting on one is hard-failed with "
                "`calculation_rejected`."
            ),
        ),
    ),
)


_VALIDATION_STATUS = Vocabulary(
    group=Group.provenance,
    title="Geometry validation status",
    enum=ValidationStatus,
    carried_by=(
        "`validation_status` on a calculation's geometry-validation summary, and "
        "`geometry_validation_status` on a calculation evidence summary"
    ),
    summary=(
        "What TCKDB found when it compared a calculation's geometry against the "
        "structure the record claims it is — connectivity and molecular "
        "identity, not energetics."
    ),
    terms=(
        Term(token="passed", means="The geometry is the structure the record claims."),
        Term(
            token="warning",
            means=(
                "Something differs and TCKDB will not call it a failure. An "
                "optimisation that drifted is science to record, not a payload "
                "to refuse."
            ),
        ),
        Term(
            token="fail",
            means=(
                "The geometry is not the claimed structure. This hard-fails the "
                "calculation's trust badge with `geometry_validation_failed`."
            ),
        ),
    ),
    projection=GeometryValidationStatus,
    projected=(
        Term(
            token="not_present",
            means=(
                "Nobody checked. There is no validation row for this "
                "calculation, and the read layer says so rather than leaving "
                "the field empty — an absent check and a passed check are "
                "different answers and TCKDB will not let them look alike."
            ),
        ),
    ),
)


_ATOM_MAP_SOURCE = Vocabulary(
    group=Group.provenance,
    title="Atom-map source",
    enum=AtomMapSource,
    carried_by="`source` on a reaction's atom-map badge",
    summary=(
        "How the atom-to-atom correspondence across a reaction was obtained. "
        "The column has no default: a map that cannot say how it was obtained "
        "is not a map TCKDB accepts."
    ),
    terms=(
        Term(
            token="declared",
            means=(
                "A depositor stated the correspondence — they ran the "
                "calculation and followed the reaction coordinate, so they know "
                "which atom went where."
            ),
        ),
        Term(
            token="inferred",
            means=(
                "An algorithm produced it. Read back as inferred, never as "
                "though a human asserted it."
            ),
        ),
    ),
)


_TS_ENTRY_STATUS = Vocabulary(
    group=Group.provenance,
    title="Transition-state entry status",
    enum=TransitionStateEntryStatus,
    carried_by="`status` on a transition-state entry",
    summary="How far a transition-state entry has been taken by whoever deposited it.",
    terms=(
        Term(token="guess", means="A starting structure. Not optimised."),
        Term(token="optimized", means="Optimised to a stationary point."),
        Term(
            token="validated",
            means=(
                "Optimised and checked as a transition state for the reaction "
                "it claims — the strongest thing an entry says about itself."
            ),
        ),
        Term(
            token="rejected",
            means=(
                "Kept, but not to be used. It hard-fails the entry's trust badge "
                "with `ts_entry_status_rejected`."
            ),
        ),
    ),
)


_ARTIFACT_INTEGRITY = Vocabulary(
    group=Group.provenance,
    title="Artifact integrity finding",
    enum=ArtifactIntegrityFinding,
    carried_by="`finding` on an artifact-integrity observation",
    summary=(
        "What was observed when TCKDB read stored bytes back and checked them "
        "against their digest. Not severities: four different observations, and "
        "the log is append-only, so a repaired object is a **new** observation "
        "rather than an edit."
    ),
    terms=(
        Term(
            token="digest_mismatch",
            means="The object was read and does not hash to the key it is stored under. The bytes are not the bytes TCKDB claims to hold.",
        ),
        Term(
            token="size_mismatch",
            means=(
                "The digest could not be faulted but the length differs from "
                "the byte count on the artifact row — almost always a truncated "
                "read rather than a changed object."
            ),
        ),
        Term(token="object_missing", means="The object is absent from the store entirely."),
        Term(
            token="verified",
            means=(
                "The object was read and does hash to its key. Recorded only "
                "for a digest that already carries a break — this is the "
                "observation that clears one, which is why a restored record "
                "does not stay condemned forever."
            ),
        ),
    ),
)


#: Every declared vocabulary, in reading order within each group.
VOCABULARIES: tuple[Vocabulary, ...] = (
    _REVIEW,
    _TRUST_BADGE,
    _TRUST_OUTCOME,
    _HARD_FAIL,
    _REPRODUCIBILITY,
    _READ_PROFILE,
    _PROFILE_RECOMMENDATION,
    _RELEASE_STATUS,
    _RELEASE_SELECTION_ACTION,
    _RELEASE_ARTIFACT_KIND,
    _DIRECTION,
    _KINETICS_DIRECTION,
    _MATCH_MODE,
    _COLLAPSE,
    _SELECTION_POLICY,
    _STRUCTURE_MODE,
    _STRUCTURE_QUERY_KIND,
    _ORIGIN,
    _CALCULATION_TYPE,
    _CALCULATION_QUALITY,
    _VALIDATION_STATUS,
    _ATOM_MAP_SOURCE,
    _TS_ENTRY_STATUS,
    _ARTIFACT_INTEGRITY,
)


def vocabularies() -> tuple[Vocabulary, ...]:
    """The declared vocabularies. A function, so callers cannot mutate the tuple's home."""
    return VOCABULARIES
