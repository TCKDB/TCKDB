import type { FormEvent, KeyboardEvent } from "react"
import { useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import {
    ScientificApiError,
    ScientificRateLimitError,
    searchReactionEquation,
    searchSpeciesExact,
    type ReactionParticipationMatch,
    type SearchMatch,
} from "../api/scientificApi"
import { classifyIdentifier, looksLikeReferenceAttempt, resultPath, type IdentifierClassification } from "../domain/recordModel"
import { classifyReactionQuery, type ReactionQueryClassification } from "../domain/reactionQuery"
import { chargeDisplay, entryCountDisplay, spinDisplay } from "../domain/chemistryFormat"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { Formula, SpeciesFace } from "./Formula"
import { ReactionEquation } from "./ReactionEquation"
import { SectionErrorBoundary } from "./SectionErrorBoundary"

type SearchMode = "species" | "reactions"

/**
 * The reader states intent up front (owner: "it should do species or you
 * click a button or something to switch it to reaction searching") instead
 * of this component guessing from the shape of what was typed -- the
 * defect the whole redesign exists to fix. Every piece of copy below is
 * keyed by mode so the field's label, placeholder, and help text say what
 * THIS mode accepts, not the union of everything the old single field had
 * to explain at once.
 */
const MODES: { value: SearchMode; label: string }[] = [
    { value: "species", label: "Species" },
    { value: "reactions", label: "Reactions" },
]

const FIELD_LABEL: Record<SearchMode, string> = {
    species: "Exact species identifier",
    reactions: "Exact reaction equation",
}

const PLACEHOLDER: Record<SearchMode, string> = {
    species: "SMILES, formula, spc_/spe_ ref, InChI, or InChIKey",
    reactions: "e.g. NN,[H] <> N,[NH2], a structure, or rxn_/rxe_ ref",
}

const HELP: Record<SearchMode, string> = {
    species: "Exact only · no common-name or external resolver lookup",
    reactions: "Exact only · searches both directions · empty is a real result, not an error",
}

/**
 * Only a structure query (SMILES/InChI/InChIKey) has chemistry a reaction
 * can be searched by -- `formula` is deliberately excluded (a reaction has
 * no formula, so a formula query stays species-only), and the ref kinds
 * never reach `runSpeciesSearch` at all (they navigate directly, see
 * `runReferenceLookup`).
 */
function isStructureQueryKind(kind: string): boolean {
    return kind === "smiles" || kind === "inchi" || kind === "inchi-key"
}

type ReactionQuerySuccess = Extract<ReactionQueryClassification, { valid: true }>

/**
 * The "Reactions matching …" heading and the "no match" honesty message
 * both need to describe, in words, what was actually searched -- built
 * once here so the two can never quietly disagree about what the query
 * meant. The equation glyph is always "⇌" (never "→") regardless of which
 * arrow the reader typed -- every search runs `direction=either` now (see
 * `searchReactionEquation`'s own doc comment), so "⇌" is the honest
 * description of what this heading is ABOUT to search, not an echo of the
 * input syntax; which individual rows matched forward versus in reverse
 * is stated per-row instead (`ReactionMatchRow`'s `matchedDirection` note).
 *
 * A one-sided equation (`classifyReactionQuery`'s own comment on `<>
 * [NH2]`/`[NH2] <>`) leaves `reactants`/`products` empty on the unwritten
 * side -- `.trim()` here drops only the stray edge space that side's own
 * empty `join` would otherwise leave next to the arrow ("⇌ [NH2]", never
 * " ⇌ [NH2]"), not a second sentence explaining why. No copy here says the
 * empty side does not narrow the search: `ReactionMatchRow`'s per-row
 * `matchedDirection` label already carries that truth, honestly, once per
 * row that needs it -- adding it here too would be the same explaining-
 * instead-of-removing mistake the arrow-direction copy already was.
 */
function describeReactionQuery(query: ReactionQuerySuccess): string {
    if (query.kind === "participation") {
        return query.smiles.length > 1 ? `${query.smiles.join(" and ")} together` : query.smiles[0]
    }
    return `${query.reactants.join(" + ")} ⇌ ${query.products.join(" + ")}`.trim()
}

/**
 * Honesty rule (species search already follows it): an absence is a real
 * result and must be SAID, not left implied by rendering nothing. The
 * backend's own all-or-nothing structure match (`searchReactionEquation`'s
 * own doc comment) means an unmatched structure empties the result rather
 * than erroring -- this says so in the message itself, not just in help
 * text a reader may not have read.
 */
function reactionEmptyMessage(query: ReactionQuerySuccess): string {
    if (query.kind === "participation") {
        return query.smiles.length > 1
            ? `No reaction in this archive lists ${query.smiles.join(" and ")} together on one side.`
            : `No reaction in this archive lists ${query.smiles[0]} as a participant.`
    }
    return `No reaction in this archive matches ${describeReactionQuery(query)}.`
}

/**
 * The "See all N" link-through target -- built with the SAME param names
 * (`reactant_smiles`/`product_smiles`/`direction`) `BrowsePage` reads on
 * mount (`seedFiltersFromUrl`, `api/browseApi.ts`), so the landing page
 * shows the identical query the count above just promised, never a
 * narrower one. `direction=either` always, matching `searchReactionEquation`
 * -- there is no other direction this search ever runs any more.
 */
function reactionSeeAllHref(query: ReactionQuerySuccess): string {
    const params = new URLSearchParams()
    if (query.kind === "participation") {
        for (const smiles of query.smiles) params.append("reactant_smiles", smiles)
    } else {
        for (const smiles of query.reactants) params.append("reactant_smiles", smiles)
        for (const smiles of query.products) params.append("product_smiles", smiles)
    }
    params.set("direction", "either")
    return `/reactions?${params}`
}

type ReactionResultState = { matches: ReactionParticipationMatch[]; total: number; query: ReactionQuerySuccess }

/** The species-mode "search reactions involving this too" cross-link -- see the file-level comment on `CrossLinkToReactions` for what this replaces and why. */
type CrossLink = { smiles: string[]; headline: string }

export function IdentifierSearch() {
    const navigate = useNavigate()
    const [mode, setMode] = useState<SearchMode>("species")
    const [query, setQuery] = useState("")
    const [message, setMessage] = useState<string | null>(null)
    const [ambiguousInput, setAmbiguousInput] = useState<string | null>(null)
    const [matches, setMatches] = useState<SearchMatch[]>([])
    const [crossLink, setCrossLink] = useState<CrossLink | null>(null)
    const [reactionResult, setReactionResult] = useState<ReactionResultState | null>(null)
    const [isSearching, setIsSearching] = useState(false)
    const activeRequest = useRef<AbortController | null>(null)

    useEffect(() => () => { activeRequest.current?.abort(); activeRequest.current = null }, [])

    function abortActiveRequest() {
        activeRequest.current?.abort()
        activeRequest.current = null
        setIsSearching(false)
    }

    function clearResults() {
        setMatches([]); setCrossLink(null); setReactionResult(null); setMessage(null)
    }

    /**
     * Switching mode is the reader RE-STATING intent, not a hint this
     * component tries to keep running with -- any results or message on
     * screen belonged to the mode that produced them, so they clear
     * immediately rather than lingering, mislabeled, under the new mode's
     * (now stale) field label. The typed query text itself is left alone:
     * a reader who typed a structure meaning to search species and
     * realizes they wanted reactions should not have to retype it.
     */
    function selectMode(next: SearchMode) {
        if (next === mode) return
        abortActiveRequest()
        setMode(next)
        clearResults()
        setAmbiguousInput(null)
    }

    async function runSpeciesSearch(classified: Extract<IdentifierClassification, { valid: true }>) {
        abortActiveRequest()
        const controller = new AbortController()
        activeRequest.current = controller
        clearResults(); setIsSearching(true)
        try {
            const found = await searchSpeciesExact(classified.identifier, controller.signal)
            if (activeRequest.current !== controller || controller.signal.aborted) return
            if (found.length === 0) {
                setMessage(`No exact ${classified.label} record was found.`)
            } else if (classified.identifier.kind === "species-ref" || classified.identifier.kind === "species-entry-ref") {
                navigate(resultPath(found[0]))
            } else {
                setMatches(found)
                if (isStructureQueryKind(classified.identifier.kind)) {
                    const smilesValues = [...new Set(found.map((match) => match.smiles).filter((value): value is string => Boolean(value)))]
                    if (smilesValues.length > 0) {
                        // SMILES leads (owner ruling), same order as `MatchHeadline`
                        // below: this names the ONE structure that was searched
                        // for ("Also search reactions involving …"), and a bare
                        // formula is still not the honest identity fact on its
                        // own, even here, wherever a species is presented.
                        setCrossLink({ smiles: smilesValues, headline: found[0].smiles ?? found[0].formula ?? classified.identifier.value })
                    }
                }
            }
        } catch (error) {
            if (activeRequest.current !== controller || controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return
            // An unparseable structure query (RDKit rejected the SMILES/InChI)
            // is a DIFFERENT fact from "the archive was searched and holds no
            // such record" -- the former says the input itself is malformed,
            // the latter says the input was understood and came up empty.
            if (error instanceof ScientificRateLimitError) {
                setMessage(`The archive is receiving too many requests right now. Wait ${formatWaitSeconds(error.retryAfterSeconds)} and reload the page.`)
            } else if (error instanceof ScientificApiError && error.code === "invalid_structure_query") {
                setMessage(`"${classified.identifier.value}" could not be parsed as a valid ${classified.label} — check the syntax and try again.`)
            } else {
                setMessage("The archive could not complete that search. Check the identifier and try again.")
            }
        } finally { if (activeRequest.current === controller && !controller.signal.aborted) setIsSearching(false) }
    }

    async function runReactionSearch(classified: ReactionQuerySuccess) {
        abortActiveRequest()
        const controller = new AbortController()
        activeRequest.current = controller
        clearResults(); setIsSearching(true)
        try {
            const { reactants, products } = classified.kind === "equation"
                ? classified
                : { reactants: classified.smiles, products: [] as string[] }
            const { matches: found, total } = await searchReactionEquation({ reactants, products }, controller.signal)
            if (activeRequest.current !== controller || controller.signal.aborted) return
            if (found.length === 0) setMessage(reactionEmptyMessage(classified))
            else setReactionResult({ matches: found, total, query: classified })
        } catch (error) {
            if (activeRequest.current !== controller || controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return
            if (error instanceof ScientificRateLimitError) {
                setMessage(`The archive is receiving too many requests right now. Wait ${formatWaitSeconds(error.retryAfterSeconds)} and reload the page.`)
            } else {
                setMessage("The archive could not complete that search. Check the equation and try again.")
            }
        } finally { if (activeRequest.current === controller && !controller.signal.aborted) setIsSearching(false) }
    }

    /**
     * A recognised public reference (`rxn_`/`rxe_`/`spc_`/… -- anything
     * `looksLikeReferenceAttempt` flags as reference-SHAPED) is unambiguous
     * on its own: no arrow or comma can appear inside its fixed shape, so
     * it means the same thing regardless of which mode is currently
     * selected. This is what makes a `rxe_…` ref pasted while "Species" is
     * selected still route to the reaction-entry page instead of being fed
     * to the species/formula grammar and reported invalid -- the exact
     * defect fixed earlier for the single-field search, now preserved
     * across BOTH directions of the new mode switch.
     */
    async function runReferenceLookup(trimmed: string) {
        const classified = classifyIdentifier(trimmed)
        clearResults(); setAmbiguousInput(null)
        if (!classified.valid) { setMessage(classified.message); return }
        if (classified.identifier.kind === "record-ref") {
            navigate(classified.identifier.path)
            return
        }
        void runSpeciesSearch(classified)
    }

    function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const trimmed = query.trim()
        abortActiveRequest()
        setAmbiguousInput(null)

        if (looksLikeReferenceAttempt(trimmed)) { void runReferenceLookup(trimmed); return }

        if (mode === "species") {
            const classified = classifyIdentifier(trimmed)
            if (!classified.valid) {
                clearResults(); setMessage(classified.message); setAmbiguousInput(classified.ambiguousValue ?? null)
                return
            }
            void runSpeciesSearch(classified)
            return
        }

        const classified = classifyReactionQuery(trimmed)
        if (!classified.valid) { clearResults(); setMessage(classified.message); return }
        void runReactionSearch(classified)
    }

    function chooseAmbiguous(kind: "formula" | "smiles") {
        const value = query.trim()
        if (!ambiguousInput || value !== ambiguousInput) return
        const choice = classifyIdentifier(`${kind}:${value}`)
        if (choice.valid) void runSpeciesSearch(choice)
    }

    /** The species-mode cross-link into reaction mode -- see `CrossLinkToReactions`'s own comment. */
    function followCrossLink() {
        if (!crossLink) return
        const joined = crossLink.smiles.join(",")
        const classified = classifyReactionQuery(joined)
        abortActiveRequest()
        setMode("reactions")
        setQuery(joined)
        clearResults()
        if (!classified.valid) { setMessage(classified.message); return }
        void runReactionSearch(classified)
    }

    return <form className="identifier-search" onSubmit={submit} noValidate>
        <SearchModeToggle mode={mode} onSelect={selectMode} />
        <label htmlFor="identifier">{FIELD_LABEL[mode]}</label>
        <div className="search-row">
            <span aria-hidden="true">⌕</span>
            <input id="identifier" value={query} onChange={(event) => {
                setQuery(event.target.value); setMessage(null); setAmbiguousInput(null)
            }}
                placeholder={PLACEHOLDER[mode]} autoComplete="off" aria-describedby="identifier-search-help" />
            <button type="submit" aria-busy={isSearching}>Search</button>
        </div>
        <p className="search-help" id="identifier-search-help">{HELP[mode]}</p>
        {message && <p className="search-message" role="status">{message}</p>}
        {mode === "species" && ambiguousInput && <fieldset className="identifier-choice">
            <legend>Search “{ambiguousInput}” as</legend>
            {/* SMILES leads: a structure string is the identifier a chemist
                reaches for first, and this archive is searched by structure far
                more often than by formula. Order is presentation only -- an
                ambiguous value is still ASKED about rather than resolved to
                either kind, because guessing would silently search for the
                wrong thing. */}
            <button type="button" onClick={() => chooseAmbiguous("smiles")}>SMILES</button>
            <button type="button" onClick={() => chooseAmbiguous("formula")}>Formula</button>
        </fieldset>}
        {mode === "species" && matches.length > 0 && <section className="search-results" aria-label="Exact search results">
            <h2>Exact matches</h2>
            <ul>{matches.map((match) => {
                const ref = match.entryRef ?? match.speciesRef
                return <li className="search-result" key={ref}>
                    <SectionErrorBoundary fallback={<FallbackRow match={match} />}>
                        <MatchRow match={match} />
                    </SectionErrorBoundary>
                </li>
            })}</ul>
            {crossLink && <CrossLinkToReactions headline={crossLink.headline} onFollow={followCrossLink} />}
        </section>}
        {mode === "reactions" && reactionResult && <ReactionResultsSection result={reactionResult} />}
    </form>
}

/**
 * Two-way choice, rendered like `ThemeToggle`'s Light/Dark/System pill
 * (`components/ThemeToggle.tsx`) -- the site's own established idiom for
 * "exactly one of a small fixed set is always the active choice", reused
 * here rather than a THIRD idiom invented for this one control (the browse
 * filters and the chart's axis controls both reach for a `<select>` for a
 * many-option facet; a two-way MODE switch that changes what the whole
 * field means is closer to what the theme toggle already is than to a
 * filter facet).
 *
 * A real `role="radiogroup"` of `role="radio"` buttons, not a div with
 * click handlers -- exactly one of the two is ever "the" mode (never none,
 * never both), which is what a mode switch IS. Arrow keys move focus AND
 * selection together (the radiogroup convention: there is nothing to
 * preview separately from selecting, unlike a tablist), with a roving
 * `tabIndex` so Tab reaches the group once, not twice.
 */
function SearchModeToggle({ mode, onSelect }: { mode: SearchMode; onSelect: (mode: SearchMode) => void }) {
    const optionRefs = useRef<Partial<Record<SearchMode, HTMLButtonElement | null>>>({})

    function focusMode(value: SearchMode) {
        optionRefs.current[value]?.focus()
    }

    function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
            event.preventDefault()
            const next = MODES[(index + 1) % MODES.length].value
            focusMode(next); onSelect(next)
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
            event.preventDefault()
            const next = MODES[(index - 1 + MODES.length) % MODES.length].value
            focusMode(next); onSelect(next)
        }
    }

    return (
        <div className="identifier-search-mode" role="radiogroup" aria-label="Search kind">
            {MODES.map(({ value, label }, index) => (
                <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={mode === value}
                    tabIndex={mode === value ? 0 : -1}
                    className="identifier-search-mode-option"
                    data-active={mode === value}
                    ref={(node) => { optionRefs.current[value] = node }}
                    onClick={() => onSelect(value)}
                    onKeyDown={(event) => handleKeyDown(event, index)}
                >
                    {label}
                </button>
            ))}
        </div>
    )
}

