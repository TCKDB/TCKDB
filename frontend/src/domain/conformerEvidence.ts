import type { ConformerProjection } from "../api/speciesEntryApi"

// The archive's own auto-numbering convention for a basin that was never
// given a real depositor-chosen label: `conformer_<N>`. A reader who
// doesn't know that convention reads it as an opaque token, not as "the
// Nth conformer group" -- so it displays as "Conformer Group N" instead.
// Anchored at both ends so a label that merely CONTAINS the pattern (a
// depositor-chosen label like "conformer_1_reoptimized") is left alone.
const AUTO_NUMBERED_LABEL = /^conformer_(\d+)$/

/**
 * The label a reader picks a conformer by.
 *
 * - No deposited label at all (`null`), or a deposited label that is empty
 *   or whitespace-only: falls back to the group's own stable ref, verbatim.
 *   A blank string is not a usable label any more than a missing one is --
 *   without this, a blank label would render as an empty heading ("Evidence
 *   for ") and a blank card, which is worse than the honest fallback. A ref
 *   is not a label and is never run through the pattern below -- this
 *   function never invents a display name for a group that was never given
 *   a real one.
 * - A deposited label matching the archive's `conformer_<N>` auto-numbering
 *   convention: displays as "Conformer Group N".
 * - Any other deposited label (including one a depositor chose themselves,
 *   even if it happens to contain the word "conformer"): renders verbatim.
 *   This function never coerces a label into a shape it does not have.
 */
export function conformerLabel(conformer: ConformerProjection): string {
    const label = conformer.conformer_group.label
    if (label === null || label.trim() === "") return conformer.conformer_group.conformer_group_ref
    const match = label.match(AUTO_NUMBERED_LABEL)
    return match ? `Conformer Group ${match[1]}` : label
}

/**
 * The numeral behind an auto-numbered label (`conformer_7` -> `7`), parsed
 * as a NUMBER -- never the label sorted as a string, which is what would
 * put `conformer_10` between `conformer_1` and `conformer_2`. `null` for
 * anything the archive's own auto-numbering pattern doesn't match (no
 * label, a blank label, or a depositor-chosen name): those conformers have
 * no numeral to sort by at all, not a numeral of `0` or `-1` invented to
 * give them one. Reuses `AUTO_NUMBERED_LABEL`, the SAME pattern
 * `conformerLabel` matches against -- a second regex here could disagree
 * with what the reader sees as "Conformer Group N" and silently sort by a
 * rule the display doesn't use.
 */
export function conformerDisplayNumber(conformer: ConformerProjection): number | null {
    const label = conformer.conformer_group.label
    if (label === null) return null
    const match = label.match(AUTO_NUMBERED_LABEL)
    return match ? Number(match[1]) : null
}

/**
 * Reorders conformers for the READER, not for the archive: `conformers/search`
 * returns its own deliberate ranking (review rank, then recency), which this
 * function never mutates in place and never claims to improve on -- it
 * returns a NEW array, so any caller still holding the original (e.g. the
 * `conformers` list `partitionByConformerLink` searches for an "other
 * conformer"'s label, where lookup is by ref and order is irrelevant) keeps
 * seeing the archive's own order untouched.
 *
 * Numbered conformers sort ascending by their PARSED numeral (`conformer_2`
 * before `conformer_10`, never string order) via `conformerDisplayNumber`.
 * Everything without a numeral -- no label, a blank label, a depositor's own
 * name -- sorts after every numbered conformer, then alphabetically by its
 * own display label (`conformerLabel`), so two non-numbered conformers land
 * in a defined, readable order rather than an arbitrary one. The final
 * tiebreaker is each conformer's own stable ref: two conformers are never
 * exactly equal under this comparator, so the order is TOTAL, and
 * `Array.prototype.sort` is spec-guaranteed stable -- together, two
 * conformers never swap places between renders of the same input.
 */
export function sortConformersForDisplay(conformers: ConformerProjection[]): ConformerProjection[] {
    return [...conformers].sort((a, b) => {
        const numberA = conformerDisplayNumber(a)
        const numberB = conformerDisplayNumber(b)
        if (numberA !== null && numberB !== null) return numberA - numberB
        if (numberA !== null) return -1
        if (numberB !== null) return 1
        const labelOrder = conformerLabel(a).localeCompare(conformerLabel(b))
        if (labelOrder !== 0) return labelOrder
        return a.conformer_group.conformer_group_ref.localeCompare(b.conformer_group.conformer_group_ref)
    })
}

