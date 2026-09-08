import { useEffect, useRef, useState, type FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"
import {
    ScientificApiError,
    ScientificRateLimitError,
    searchReactionParticipation,
    searchSpeciesExact,
    type ReactionParticipationMatch,
    type SearchMatch,
} from "../api/scientificApi"
import { classifyIdentifier, resultPath, type IdentifierClassification } from "../domain/recordModel"
import { chargeDisplay, entryCountDisplay, spinDisplay } from "../domain/chemistryFormat"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { Formula } from "./Formula"
import { ReactionEquation } from "./ReactionEquation"
import { SectionErrorBoundary } from "./SectionErrorBoundary"

/**
 * Only a structure query (SMILES/InChI/InChIKey) has chemistry a reaction
 * can be searched by -- `formula` is deliberately excluded (a reaction has
 * no formula, so a formula query stays species-only), and the ref kinds
 * never reach `runSearch` at all (they navigate directly, see `submit`).
 */
function isStructureQueryKind(kind: string): boolean {
    return kind === "smiles" || kind === "inchi" || kind === "inchi-key"
}

type ReactionParticipationState =
    | { status: "idle" }
    | { status: "ready"; matches: ReactionParticipationMatch[]; total: number; headline: string; querySmiles: string[] }
    | { status: "error"; message: string }

export function IdentifierSearch() {
    const navigate = useNavigate()
    const [query, setQuery] = useState("")
    const [message, setMessage] = useState<string | null>(null)
    const [ambiguousInput, setAmbiguousInput] = useState<string | null>(null)
    const [matches, setMatches] = useState<Awaited<ReturnType<typeof searchSpeciesExact>>>([])
    const [isSearching, setIsSearching] = useState(false)
    const [reactionState, setReactionState] = useState<ReactionParticipationState>({ status: "idle" })
    const activeRequest = useRef<AbortController | null>(null)
    // A second, independent in-flight request -- the reaction-participation
    // lookup is enrichment of an already-successful species result, not
    // part of the species request/response cycle it runs alongside (see
    // `runSearch`: it is fired only AFTER species matches resolve, and its
    // own failure never touches `message`/`matches`). Aborted separately so
    // a stale reaction fetch from a superseded search cannot land after a
    // newer one, the same guard `activeRequest` gives the species request.
    const activeReactionRequest = useRef<AbortController | null>(null)

    useEffect(() => () => {
        activeRequest.current?.abort(); activeRequest.current = null
        activeReactionRequest.current?.abort(); activeReactionRequest.current = null
    }, [])

    function abortActiveRequest() {
        activeRequest.current?.abort()
        activeRequest.current = null
        setIsSearching(false)
        activeReactionRequest.current?.abort()
        activeReactionRequest.current = null
    }

    /**
     * "Reactions involving …", the second group beneath a structure
     * query's species matches. Fired only for a SMILES/InChI/InChIKey
     * query that resolved at least one species (see `runSearch`) -- a
     * formula query never reaches this (a reaction has no formula), and a
     * structure query with zero species matches has no resolved species
     * SMILES to key a reaction lookup on. Queried by the ARCHIVE'S OWN
     * canonical SMILES for the matched species (`match.smiles`), not the
     * raw user-typed spelling -- `searchReactionParticipation`'s own doc
     * explains why (the backend match is literal-string, not RDKit).
     * Failures here never touch the species `message`/`matches` state --
     * a failed enrichment must not blank out a successful primary result.
     */
    async function loadReactionParticipation(
        headline: string,
        speciesMatches: SearchMatch[],
        controller: AbortController,
    ) {
        const querySmiles = [...new Set(speciesMatches.map((match) => match.smiles).filter((value): value is string => Boolean(value)))]
        if (querySmiles.length === 0) { setReactionState({ status: "idle" }); return }
        try {
            const { matches: reactionMatches, total } = await searchReactionParticipation(querySmiles, controller.signal)
            if (activeReactionRequest.current !== controller || controller.signal.aborted) return
            setReactionState({ status: "ready", matches: reactionMatches, total, headline, querySmiles })
        } catch (error) {
            if (activeReactionRequest.current !== controller || controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return
            const reactionMessage = error instanceof ScientificRateLimitError
                ? `The archive is receiving too many requests right now. Wait ${formatWaitSeconds(error.retryAfterSeconds)} and reload the page.`
                : "The archive could not load reactions for this structure. Try again."
            setReactionState({ status: "error", message: reactionMessage })
        }
    }

    async function runSearch(classified: Extract<IdentifierClassification, { valid: true }>) {
        abortActiveRequest()
        const controller = new AbortController()
        activeRequest.current = controller
        setMatches([]); setMessage(null); setAmbiguousInput(null); setIsSearching(true)
        setReactionState({ status: "idle" })
        try {
            const matches = await searchSpeciesExact(classified.identifier, controller.signal)
            if (activeRequest.current !== controller || controller.signal.aborted) return
            if (matches.length === 0) setMessage(`No exact ${classified.label} record was found.`)
            else if (classified.identifier.kind === "species-ref" || classified.identifier.kind === "species-entry-ref") {
                navigate(resultPath(matches[0]))
            } else {
                setMatches(matches)
                if (isStructureQueryKind(classified.identifier.kind)) {
                    const reactionController = new AbortController()
                    activeReactionRequest.current = reactionController
                    const headline = matches[0].formula ?? matches[0].smiles ?? classified.identifier.value
                    void loadReactionParticipation(headline, matches, reactionController)
                }
            }
        } catch (error) {
            if (activeRequest.current !== controller || controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return
            // An unparseable structure query (RDKit rejected the SMILES/InChI)
            // is a DIFFERENT fact from "the archive was searched and holds no
            // such record" -- the former says the input itself is malformed,
            // the latter says the input was understood and came up empty.
            // Collapsing them into one generic message would tell a chemist
            // their syntactically bad SMILES "was not found", which reads as
            // "this molecule is absent from the archive" -- exactly the wrong
            // answer this fix exists to stop giving. The archive's own `code`
            // (`app/api/error_contract.py`) distinguishes the two; see
            // `structure_search.py`'s `invalid_structure_query` raises.
            if (error instanceof ScientificRateLimitError) {
                // Distinct from the generic "could not complete that
                // search" below -- `requestScientificJson` already
                // retried once automatically, and this only fires when
                // the archive was STILL over its anonymous-read budget a
                // `Retry-After` window later. Same plain-language wording
                // as every other rate-limited surface (`RecordStatus`,
                // `SpeciesEntryPage`, `SpeciesOverviewPage`, `BrowsePage`).
                setMessage(`The archive is receiving too many requests right now. Wait ${formatWaitSeconds(error.retryAfterSeconds)} and reload the page.`)
            } else if (error instanceof ScientificApiError && error.code === "invalid_structure_query") {
                setMessage(`"${classified.identifier.value}" could not be parsed as a valid ${classified.label} — check the syntax and try again.`)
            } else {
                setMessage("The archive could not complete that search. Check the identifier and try again.")
            }
        } finally { if (activeRequest.current === controller && !controller.signal.aborted) setIsSearching(false) }
    }

    function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const classified = classifyIdentifier(query)
        abortActiveRequest()
        if (!classified.valid) {
            setMatches([]); setMessage(classified.message); setAmbiguousInput(classified.ambiguousValue ?? null)
            setReactionState({ status: "idle" })
            return
        }
        // A recognised public reference this frontend already routes
        // (`rxn_`/`rxe_`/`tse_`/… -- see `classifyIdentifier`'s
        // `routedPublicRefPrefixes`) navigates straight to its record page,
        // same as clicking a link -- no verifying search call first (unlike
        // `species-ref`/`species-entry-ref`, which still go through
        // `runSearch` below). The destination page's own `RecordStatus`
        // reports "not found" honestly if the ref does not resolve.
        if (classified.identifier.kind === "record-ref") {
            setMatches([]); setMessage(null); setAmbiguousInput(null)
            setReactionState({ status: "idle" })
            navigate(classified.identifier.path)
            return
        }
        void runSearch(classified)
    }

    function chooseAmbiguous(kind: "formula" | "smiles") {
        const value = query.trim()
        if (!ambiguousInput || value !== ambiguousInput) return
        const choice = classifyIdentifier(`${kind}:${value}`)
        if (choice.valid) void runSearch(choice)
    }

    return <form className="identifier-search" onSubmit={submit} noValidate>
        <label htmlFor="identifier">Exact species or reaction identifier</label>
        <div className="search-row">
            <span aria-hidden="true">⌕</span>
            <input id="identifier" value={query} onChange={(event) => {
                setQuery(event.target.value); setMessage(null); setAmbiguousInput(null)
            }}
                placeholder="SMILES, formula, spc_/spe_/rxn_/rxe_/tse_ ref, InChI, or InChIKey" autoComplete="off" />
            <button type="submit" aria-busy={isSearching}>Search</button>
        </div>
        <p className="search-help">Exact only · no common-name or external resolver lookup</p>
        {message && <p className="search-message" role="status">{message}</p>}
        {ambiguousInput && <fieldset className="identifier-choice">
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
        {matches.length > 0 && <section className="search-results" aria-label="Exact search results">
            <h2>Exact matches</h2>
            <ul>{matches.map((match) => {
                const ref = match.entryRef ?? match.speciesRef
                return <li className="search-result" key={ref}>
                    <SectionErrorBoundary fallback={<FallbackRow match={match} />}>
                        <MatchRow match={match} />
                    </SectionErrorBoundary>
                </li>
            })}</ul>
        </section>}
        <ReactionParticipationResults state={reactionState} />
    </form>
}

/**
 * The second, clearly-labelled group beneath a structure query's species
 * matches -- "find reactions where that species participates" (owner
 * brief). Renders only for `reactionState.status === "ready"`, which
 * `runSearch`/`loadReactionParticipation` only ever reach for a SMILES/
 * InChI/InChIKey query that resolved at least one species; a formula query
 * or an unresolved structure query leaves `reactionState` at `"idle"`
 * (rendering nothing here) rather than ever asserting a reaction fact
 * about a species this archive did not find.
 *
 * A genuine zero-reaction result IS rendered (as a stated absence, "No
 * reactions in this archive list … as a participant") -- this archive's
 * honesty rule is that an absence must be SAID, never left implied by
 * silence, the same rule the species-match "No exact … record was found"
 * message already follows one level up.
 */
function ReactionParticipationResults({ state }: { state: ReactionParticipationState }) {
    if (state.status === "idle") return null
    if (state.status === "error") return <p className="search-message reaction-search-message" role="status">{state.message}</p>
    if (state.matches.length === 0) {
        return <p className="search-message reaction-search-message" role="status">
            No reactions in this archive list {state.headline} as a participant.
        </p>
    }
    const hasMore = state.total > state.matches.length
    const browseQuery = state.querySmiles.map((smiles) => `reactant_smiles=${encodeURIComponent(smiles)}`).join("&")
    return <section className="search-results reaction-search-results" aria-label="Reactions found">
        <h2>Reactions involving {state.headline}</h2>
        <ul>{state.matches.map((match) => (
            <li className="search-result" key={match.reactionEntryRef}>
                <SectionErrorBoundary fallback={<ReactionFallbackRow match={match} />}>
                    <ReactionMatchRow match={match} />
                </SectionErrorBoundary>
            </li>
        ))}</ul>
        {hasMore && <p className="search-results-more">
            <Link to={`/reactions?${browseQuery}`}>See all {state.total} reactions involving {state.headline}</Link>
        </p>}
    </section>
}

/**
 * One reaction row. Reuses `ReactionEquation` (not a fork) with
 * `linkParticipants={false}`, the exact opt-out `ReactionBrowseRow`
 * already uses for the identical reason: the row itself is wrapped in ONE
 * `<Link>` to `/reaction-entries/:ref`, and per-participant links would
 * nest `<a>` inside `<a>`.
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
 * The row's headline. Formula, typeset with subscripts, leads when the
 * archive computed one; SMILES always follows it, because two isomers can
 * share a formula and only the structure string tells them apart.
 *
 * `formula` is nullable (#251: it is computed, and can legitimately come
 * back null) and structure-search matches never carry one at all -- see
 * `scientificApi.ts`'s `SearchMatch.formula`. Either way this says so
 * explicitly rather than leaving a blank where the formula would be, and
 * never falls back to the public reference: that fallback is the exact
 * defect this component exists to fix.
 */
function MatchHeadline({ match }: { match: SearchMatch }) {
    if (match.formula) {
        return <span className="search-result-headline">
            <span className="search-result-formula"><Formula value={match.formula} /></span>
            {match.smiles && <>{" "}<span className="search-result-smiles">{match.smiles}</span></>}
        </span>
    }
    if (match.smiles) {
        return <span className="search-result-headline">
            <span className="search-result-smiles search-result-smiles-primary">{match.smiles}</span>
            {" "}
            <span className="search-result-formula-missing">formula not available</span>
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