/**
 * What replaced the old "Reactions involving …" group that used to render
 * unconditionally beneath a structure query's species matches (owner
 * brief: that automatic secondary fetch is the exact kind of INFERRED
 * intent this redesign exists to remove -- species mode means "search
 * species", full stop). A reader who typed a structure and got species
 * matches back is one click from continuing into reaction mode with the
 * SAME structure, pre-filled -- discoverable, but never fetched until
 * asked for. Rendered only when `crossLink` is set, which
 * `runSpeciesSearch` only ever does for a SMILES/InChI/InChIKey query that
 * resolved at least one species match (a formula query never gets one --
 * a reaction has no formula).
 */
function CrossLinkToReactions({ headline, onFollow }: { headline: string; onFollow: () => void }) {
    return <p className="search-cross-link">
        <button type="button" className="search-cross-link-button" onClick={onFollow}>
            Also search reactions involving {headline} →
        </button>
    </p>
}

/**
 * Reaction mode's own primary results group -- what "Reactions involving
 * …" became now that reaction mode is an explicit, asked-for search rather
 * than enrichment tacked onto a species result. Renders only when
 * `reactionResult` holds at least one match; a genuine zero-match search
 * is a stated `message` instead (`reactionEmptyMessage`), same honesty
 * rule the species side already follows for "No exact … record was found."
 */