// Stages read in a fixed, chemistry-meaningful order (opt precedes freq
// precedes sp, the order evidence normally accumulates) with any type this
// module doesn't specifically know about appended afterward -- never
// dropped, just unordered relative to the three named stages.
const KNOWN_CALCULATION_TYPE_ORDER = ["opt", "freq", "sp"]

export type CalculationTypeCount = { type: string; count: number }

/**
 * Raw calculation-row counts by stage (`type`), read off the conformer's
 * own flat `calculations` list -- e.g. 7 opt + 4 freq + 3 sp. This is a
 * DIFFERENT number from `evidence_summary.evidence_coverage`, which counts
 * observations having a stage, not calculation rows of that stage. Never
 * conflate the two: this function answers "how many rows", coverage
 * answers "how many observations".
 */
export function calculationTypeCounts(conformer: ConformerProjection): CalculationTypeCount[] {
    const counts = new Map<string, number>()
    for (const calculation of conformer.calculations ?? []) {
        counts.set(calculation.type, (counts.get(calculation.type) ?? 0) + 1)
    }
    const known = KNOWN_CALCULATION_TYPE_ORDER
        .filter((type) => counts.has(type))
        .map((type) => ({ type, count: counts.get(type) as number }))
    const rest = [...counts.entries()]
        .filter(([type]) => !KNOWN_CALCULATION_TYPE_ORDER.includes(type))
        .map(([type, count]) => ({ type, count }))
    return [...known, ...rest]
}

// ---------------------------------------------------------------------------
// Optimization staging -- "is the opt row count also counting the pre-opt?"
//
// `conformers/search` (the endpoint this page reads) does NOT expose
// `calculation_dependency.dependency_role` per calculation or per
// observation -- verified against the live wire (a `curl` of
// spe_bcbdjwkip75yoziblpntwzblzu's `?include=calculations` response has no
// `dependency_role`/`dependencies` key on any calculation object) and
// against the schema (`ConformerCalculationSummary`,
// `backend/app/schemas/reads/scientific_conformer.py:345`, has no such
// field; `ConformersSearchRequest`'s `include` tokens do not offer one
// either). The dependency graph exists -- the server computes
// `evidence_summary.optimization_chain_count` FROM it
// (`backend/app/services/scientific_read/conformers.py:603-661`) -- but
// that computation happens server-side and only its AGGREGATE result
// reaches this client. Nothing here reads `calculation_dependency` because
// nothing here CAN; the functions below are built entirely from counts the
// wire actually states, deriving only what those counts can honestly prove.
// ---------------------------------------------------------------------------

export type OptimizationStaging =
    // No opt-row breakdown loaded at all (`calculations` not fetched) --
    // nothing safe to say about staging, not even the aggregate.
    | { kind: "unknown" }
    // Aggregate-only: staging math is sound (staged rows = raw opt rows -
    // chains, both real wire fields), but WHICH sighting was staged more
    // than once cannot be attributed -- either because the group has an
    // independent (non-collapsing) chain living on the same observation as
    // another chain (`chainCount !== observations with opt`), or because
    // not every observation's own calculation list is loaded.
    | { kind: "aggregate"; rawOptCount: number; chainCount: number; stagedRowCount: number }
    // Per-sighting: safe to say which observation was staged and how many
    // times, because the group's own chain count equals the number of
    // observations carrying opt evidence -- no observation can be hiding a
    // SECOND independent chain behind its raw row count, so every extra row
    // on an observation is necessarily a stage of that observation's one
    // chain, never a parallel attempt.
    | { kind: "per-observation"; rawOptCount: number; chainCount: number; stagedRowCount: number; perObservation: Map<string, number> }

/**
 * Whether -- and how precisely -- this basin's optimization staging
 * (coarse pass refined by a fine one) can be described from what the wire
 * actually states. See the module-level comment above for why this never
 * reads a dependency edge directly.
 */
export function optimizationStaging(conformer: ConformerProjection): OptimizationStaging {
    if (conformer.calculations == null) return { kind: "unknown" }
    const evidence = conformer.evidence_summary
    const chainCount = evidence.optimization_chain_count
    const rawOptCount = calculationTypeCounts(conformer).find((entry) => entry.type === "opt")?.count ?? 0
    const stagedRowCount = Math.max(0, rawOptCount - chainCount)
    const coverageOpt = evidence.evidence_coverage.opt
    const observations = conformer.observations
    const everyObservationLoaded = !!observations && observations.length > 0
        && observations.every((observation) => observation.calculations != null)
    // The safety condition: exactly one chain per observation that has one.
    // If the group had MORE chains than observations-with-opt, at least one
    // observation would be hiding two INDEPENDENT chains behind its raw row
    // count -- and this function has no way to tell that apart from one
    // observation staged twice using counts alone.
    if (chainCount !== coverageOpt || !everyObservationLoaded) {
        return { kind: "aggregate", rawOptCount, chainCount, stagedRowCount }
    }
    const perObservation = new Map<string, number>()
    for (const observation of observations as NonNullable<ConformerProjection["observations"]>) {
        const optCount = (observation.calculations ?? []).filter((calculation) => calculation.type === "opt").length
        if (optCount > 0) perObservation.set(observation.conformer_observation.conformer_observation_ref, optCount)
    }
    return { kind: "per-observation", rawOptCount, chainCount, stagedRowCount, perObservation }
}