function ReactionResultsSection({ result }: { result: ReactionResultState }) {
    const { matches, total, query } = result
    const hasMore = total > matches.length
    return <section className="search-results reaction-search-results" aria-label="Reactions found">
        <h2>Reactions matching {describeReactionQuery(query)}</h2>
        <ul>{matches.map((match) => (
            <li className="search-result" key={match.reactionEntryRef}>
                <SectionErrorBoundary fallback={<ReactionFallbackRow match={match} />}>
                    <ReactionMatchRow match={match} />
                </SectionErrorBoundary>
            </li>
        ))}</ul>
        {hasMore && <p className="search-results-more">
            <Link to={reactionSeeAllHref(query)}>See all {total} reactions</Link>
        </p>}
    </section>
}

/**
 * One reaction row. Reuses `ReactionEquation` (not a fork) with
 * `linkParticipants={false}`, the exact opt-out `ReactionBrowseRow`
 * already uses for the identical reason: the row itself is wrapped in ONE
 * `<Link>` to `/reaction-entries/:ref`, and per-participant links would
 * nest `<a>` inside `<a>`.
 *
 * `matchedDirection === "reverse"` renders "Matched on the reverse
 * direction" -- the SAME wording, the SAME condition, and the SAME
 * `.browse-row-evidence`-equivalent note styling `ReactionBrowseRow.tsx`
 * already uses on `/reactions` for the identical fact, reused rather than
 * re-invented so a reader sees one consistent phrase for "this row only
 * matched because the archive also checked the reverse orientation"
 * wherever they encounter it. This is now the ONLY place that fact is
 * surfaced -- every search here runs `direction=either` regardless of
 * which arrow (if any) was typed, so there is no separate "forward-only"
 * mode whose absence would need explaining; a reverse match is simply
 * labelled, not hidden behind syntax the reader would have had to already
 * know to type differently.
 */
function ReactionMatchRow({ match }: { match: ReactionParticipationMatch }) {
    return <>
        <Link className="search-result-link" to={`/reaction-entries/${match.reactionEntryRef}`}>
            <span className="search-result-headline">
                <ReactionEquation
                    reactants={match.reactants}
                    products={match.products}
                    reversible={match.reversible}
                    linkParticipants={false}
                />
            </span>
        </Link>
        {match.matchedDirection === "reverse" && (
            <span className="search-result-evidence">Matched on the reverse direction</span>
        )}
        <code className="search-result-ref">{match.reactionEntryRef}</code>
    </>
}

function ReactionFallbackRow({ match }: { match: ReactionParticipationMatch }) {
    return <Link className="search-result-link" to={`/reaction-entries/${match.reactionEntryRef}`}>{match.reactionEntryRef}</Link>
}

/**
 * A chemist scanning this list must be able to tell two matches apart by
 * their chemistry -- formula and SMILES carry the row, charge/spin/entry
 * count give context -- without decoding a base32 public reference. The
 * reference stays on the row (visible, selectable, in monospace) but
 * demoted below the chemistry, never standing in for it. See
 * `docs/plans/provenance-first-website.md`'s "stable public references
 * remain visible and copyable" and the sibling defect this project already
 * caught in the opposite direction: a label-or-ref fallback where a
 * present label made a real ref appear nowhere on the page. Neither
 * failure is acceptable, so both stay checked by
 * `IdentifierSearch.test.tsx`.
 */