// Spelled out zero through nine; digits from ten up -- a single, fixed
// threshold that never falls BETWEEN two numbers compared in the same
// sentence. The prior array ran to "ten" (n < 11), which put nine and ten
// on the word side and eleven on the digit side -- so a real archive
// fixture (11 sightings, 10 with a stage) rendered "Ten of the 11
// sightings", spelled word beside bare digit for two numbers a reader is
// meant to read as the same kind of count. Any threshold has SOME boundary,
// but only this one keeps both numbers in a comparison past ten uniformly
// on the digit side.
const NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]

function numberWord(n: number): string {
    return n >= 0 && n < NUMBER_WORDS.length ? NUMBER_WORDS[n] : String(n)
}

function pluralize(n: number, singular: string, plural: string = `${singular}s`): string {
    return n === 1 ? singular : plural
}

function capitalize(sentence: string): string {
    return sentence.length === 0 ? sentence : sentence[0].toUpperCase() + sentence.slice(1)
}

function describeStaging(staging: OptimizationStaging, sightings: number): string | null {
    if (staging.kind === "unknown") return null
    if (staging.kind === "aggregate") {
        const { rawOptCount, chainCount, stagedRowCount } = staging
        if (rawOptCount === 0) return "None of them have an optimisation calculation on file."
        if (stagedRowCount === 0) {
            return `${numberWord(rawOptCount)} optimisation ${pluralize(rawOptCount, "calculation")}`
                + ` ${pluralize(rawOptCount, "is", "are")} on file, one per chain — no chain was staged in more than one pass.`
        }
        return `${numberWord(rawOptCount)} optimisation ${pluralize(rawOptCount, "calculation")} ${pluralize(rawOptCount, "is", "are")} on file across`
            + ` ${numberWord(chainCount)} independent optimisation ${pluralize(chainCount, "chain")} —`
            + ` ${numberWord(stagedRowCount)} of those calculations ${pluralize(stagedRowCount, "is", "are")} a coarse pass later refined within the same`
            + " chain, though the archive does not say which sighting they belong to."
    }
    // Every sighting has zero opt rows -- there is nothing to bucket. Without
    // this, the loop below runs over an empty map, `clauses` comes out
    // empty, and the caller is left joining an empty array into "" and
    // appending a bare "." -- a stray, meaningless sentence fragment
    // ("This conformer was sighted two times. . Every sighting got...").
    // Say the same honest thing the aggregate branch says for the identical
    // fact (no opt evidence at all), rather than silently producing nothing.
    if (staging.perObservation.size === 0) return "None of them have an optimisation calculation on file."
    const buckets = new Map<number, number>()
    for (const stageCount of staging.perObservation.values()) {
        buckets.set(stageCount, (buckets.get(stageCount) ?? 0) + 1)
    }
    // Staged (more than one pass) chains lead, single-pass chains follow --
    // the more informative fact first, mirroring how a reader would explain
    // it themselves.
    const sortedCounts = [...buckets.keys()].sort((a, b) => b - a)
    const clauses = sortedCounts.map((stageCount, index) => {
        const obsCount = buckets.get(stageCount) as number
        // At exactly two rows, "two stages" and "a coarse pass then a fine
        // one" are the same fact -- with only two rows in one chain there is
        // only one possible relationship between them (one refines the
        // other). From THREE rows up that stops being true: the backend's
        // `_feeds_a_refinement_on_the_same_observation`
        // (backend/app/services/scientific_read/conformers.py:645-666)
        // collapses on ANY `optimized_from` parent without constraining the
        // child, so three opt rows folded into one chain is byte-identical
        // on the wire whether they are a genuine coarse->medium->fine
        // sequence OR two independent coarse attempts (A->C, B->C) both
        // refined into the same final geometry -- and per that function's
        // own docstring, the deployed database has no chain longer than two
        // nodes today, which makes the parallel reading the MORE probable
        // one for any 3-row/1-chain observation, not a rare edge case. Say
        // only what the count licenses: N rows belonging to one chain, never
        // a step count or a sequence, once stageCount passes two.
        const stageDesc = stageCount === 1
            ? "a single pass"
            : stageCount === 2
                ? "two stages"
                : `${numberWord(stageCount)} calculations belonging to a single chain`
        if (index === 0) return `${numberWord(obsCount)} ${pluralize(obsCount, "was", "were")} optimised in ${stageDesc}`
        return `${numberWord(obsCount)} in ${stageDesc}`
    })
    // Sightings whose own calculation list is loaded but carries no opt row
    // at all never get an entry in `perObservation` (it's only populated
    // when `optCount > 0`) -- without naming them, a reader has no way to
    // tell "this sighting has no opt evidence" apart from "this sighting's
    // opt evidence just wasn't worth mentioning", and the freq/sp sentences
    // right beside this one DO carry a denominator over all sightings, so a
    // silently short count here reads as a complete accounting when it
    // is not.
    const withoutOpt = sightings - staging.perObservation.size
    let sentence = withoutOpt > 0
        ? `${clauses.join(", ")}, and ${numberWord(withoutOpt)} had no optimisation on file.`
        : `${clauses.join(", ")}.`
    // The sequential gloss asserts a specific two-step relationship (coarse
    // pass, then a refinement of it) that the wire only actually
    // distinguishes from a parallel-attempts shape at exactly two rows --
    // see the stageDesc comment above. Suppress it the instant any bucket
    // has three or more rows, so it is never attached to a sentence that
    // also, in its own next clause, admits it cannot tell a sequence from
    // parallel attempts.
    if (staging.stagedRowCount > 0 && sortedCounts[0] <= 2) {
        sentence += " A staged optimisation runs a coarse pass first, then refines it."
    }
    return sentence
}

function coverageSentence(count: number, total: number, thing: string): string {
    if (count === total) return `Every sighting got ${thing}.`
    if (count === 0) return `None of the sightings got ${thing}.`
    return `${numberWord(count)} of the ${numberWord(total)} sightings got ${thing}.`
}

/**
 * The story behind a conformer's evidence figures, in prose: how many times
 * it was sighted, whether (and how many of) those sightings were optimized
 * in more than one pass, and how many got a frequency calculation / a
 * single-point energy. This is the answer to "is the 7 opt also including
 * the pre-opt?" -- it says so directly, deriving only what
 * `optimizationStaging` can prove from the wire (see its module comment)
 * rather than inferring a staging relationship the wire never states.
 */
export function describeConformerEvidence(conformer: ConformerProjection): string {
    const sightings = conformer.observations_summary.total
    if (sightings === 0) return "No observations are deposited for this conformer yet."
    const coverage = conformer.evidence_summary.evidence_coverage
    const lead = sightings === 1
        ? "This conformer was sighted once."
        : sightings === 2
            ? "This conformer was sighted twice."
            : `This conformer was sighted ${numberWord(sightings)} times.`
    const stagingSentence = describeStaging(optimizationStaging(conformer), sightings)
    const freqSentence = coverageSentence(coverage.freq, sightings, "a frequency calculation")
    const spSentence = coverageSentence(coverage.sp, sightings, "a single-point energy")
    return [lead, stagingSentence, freqSentence, spSentence]
        .filter((part): part is string => !!part)
        .map(capitalize)
        .join(" ")
}

export type GeometryConvergenceEntry = { geometryRef: string; calculationCount: number }

/**
 * How many calculation outputs converge on each distinct stored geometry
 * this conformer's evidence links to -- e.g. one geometry produced by 4
 * calculation outputs, another by 3. Derived from the conformer's own
 * `geometries` link list (never from a pre-aggregated count, since the
 * per-geometry breakdown has no other source on the wire).
 */
export function geometryConvergence(conformer: ConformerProjection): GeometryConvergenceEntry[] {
    const counts = new Map<string, number>()
    for (const link of conformer.geometries ?? []) {
        counts.set(link.geometry.geometry_ref, (counts.get(link.geometry.geometry_ref) ?? 0) + 1)
    }
    return [...counts.entries()].map(([geometryRef, calculationCount]) => ({ geometryRef, calculationCount }))
}

export type ConformerAttribution<T> = {
    thisConformer: T[]
    /** Each entry is a DIFFERENT conformer the wire actually names — never merged into one generic "other" bucket. */
    otherConformers: Array<{ ref: string; label: string; records: T[] }>
    noLink: T[]
}