function MatchRow({ match }: { match: SearchMatch }) {
    const ref = match.entryRef ?? match.speciesRef
    const context = [chargeContext(match), spinContext(match)]
    if (match.entryCount !== undefined) context.push(entryCountDisplay(match.entryCount))
    return <>
        <Link className="search-result-link" to={resultPath(match)}>
            <MatchHeadline match={match} />
            {" "}
            <span className="search-result-context">{context.join(" · ")}</span>
        </Link>
        <code className="search-result-ref">{ref}</code>
    </>
}

function chargeContext(match: SearchMatch) {
    return `charge ${chargeDisplay(match.charge)}`
}

function spinContext(match: SearchMatch) {
    return `spin ${spinDisplay(match.multiplicity)}`
}

/**
 * The row's headline. SMILES leads (owner ruling: SMILES leads, formula
 * follows in parentheses), typeset in `code.data` through `SpeciesFace`
 * (`./Formula.tsx`); the formula -- still typeset with subscripts -- is
 * the parenthesised follower, because two isomers can share a formula and
 * only the structure string tells them apart.
 *
 * `formula` is nullable (#251: it is computed, and can legitimately come
 * back null) and structure-search matches never carry one at all -- see
 * `scientificApi.ts`'s `SearchMatch.formula`. Either way this says so
 * explicitly ("formula not available") rather than leaving a blank where
 * the formula would be. Symmetrically, `smiles` is also nullable/optional
 * on the wire; a formula/ref match that somehow carries no SMILES falls
 * back to the bare formula with its own explicit "SMILES not available"
 * note, rather than silently promoting the formula to the primary spot
 * with no explanation for why SMILES is missing. Neither case falls back
 * to the public reference: that fallback is the exact defect this
 * component exists to fix.
 */
function MatchHeadline({ match }: { match: SearchMatch }) {
    if (match.smiles) {
        return <span className="search-result-headline">
            <SpeciesFace smiles={match.smiles} formula={match.formula} />
            {!match.formula && <>{" "}<span className="search-result-formula-missing">formula not available</span></>}
        </span>
    }
    if (match.formula) {
        return <span className="search-result-headline">
            <span className="search-result-formula"><Formula value={match.formula} /></span>
            {" "}
            <span className="search-result-smiles-missing">SMILES not available</span>
        </span>
    }
    // Neither a formula nor a SMILES is known for this match -- there is
    // nothing chemistry-shaped left to lead with. Showing the reference
    // here is honest (there genuinely is no chemistry data), which is a
    // different case from the defect this component fixes: that defect was
    // showing the reference *instead of* chemistry that existed.
    return <span className="search-result-headline search-result-headline-fallback">{match.entryRef ?? match.speciesRef}</span>
}

function FallbackRow({ match }: { match: SearchMatch }) {
    return <Link className="search-result-link" to={resultPath(match)}>{match.entryRef ?? match.speciesRef}</Link>
}