/**
 * Three-way split by a record's own conformer-group link(s), never a guess
 * and never a first-match: the group actually selected, any OTHER group(s)
 * the wire names (kept separate and labeled — a record IS linked, just not
 * ONLY to what's selected), and records with no link at all. A binary
 * matched/unmatched split cannot express the middle case honestly — it must
 * either invent a match to the wrong conformer or claim "no link" about a
 * record that has one.
 *
 * `linkedGroupRefs` returns EVERY group ref a record names, not one — a
 * record can legitimately trace to more than one basin (an ensemble-level
 * statmech treatment spanning several conformers is the real, on-the-wire
 * case: `include=conformers` on a single statmech record returned three
 * refs, live, on `spe_mbdqifmaclaakukr7agxbuq3wa`). Membership, not
 * equality, decides `thisConformer`: a record naming the selected ref
 * anywhere in its list belongs there regardless of what else it names. A
 * record that does NOT name the selected ref but names several others is
 * filed under EVERY one of those — never collapsed onto just the first,
 * which was the exact defect here (`statmechConformerGroupRef` used to read
 * only `conformerContext[0]`, so a record naming
 * `[conformer_1, conformer_2, conformer_3]` always filed under
 * "From Conformer Group 1" no matter which conformer was selected).
 *
 * Thermo's real link (`thermoConformerGroupRef`, PR #285) is genuinely
 * single-valued (one primary calculation, one basin) — its call site wraps
 * the single ref in a one-element array rather than this function growing
 * a second, singular code path.
 */
export function partitionByConformerLink<T>(
    records: T[],
    conformers: ConformerProjection[],
    selectedRef: string,
    linkedGroupRefs: (record: T) => string[],
): ConformerAttribution<T> {
    const thisConformer: T[] = []
    const noLink: T[] = []
    const otherByRef = new Map<string, T[]>()
    for (const record of records) {
        const refs = linkedGroupRefs(record)
        if (refs.length === 0) { noLink.push(record); continue }
        if (refs.includes(selectedRef)) { thisConformer.push(record); continue }
        for (const ref of refs) {
            const bucket = otherByRef.get(ref)
            if (bucket) bucket.push(record)
            else otherByRef.set(ref, [record])
        }
    }
    const otherConformers = [...otherByRef.entries()].map(([ref, groupRecords]) => {
        const match = conformers.find((candidate) => candidate.conformer_group.conformer_group_ref === ref)
        // Falls back to the raw ref as its own label if the linked group
        // somehow isn't in the loaded conformer list -- still true of the
        // record (it IS linked to something), never invented.
        return { ref, label: match ? conformerLabel(match) : ref, records: groupRecords }
    })
    return { thisConformer, otherConformers, noLink }
}

export type ThermoConformerLinkLike = {
    provenance?: { conformer_group_ref?: string | null } | null
}

/**
 * Thermo's REAL conformer link (PR #285): `provenance.conformer_group_ref`
 * resolves through the SAME primary calculation already used for
 * `primary_calculation`/`level_of_theory`, sourced server-side via
 * `calculation.conformer_observation_id`. `null` for a record with no
 * resolvable primary calculation (population B) -- reported as "no
 * conformer link", never guessed at from calculation refs. The prior
 * calc-ref-intersection heuristic that lived here was removed because it
 * could not tell that case apart from a record that genuinely does trace
 * to one observation's own opt/freq/sp chain -- see the review that caught
 * it. This field is what makes the distinction real.
 */
export function thermoConformerGroupRef(record: ThermoConformerLinkLike): string | null {
    return record.provenance?.conformer_group_ref ?? null
}

export type ConformerContextLike = Array<{ conformer_group_ref: string }> | null | undefined

/**
 * Statmech's REAL conformer link(s): `include=conformers` on the
 * entry-scoped statmech list returns EVERY conformer_group_ref a record was
 * actually computed against — a first-class field, not a client-side guess,
 * and genuinely multi-valued: an ensemble-level statmech treatment can
 * legitimately span several basins (measured live on
 * `spe_mbdqifmaclaakukr7agxbuq3wa`: one statmech record names all three of
 * that entry's conformer groups). Returns every ref, in the wire's own
 * order — never just the first. An earlier version of this function DID
 * read only `conformerContext[0]`, so a record naming
 * `[conformer_1, conformer_2, conformer_3]` filed under "From Conformer
 * Group 1" under `partitionByConformerLink` no matter which conformer was
 * actually selected; that was the entire bug ("Thermochemistry is bust...
 * it always shows From Conformer Group 1"), not a display-label glitch.
 * `partitionByConformerLink` now does a set-membership test against this
 * function's full list, not an equality test against one item from it.
 */
export function statmechConformerGroupRefs(conformerContext: ConformerContextLike): string[] {
    return (conformerContext ?? []).map((entry) => entry.conformer_group_ref)
}
