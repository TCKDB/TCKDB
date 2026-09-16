import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import ReviewQueuePage from "./ReviewQueuePage"
import { AuthProvider } from "../components/AuthProvider"

/**
 * Record review is the one surface in this app where a click changes
 * what readers are told to trust. These tests pin:
 *
 * 1. Who gets in (curator or admin; a plain signed-in user is told why).
 * 2. That the page says which review axis it is, since "review" alone
 *    does not distinguish it from Machine findings.
 * 3. Task #269's redesign: records group under the SUBJECT a curator
 *    actually judges (a species entry, a transition-state entry, ...),
 *    a same-type cluster collapses to a count and still expands to reach
 *    every record, and the header reports honest totals over the WHOLE
 *    filtered backlog rather than admitting it cannot say how big the
 *    page is.
 * 4. That only transitions the backend allows are offered, and that a
 *    refusal is believed over the page's own copy of the policy.
 * 5. That everything a row owns stays with that row, whether it sits
 *    alone or inside an expanded group.
 *
 * This file replaces the flat-table version wholesale rather than
 * patching it: the wire shape changed from a bare array to
 * `{ subjects, subject_total, record_total, ... }`, and the markup is no
 * longer a `<table>` -- there is nothing left in the old suite's
 * `rowFor`/`rowButton` helpers to adapt. Tests of behaviour the redesign
 * REMOVES (the "cannot be named" wording, "shown on species entry ..."
 * container text, the "a full page -- there may be more" hedge, and a
 * per-row "unreadable" count) are dropped rather than ported: that
 * wording no longer exists anywhere on this page, and its absence is
 * itself pinned below ("the redesign actually removed the old
 * complaints").
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => {
    server.resetHandlers()
    cleanup()
})
afterAll(() => server.close())

const QUEUE = "/api/v1/record-reviews/queue"
const PATCH_BASE = "/api/v1/record-reviews"

const curator = {
    id: 1,
    username: "calvin",
    email: "calvin@example.com",
    full_name: "Calvin Pieters",
    role: "curator",
    is_active: true,
}
const admin = { ...curator, id: 2, username: "root", role: "admin" }
const plainUser = { ...curator, id: 3, username: "noah", role: "user" }

function meIs(who: Record<string, unknown>) {
    server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(who)))
}

function chemistry(over: Record<string, unknown> = {}) {
    return {
        formula: null,
        multiplicity: null,
        species_entry_kind: null,
        electronic_state_kind: null,
        electronic_state_label: null,
        term_symbol: null,
        stereo_label: null,
        isotope_key: null,
        unmapped_smiles: null,
        ...over,
    }
}

/** A reaction_entry subject's own equation, in the shape `/queue` serves. */
function reactionEquation(over: Record<string, unknown> = {}) {
    return {
        reversible: true,
        reactants: [],
        products: [],
        ...over,
    }
}

function reactionParticipant(over: Record<string, unknown> = {}) {
    return {
        species_entry_ref: "spe_participant",
        species_entry_label: null,
        smiles: "C",
        formula: "CH4",
        stoichiometry: 1,
        participant_index: 1,
        ...over,
    }
}

/** One review row, shaped like `RecordReviewRead`, nested inside a subject. */
function record(over: Record<string, unknown> = {}) {
    return {
        id: 11,
        record_type: "thermo",
        record_id: 987654,
        record_public_ref: null,
        container_type: "species_entry",
        container_ref: "spe_h2o",
        status: "not_reviewed",
        submission_id: 7,
        reviewed_by: null,
        reviewed_at: null,
        first_approved_at: null,
        note: null,
        created_at: "2026-09-14T10:00:00Z",
        created_by: 5,
        ...over,
    }
}

/** One subject block: a species entry, by default -- the common case. */
function subject(records: Record<string, unknown>[], over: Record<string, unknown> = {}) {
    return {
        subject_type: "species_entry",
        subject_ref: "spe_h2o",
        chemistry: chemistry({ formula: "H2O", multiplicity: 1, species_entry_kind: "minimum", electronic_state_kind: "ground" }),
        records,
        ...over,
    }
}

function queuePageBody(subjects: Record<string, unknown>[], over: Record<string, unknown> = {}) {
    const recordTotal = subjects.reduce(
        (n, s) => n + (s.records as unknown[]).length,
        0,
    )
    return {
        subjects,
        subject_total: subjects.length,
        record_total: recordTotal,
        offset: 0,
        limit: 50,
        ...over,
    }
}

/** Serve this page for EVERY request, whatever the page asked for. */
function queueIs(subjects: Record<string, unknown>[]): { reads: number } {
    const counter = { reads: 0 }
    server.use(
        http.get(QUEUE, () => {
            counter.reads += 1
            return HttpResponse.json(queuePageBody(subjects))
        }),
    )
    return counter
}

/** Serve subjects the way the server does: filtered by the `status` asked for. */
function queueByStatus(subjectsNow: () => Record<string, unknown>[]) {
    server.use(
        http.get(QUEUE, ({ request }) => {
            const wanted = new URL(request.url).searchParams.get("status")
            const subjects = subjectsNow()
            const filtered =
                wanted === null
                    ? subjects
                    : (subjects
                          .map((s) => ({
                              ...s,
                              records: (s.records as Record<string, unknown>[]).filter(
                                  (r) => r.status === wanted,
                              ),
                          }))
                          .filter((s) => (s.records as unknown[]).length > 0))
            return HttpResponse.json(queuePageBody(filtered))
        }),
    )
}

function renderPage() {
    return render(
        <AuthProvider>
            <MemoryRouter>
                <ReviewQueuePage />
            </MemoryRouter>
        </AuthProvider>,
    )
}

/** The `.review-subject` block whose quiet ref line reads `ref`. */
async function subjectFor(ref: string): Promise<HTMLElement> {
    const node = await screen.findByText(ref)
    const section = node.closest(".review-subject")
    if (section === null) throw new Error(`no subject block for ${ref}`)
    return section as HTMLElement
}

/** The lone (uncollapsed) "Review..." button inside one subject's block. */
function reviewButtonIn(section: HTMLElement): HTMLElement {
    return within(section).getByRole("button", { name: "Review…" })
}

/**
 * A promise the test releases by hand.
 *
 * Holding a request open with `setTimeout` makes the test a race between
 * that delay and however long the rest of the interaction takes, which
 * under a loaded parallel suite is not a race you win reliably. A gate
 * the test opens deliberately removes the timing from the question.
 */
function gate(): { held: Promise<void>; release: () => void } {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
        release = resolve
    })
    return { held, release }
}

const H2O = subject([record()])
const CH4_CALC = subject(
    [record({ id: 12, record_type: "calculation", record_id: 123456, record_public_ref: "calc_ch4_opt" })],
    {
        subject_ref: "spe_ch4",
        chemistry: chemistry({ formula: "CH4", multiplicity: 1 }),
    },
)

describe("who can open record review", () => {
    it("a curator can", async () => {
        meIs(curator)
        queueIs([H2O])
        renderPage()
        expect(await screen.findByText("spe_h2o")).toBeInTheDocument()
    })

    it("an admin can", async () => {
        meIs(admin)
        queueIs([H2O])
        renderPage()
        expect(await screen.findByText("spe_h2o")).toBeInTheDocument()
    })

    it("a signed-in user without the role is told, and the queue is never fetched", async () => {
        meIs(plainUser)
        // No QUEUE handler: `onUnhandledRequest: "error"` makes any
        // request a failure, so this asserts the page does not ask.
        renderPage()

        expect(await screen.findByRole("alert")).toHaveTextContent(/for curators/i)
        expect(screen.queryByText("spe_h2o")).not.toBeInTheDocument()
    })

    it("a backend outage does not render as 'you are not a curator'", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.error()))
        renderPage()

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/could not be reached/i)
        expect(alert).not.toHaveTextContent(/for curators/i)
    })
})

describe("the page says which review this is", () => {
    it("is titled Record review, and never by its old name", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        expect(await screen.findByRole("heading", { name: "Record review", level: 1 })).toBeInTheDocument()
        expect(screen.queryByText(/review queue/i)).not.toBeInTheDocument()
    })

    it("states that approving here changes what readers are told to trust", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        expect(
            await screen.findByText(/changes what every reader is told to trust/i),
        ).toBeInTheDocument()
    })

    it("warns that approving is permanent before anyone clicks it", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        const lede = await screen.findByText(/freezes the record against further edits/i)
        expect(lede).toBeInTheDocument()
        expect(lede).toHaveTextContent(/does not unfreeze it/i)
    })

    it("links to the other queue, so the two are not confused", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        const link = await screen.findByRole("link", { name: "Machine findings" })
        expect(link).toHaveAttribute("href", "/admin/curator-queue")
    })

    it("defaults to the backlog: records nobody has judged", async () => {
        meIs(curator)
        const asked: (string | null)[] = []
        server.use(
            http.get(QUEUE, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("status"))
                return HttpResponse.json(queuePageBody([H2O]))
            }),
        )
        renderPage()
        await screen.findByText("spe_h2o")

        expect(asked).toEqual(["not_reviewed"])
    })
})

describe("what a subject block shows (task #269, defect #4: nothing said what the chemistry was)", () => {
    it("does not print a species entry's own ref twice inside its own block (defect #1)", async () => {
        // Review #492, finding 1 -- the MUST-fix, and the owner's original
        // complaint back on the page: a species deposit gets a
        // species_entry review row, that row is its own subject, and it
        // used to render its OWN ref a second time inside the block whose
        // heading already shows it:
        //
        //   H2O ...   spe_h2o (opens in a new tab)
        //     Thermochemistry              Review...
        //     Species entry  spe_h2o (opens in a new tab)   Review...
        meIs(curator)
        queueIs([
            subject(
                [
                    record({ id: 1, record_type: "thermo", record_id: 101 }),
                    // The species_entry's OWN review row -- same type and
                    // ref as the subject itself.
                    record({
                        id: 2,
                        record_type: "species_entry",
                        record_id: 202,
                        record_public_ref: "spe_h2o",
                        container_type: null,
                        container_ref: null,
                    }),
                ],
                { subject_ref: "spe_h2o" },
            ),
        ])
        renderPage()

        const section = await subjectFor("spe_h2o")
        // The ref appears exactly once: in the block's own ref line.
        expect(within(section).getAllByText("spe_h2o")).toHaveLength(1)
        // The species_entry row itself is still there, still actionable --
        // it is the link that must not repeat, not the row.
        expect(within(section).getByText("Species entry")).toBeInTheDocument()
    })

    it("does not print a transition-state entry's own ref twice inside its own block either", async () => {
        meIs(curator)
        queueIs([
            subject(
                [
                    record({ id: 1, record_type: "statmech", record_id: 101, container_type: "transition_state_entry", container_ref: "tse_dup" }),
                    record({
                        id: 2,
                        record_type: "transition_state_entry",
                        record_id: 202,
                        record_public_ref: "tse_dup",
                        container_type: null,
                        container_ref: null,
                    }),
                ],
                { subject_type: "transition_state_entry", subject_ref: "tse_dup", chemistry: chemistry({ multiplicity: 2 }) },
            ),
        ])
        renderPage()

        const section = await subjectFor("tse_dup")
        expect(within(section).getAllByText("tse_dup")).toHaveLength(1)
    })

    it("shows a species entry's formula, spin word, and disambiguating facets", async () => {
        meIs(curator)
        queueIs([
            subject([record()], {
                subject_ref: "spe_c9h9",
                chemistry: chemistry({
                    formula: "C9H9",
                    multiplicity: 2,
                    species_entry_kind: "minimum",
                    electronic_state_kind: "ground",
                    stereo_label: "R",
                }),
            }),
        ])
        renderPage()

        const section = await screen.findByText("spe_c9h9").then((el) => el.closest(".review-subject") as HTMLElement)
        // The formula's subscript ("9") splits it across elements, so the
        // heading is read as text content rather than matched as one node.
        expect(section.querySelector(".review-subject-heading")?.textContent).toContain("C9H9")
        expect(within(section).getByText("doublet")).toBeInTheDocument()
        expect(within(section).getByText(/R enantiomer/)).toBeInTheDocument()
    })

    it("names a transition-state subject from its OWN unmapped SMILES, not the reaction it sits on", async () => {
        meIs(curator)
        queueIs([
            subject(
                [record({ record_type: "statmech", container_type: "transition_state_entry", container_ref: "tse_1" })],
                {
                    subject_type: "transition_state_entry",
                    subject_ref: "tse_1",
                    chemistry: chemistry({ formula: "CH4", multiplicity: 2 }),
                },
            ),
        ])
        renderPage()

        const section = await screen.findByText("tse_1").then((el) => el.closest(".review-subject") as HTMLElement)
        expect(section.querySelector(".review-subject-heading")?.textContent).toContain("CH4")
        expect(within(section).getByText("doublet")).toBeInTheDocument()
    })

    it("says the formula was never recorded rather than inventing one", async () => {
        meIs(curator)
        queueIs([
            subject([record({ record_type: "statmech", container_type: "transition_state_entry", container_ref: "tse_2" })], {
                subject_type: "transition_state_entry",
                subject_ref: "tse_2",
                chemistry: chemistry({ multiplicity: 3 }),
            }),
        ])
        renderPage()

        const section = await screen.findByText("tse_2").then((el) => el.closest(".review-subject") as HTMLElement)
        expect(within(section).getByText(/no smiles recorded/i)).toBeInTheDocument()
    })

    it("puts a NAME in the heading even with no formula -- the apology is a caveat, never the name itself", async () => {
        // The exact defect the owner called "rubbish" once already
        // ("applied_energy_correction cannot be named"), reappearing in
        // the subject heading: an absence sentence must never occupy the
        // slot where every OTHER subject's name sits.
        meIs(curator)
        queueIs([
            subject([record({ record_type: "statmech", container_type: "transition_state_entry", container_ref: "tse_3" })], {
                subject_type: "transition_state_entry",
                subject_ref: "tse_3",
                chemistry: chemistry({ multiplicity: 2 }),
            }),
        ])
        renderPage()

        const section = await screen.findByText("tse_3").then((el) => el.closest(".review-subject") as HTMLElement)
        const heading = within(section).getByRole("heading", { level: 2 })
        expect(heading).toHaveTextContent(/^Transition state/)
        expect(heading).not.toHaveTextContent(/no smiles recorded/i)
        // The caveat still appears -- just outside the heading.
        expect(within(section).getByText(/no smiles recorded/i)).toBeInTheDocument()
    })

    it("tells 'recorded but no formula could be derived' apart from 'never recorded'", async () => {
        // A reaction-shaped SMILES ("[CH3].[H]>>C") is a real, recorded
        // value that the single-molecule formula parser rejects -- the
        // caveat must say THAT, not the same sentence a genuinely blank
        // field gets, which would simply be false here.
        meIs(curator)
        queueIs([
            subject([record({ record_type: "statmech", container_type: "transition_state_entry", container_ref: "tse_4" })], {
                subject_type: "transition_state_entry",
                subject_ref: "tse_4",
                chemistry: chemistry({ multiplicity: 2, unmapped_smiles: "[CH3].[H]>>C" }),
            }),
        ])
        renderPage()

        const section = await screen.findByText("tse_4").then((el) => el.closest(".review-subject") as HTMLElement)
        expect(within(section).queryByText(/no smiles recorded for this candidate/i)).not.toBeInTheDocument()
        expect(within(section).getByText(/no formula could be derived/i)).toBeInTheDocument()
        expect(within(section).getByText("[CH3].[H]>>C")).toBeInTheDocument()
    })

    it("names a subject with no chemistry of its own by its record type, honestly", async () => {
        meIs(curator)
        queueIs([
            subject(
                [record({ record_type: "conformer_observation", container_type: "conformer_group", container_ref: "cfg_1" })],
                { subject_type: "conformer_group", subject_ref: "cfg_1", chemistry: chemistry() },
            ),
        ])
        renderPage()

        const section = await screen.findByText("cfg_1").then((el) => el.closest(".review-subject") as HTMLElement)
        expect(within(section).getByRole("heading", { name: "Conformer group" })).toBeInTheDocument()
    })

    it("the phrase 'cannot be named' does not survive the redesign", async () => {
        meIs(curator)
        queueIs([
            H2O,
            // The orphan case: a review row whose record and container
            // both resolved to nothing.
            {
                subject_type: null,
                subject_ref: null,
                chemistry: chemistry(),
                records: [record({ id: 99, record_type: "applied_energy_correction", container_type: null, container_ref: null })],
            },
        ])
        renderPage()

        await screen.findByText("spe_h2o")
        expect(screen.queryByText(/cannot be named/i)).not.toBeInTheDocument()
        expect(await screen.findByText(/no record could be found for this review row/i)).toBeInTheDocument()
    })

    it("names a reaction_entry subject by its own equation, not a bare ref (defect #6)", async () => {
        // Review #492, finding 6: a kinetics reviewer used to see "Reaction
        // entry rxe_..." and nothing else -- the species side of this page
        // named subjects well and the reaction side named nothing.
        meIs(curator)
        queueIs([
            subject(
                [record({ record_type: "kinetics", container_type: "reaction_entry", container_ref: "rxe_ab12" })],
                {
                    subject_type: "reaction_entry",
                    subject_ref: "rxe_ab12",
                    chemistry: chemistry(),
                    reaction: reactionEquation({
                        reversible: true,
                        reactants: [
                            reactionParticipant({ species_entry_ref: "spe_ch3", smiles: "[CH3]", formula: "CH3" }),
                        ],
                        products: [
                            reactionParticipant({ species_entry_ref: "spe_ch4", smiles: "C", formula: "CH4" }),
                        ],
                    }),
                },
            ),
        ])
        renderPage()

        const section = await screen.findByText("rxe_ab12").then((el) => el.closest(".review-subject") as HTMLElement)
        const heading = within(section).getByRole("heading", { level: 2 })
        expect(heading).toHaveTextContent(/CH3/)
        expect(heading).toHaveTextContent(/CH4/)
        expect(heading).not.toHaveTextContent(/^Reaction entry/)
    })

    it("shows each participant's formula ONCE, not its SMILES and formula both (review #492 follow-up)", async () => {
        // The default <ReactionEquation> rendering shows BOTH -- "[CH3]
        // (CH3) + [H] (H) ⇌ C (CH4)" -- which review of #492 caught as
        // noise here: a bare "C" next to "(CH4)" reads as a typo, and the
        // species blocks above this one already show formula alone. This
        // page must pass `formulaOnly` so exactly one notation appears.
        meIs(curator)
        queueIs([
            subject(
                [record({ record_type: "kinetics", container_type: "reaction_entry", container_ref: "rxe_formula_only" })],
                {
                    subject_type: "reaction_entry",
                    subject_ref: "rxe_formula_only",
                    chemistry: chemistry(),
                    reaction: reactionEquation({
                        reversible: true,
                        reactants: [
                            reactionParticipant({ species_entry_ref: "spe_ch3", smiles: "[CH3]", formula: "CH3" }),
                            reactionParticipant({ species_entry_ref: "spe_h", smiles: "[H]", formula: "H", participant_index: 2 }),
                        ],
                        products: [
                            reactionParticipant({ species_entry_ref: "spe_ch4", smiles: "C", formula: "CH4" }),
                        ],
                    }),
                },
            ),
        ])
        renderPage()

        const section = await screen
            .findByText("rxe_formula_only")
            .then((el) => el.closest(".review-subject") as HTMLElement)
        const heading = within(section).getByRole("heading", { level: 2 })
        // No SMILES text anywhere in the heading -- not as its own run,
        // and not the bare "C" that made this defect visible.
        expect(within(heading).queryByText("[CH3]")).not.toBeInTheDocument()
        expect(within(heading).queryByText("[H]")).not.toBeInTheDocument()
        expect(within(heading).queryByText("C")).not.toBeInTheDocument()
        // No parenthesised formula either -- formula is the WHOLE
        // notation here, not an aside after the SMILES.
        expect(heading.textContent).not.toContain("(CH4)")
        expect(heading.textContent).not.toContain("(CH3)")
    })

    it("falls back to SMILES-leading form for an isomerisation, so it never renders X <=> X", async () => {
        // The coordinator's own catch: formula-only would render this
        // reactant/product pair (same formula C9H8, different structures)
        // as "C9H8 <=> C9H8" -- the exact species-reacting-with-itself
        // defect `SpeciesFace`'s SMILES-leading design exists to prevent
        // (rxn_fktlilofmrdaylunqva2hbltpq). This equation must fall back
        // to the ordinary SMILES+formula rendering for ALL its
        // participants, not silently print the ambiguous pair.
        meIs(curator)
        queueIs([
            subject(
                [record({ record_type: "kinetics", container_type: "reaction_entry", container_ref: "rxe_isomerisation" })],
                {
                    subject_type: "reaction_entry",
                    subject_ref: "rxe_isomerisation",
                    chemistry: chemistry(),
                    reaction: reactionEquation({
                        reversible: true,
                        reactants: [
                            reactionParticipant({
                                species_entry_ref: "spe_indene",
                                smiles: "C1=CC2=CC=CC=C2C1",
                                formula: "C9H8",
                            }),
                        ],
                        products: [
                            reactionParticipant({
                                species_entry_ref: "spe_indene_isomer",
                                smiles: "C1=CC2=CC=CC=C2C=1",
                                formula: "C9H8",
                            }),
                        ],
                    }),
                },
            ),
        ])
        renderPage()

        const section = await screen
            .findByText("rxe_isomerisation")
            .then((el) => el.closest(".review-subject") as HTMLElement)
        const heading = within(section).getByRole("heading", { level: 2 })
        // The SMILES are back -- proof this equation did NOT take the
        // formula-only path.
        expect(within(heading).getByText("C1=CC2=CC=CC=C2C1")).toBeInTheDocument()
        expect(within(heading).getByText("C1=CC2=CC=CC=C2C=1")).toBeInTheDocument()
        // And the formula still follows each one, in parentheses, exactly
        // as the ordinary (non-formulaOnly) SpeciesFace rendering does --
        // this is the fallback, not a third notation.
        expect(heading.textContent).toContain("(C9H8)")
    })

    it("does NOT fall back when the same species appears on both sides of its own equation", async () => {
        // A catalyst-shaped equation: one species, same ref, on both
        // sides. That is correctly the same molecule rendering the same
        // way twice -- not the ambiguity the fallback exists for.
        meIs(curator)
        queueIs([
            subject(
                [record({ record_type: "kinetics", container_type: "reaction_entry", container_ref: "rxe_catalyst" })],
                {
                    subject_type: "reaction_entry",
                    subject_ref: "rxe_catalyst",
                    chemistry: chemistry(),
                    reaction: reactionEquation({
                        reversible: false,
                        reactants: [
                            reactionParticipant({ species_entry_ref: "spe_cat", smiles: "[Pt]", formula: "Pt", participant_index: 1 }),
                            reactionParticipant({ species_entry_ref: "spe_h2", smiles: "[H][H]", formula: "H2", participant_index: 2 }),
                        ],
                        products: [
                            reactionParticipant({ species_entry_ref: "spe_cat", smiles: "[Pt]", formula: "Pt", participant_index: 1 }),
                        ],
                    }),
                },
            ),
        ])
        renderPage()

        const section = await screen
            .findByText("rxe_catalyst")
            .then((el) => el.closest(".review-subject") as HTMLElement)
        const heading = within(section).getByRole("heading", { level: 2 })
        expect(within(heading).queryByText("[Pt]")).not.toBeInTheDocument()
        expect(heading.textContent).not.toContain("(Pt)")
    })

    it("falls back when one participant has no formula, so it never mixes notations on one line", async () => {
        // Review #492 (second round), finding 4: with no duplicate at all,
        // a participant with `formula: null` still forced mixed notation
        // under the old rule -- "C9H8 <=> CC1=CC=CC=1", formula on one
        // side, SMILES on the other, exactly the confusion this page's
        // own comment already named. A missing formula must force the
        // WHOLE equation to the SMILES-leading form, on its own, with no
        // collision required.
        meIs(curator)
        queueIs([
            subject(
                [record({ record_type: "kinetics", container_type: "reaction_entry", container_ref: "rxe_no_formula" })],
                {
                    subject_type: "reaction_entry",
                    subject_ref: "rxe_no_formula",
                    chemistry: chemistry(),
                    reaction: reactionEquation({
                        reversible: true,
                        reactants: [
                            reactionParticipant({
                                species_entry_ref: "spe_known",
                                smiles: "C1=CC=CC=C1",
                                formula: "C9H8",
                                participant_index: 1,
                            }),
                        ],
                        products: [
                            reactionParticipant({
                                species_entry_ref: "spe_unparsed",
                                smiles: "CC1=CC=CC=1",
                                formula: null,
                                participant_index: 1,
                            }),
                        ],
                    }),
                },
            ),
        ])
        renderPage()

        const section = await screen
            .findByText("rxe_no_formula")
            .then((el) => el.closest(".review-subject") as HTMLElement)
        const heading = within(section).getByRole("heading", { level: 2 })
        // Both participants render as SMILES -- not one formula, one
        // SMILES.
        expect(within(heading).getByText("C1=CC=CC=C1")).toBeInTheDocument()
        expect(within(heading).getByText("CC1=CC=CC=1")).toBeInTheDocument()
        expect(heading.textContent).toContain("(C9H8)")
    })

    it("falls back to the plain type label when a reaction_entry subject has no resolvable equation", async () => {
        meIs(curator)
        queueIs([
            subject(
                [record({ record_type: "kinetics", container_type: "reaction_entry", container_ref: "rxe_cd34" })],
                {
                    subject_type: "reaction_entry",
                    subject_ref: "rxe_cd34",
                    chemistry: chemistry(),
                    reaction: null,
                },
            ),
        ])
        renderPage()

        const section = await screen.findByText("rxe_cd34").then((el) => el.closest(".review-subject") as HTMLElement)
        expect(within(section).getByRole("heading", { name: "Reaction entry" })).toBeInTheDocument()
    })

    it("drops the redundant 'minimum ground state' chips for an ordinary species (defect #8)", async () => {
        // Review #492, finding 8: "minimum · ground state" on nearly
        // every ordinary molecule carries no information there.
        meIs(curator)
        queueIs([H2O])
        renderPage()

        const section = await subjectFor("spe_h2o")
        expect(within(section).queryByText("minimum")).not.toBeInTheDocument()
        expect(within(section).queryByText("ground state")).not.toBeInTheDocument()
        // Real information -- the spin word -- still shows.
        expect(within(section).getByText("singlet")).toBeInTheDocument()
    })

    it("keeps the kind chip when it is NOT the ordinary default", async () => {
        meIs(curator)
        queueIs([
            subject([record()], {
                subject_ref: "spe_vdw",
                chemistry: chemistry({
                    formula: "H2O",
                    multiplicity: 1,
                    species_entry_kind: "vdw_complex",
                    electronic_state_kind: "ground",
                }),
            }),
        ])
        renderPage()

        const section = await subjectFor("spe_vdw")
        expect(within(section).getByText("van der Waals complex")).toBeInTheDocument()
    })
})

describe("collapsing same-type records under one subject (defect #2: the duplicate-looking pair)", () => {
    function twoCorrections(overA: Record<string, unknown> = {}, overB: Record<string, unknown> = {}) {
        return subject(
            [
                record({
                    id: 21,
                    record_type: "applied_energy_correction",
                    record_id: 501,
                    record_public_ref: null,
                    note: "atom-energy correction",
                    ...overA,
                }),
                record({
                    id: 22,
                    record_type: "applied_energy_correction",
                    record_id: 502,
                    record_public_ref: null,
                    note: "bond-additivity correction",
                    ...overB,
                }),
            ],
            { subject_ref: "spe_two_corrections" },
        )
    }

    it("renders two corrections on one species as one line with a count, not two lines", async () => {
        meIs(curator)
        queueIs([twoCorrections()])
        renderPage()

        const section = await screen
            .findByText("spe_two_corrections")
            .then((el) => el.closest(".review-subject") as HTMLElement)

        expect(within(section).getByText("Energy corrections")).toBeInTheDocument()
        expect(within(section).getByText("2", { selector: ".review-record-count" })).toBeInTheDocument()
        expect(
            within(section).queryByText("atom-energy correction"),
        ).not.toBeInTheDocument()
    })

    it("expands to reach and judge each record individually", async () => {
        meIs(curator)
        queueIs([twoCorrections()])
        renderPage()
        const section = await screen
            .findByText("spe_two_corrections")
            .then((el) => el.closest(".review-subject") as HTMLElement)

        const user = userEvent.setup()
        await user.click(within(section).getByRole("button", { name: "Show 2 records" }))

        expect(within(section).getByText("atom-energy correction")).toBeInTheDocument()
        expect(within(section).getByText("bond-additivity correction")).toBeInTheDocument()
        // Each expanded record keeps its OWN Review... control -- a
        // collapsed cluster is not a per-subject bulk approve (task #270
        // is explicitly not this).
        expect(within(section).getAllByRole("button", { name: "Review…" })).toHaveLength(2)
    })

    it("acting on one expanded record does not touch its sibling", async () => {
        meIs(curator)
        queueIs([twoCorrections()])
        let patchedId: string | null = null
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, ({ params }) => {
                patchedId = String(params.id)
                return HttpResponse.json(record({ status: "approved" }))
            }),
        )
        renderPage()
        const section = await screen
            .findByText("spe_two_corrections")
            .then((el) => el.closest(".review-subject") as HTMLElement)

        const user = userEvent.setup()
        await user.click(within(section).getByRole("button", { name: "Show 2 records" }))
        const rows = within(section).getAllByRole("button", { name: "Review…" })
        await user.click(rows[0])
        await user.type(within(section).getByLabelText(/Why/), "checked the atom energies")
        await user.click(within(section).getByRole("button", { name: "Record this judgement" }))

        await waitFor(() => expect(patchedId).toBe("501"))
    })

    it("shows the shared status when every collapsed record agrees, under a filter it does not just repeat", async () => {
        meIs(curator)
        // "all" is the one filter a shown status is never redundant
        // against -- see the "does not repeat the active filter" suite
        // below for the not_reviewed case, where this same status is
        // deliberately suppressed.
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json(
                    queuePageBody([twoCorrections({ status: "approved" }, { status: "approved" })]),
                ),
            ),
        )
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")
        const section = await screen
            .findByText("spe_two_corrections")
            .then((el) => el.closest(".review-subject") as HTMLElement)

        expect(within(section).getByText("approved")).toBeInTheDocument()
        expect(within(section).queryByText(/mixed/i)).not.toBeInTheDocument()
    })

    it("says 'mixed' rather than picking one status when the collapsed records disagree", async () => {
        meIs(curator)
        queueIs([twoCorrections({ status: "not_reviewed" }, { status: "approved" })])
        renderPage()
        const section = await screen
            .findByText("spe_two_corrections")
            .then((el) => el.closest(".review-subject") as HTMLElement)

        expect(within(section).getByText(/mixed review state/i)).toBeInTheDocument()
    })
})

describe("the header's totals (defect #3: '50 shown' with no total)", () => {
    it("reports the server's own subject and record counts, honestly, not a count of what is drawn", async () => {
        meIs(curator)
        // The server says there are 3 subjects and 5 records in the WHOLE
        // filtered backlog, even though only one subject is on this page --
        // the page must print the server's numbers, not `subjects.length`.
        queueIs([H2O])
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json(queuePageBody([H2O], { subject_total: 3, record_total: 5 })),
            ),
        )
        renderPage()

        expect(await screen.findByText(/5 records awaiting review/i)).toBeInTheDocument()
        expect(await screen.findByText(/3 subjects/i)).toBeInTheDocument()
    })

    it("does not say 'awaiting review' outside the not_reviewed filter", async () => {
        meIs(curator)
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json(queuePageBody([subject([record({ status: "approved" })])])),
            ),
        )
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "approved")

        await screen.findByText("spe_h2o")
        expect(screen.queryByText(/awaiting review/i)).not.toBeInTheDocument()
    })

    it("an empty backlog reads as finished, not as broken", async () => {
        meIs(curator)
        queueIs([])
        renderPage()

        expect(
            await screen.findByText(/Every record has been looked at/i),
        ).toBeInTheDocument()
    })

    it("a queue that will not load is reported, not shown as finished", async () => {
        meIs(curator)
        server.use(http.get(QUEUE, () => HttpResponse.error()))
        renderPage()

        expect(await screen.findByRole("alert")).toHaveTextContent(/could not be loaded|could not load/i)
        expect(screen.queryByText(/Every record has been looked at/i)).not.toBeInTheDocument()
    })

    it("a page this build cannot parse is reported, not silently emptied", async () => {
        meIs(curator)
        // Unlike the old flat list, the grouped shape has no obvious
        // per-row unit to drop -- a malformed subject costs the whole page.
        server.use(http.get(QUEUE, () => HttpResponse.json({ subjects: "not an array" })))
        renderPage()

        expect(await screen.findByRole("alert")).toBeInTheDocument()
    })
})

describe("a record's status is not repeated when it only echoes the active filter", () => {
    it("drops a record's own status under a specific filter, where every shown record already shares it", async () => {
        meIs(curator)
        queueIs([H2O])
        renderPage()

        const section = await subjectFor("spe_h2o")
        // "Showing: not reviewed" above already says this -- the record's
        // own status line would say it again, fifteen times on a real page.
        expect(within(section).queryByText("not reviewed")).not.toBeInTheDocument()
        // The record and its action are still there.
        expect(within(section).getByText("Thermochemistry")).toBeInTheDocument()
        expect(within(section).getByRole("button", { name: "Review…" })).toBeInTheDocument()
    })

    it("shows a record's own status under 'all', where it is the information", async () => {
        meIs(curator)
        server.use(http.get(QUEUE, () => HttpResponse.json(queuePageBody([H2O]))))
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")

        const section = await subjectFor("spe_h2o")
        expect(within(section).getByText("not reviewed")).toBeInTheDocument()
    })

    it("still shows a record's status under a specific filter when it genuinely differs from it", async () => {
        meIs(curator)
        // Not reachable through the server's own filtering today (a
        // specific filter already narrows every returned row to that
        // status), but the page must not assume that and hide a status
        // that turns out to disagree -- it checks the actual value.
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json(queuePageBody([subject([record({ status: "approved" })])])),
            ),
        )
        renderPage()

        const section = await subjectFor("spe_h2o")
        expect(within(section).getByText("approved")).toBeInTheDocument()
    })
})

describe("only transitions the backend allows are offered", () => {
    it("offers all four from not_reviewed", async () => {
        meIs(curator)
        queueIs([H2O])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())
        expect(options).toEqual(["under review", "approved", "rejected", "deprecated"])
    })

    it("does not offer approved -> rejected, which routes through under review", async () => {
        meIs(curator)
        queueIs([subject([record({ status: "approved" })])])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())
        expect(options).toEqual(["under review", "deprecated"])
        expect(options).not.toContain("rejected")
    })

    it("spells out what each state asserts about the record", async () => {
        meIs(curator)
        queueIs([H2O])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        const choice = screen.getByLabelText(/New review state/)

        expect(screen.getByText(/No judgement is recorded yet/i)).toBeInTheDocument()
        await user.selectOptions(choice, "approved")
        expect(screen.getByText(/Readers are told it is trusted/i)).toBeInTheDocument()
    })
})

describe("recording a judgement", () => {
    it("sends the record's type and id, the new status, and the reason", async () => {
        meIs(curator)
        queueIs([H2O])
        let body: Record<string, unknown> = {}
        let path = ""
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, async ({ request, params }) => {
                path = `${params.type}/${params.id}`
                body = (await request.json()) as Record<string, unknown>
                return HttpResponse.json(record({ status: "approved", note: "checked" }))
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.selectOptions(screen.getByLabelText(/New review state/), "approved")
        await user.type(screen.getByLabelText(/Why/), "checked")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await waitFor(() => expect(body.status).toBe("approved"))
        expect(path).toBe("thermo/987654")
        expect(body.note).toBe("checked")
    })

    it("will not record a judgement without a reason", async () => {
        meIs(curator)
        queueIs([H2O])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))

        expect(screen.getByRole("button", { name: "Record this judgement" })).toBeDisabled()
        await user.type(screen.getByLabelText(/Why/), "   ")
        expect(screen.getByRole("button", { name: "Record this judgement" })).toBeDisabled()
    })

    it("re-reads the queue from the server afterwards", async () => {
        meIs(curator)
        const counter = queueIs([H2O])
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, () =>
                HttpResponse.json(record({ status: "approved" })),
            ),
        )
        renderPage()
        await screen.findByText("spe_h2o")
        const before = counter.reads

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await waitFor(() => expect(counter.reads).toBeGreaterThan(before))
    })

    it("believes the server over its own copy of the transition policy", async () => {
        meIs(curator)
        queueIs([H2O])
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, () =>
                HttpResponse.json(
                    { code: "domain_error", detail: "Transition not_reviewed -> approved is not allowed." },
                    { status: 400 },
                ),
            ),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        expect(await screen.findByRole("alert")).toHaveTextContent(
            /Transition not_reviewed -> approved is not allowed/,
        )
    })

    it("surfaces the self-approval refusal as the server words it", async () => {
        meIs(curator)
        queueIs([H2O])
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, () =>
                HttpResponse.json(
                    { code: "domain_error", detail: "You cannot approve a record you deposited." },
                    { status: 400 },
                ),
            ),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.type(screen.getByLabelText(/Why/), "mine, but good")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        expect(await screen.findByRole("alert")).toHaveTextContent(
            /cannot approve a record you deposited/i,
        )
    })

    it("never reports a committed judgement as 'nothing was changed'", async () => {
        meIs(curator)
        queueIs([H2O])
        let hits = 0
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, () => {
                hits += 1
                return HttpResponse.json({ id: 11, status: "provisionally_endorsed" })
            }),
        )
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/was saved/i)
        expect(alert).not.toHaveTextContent(/Nothing was changed/i)
        expect(hits).toBe(1)
        await waitFor(() => expect(screen.queryByLabelText(/Why/)).not.toBeInTheDocument())
    })
})

describe("everything a row owns stays with that row", () => {
    it("does not carry one record's reason over to another record, in a different subject", async () => {
        meIs(curator)
        queueIs([H2O, CH4_CALC])
        let sent: Record<string, unknown> = {}
        server.use(
            http.patch(`${PATCH_BASE}/calculation/123456`, async ({ request }) => {
                sent = (await request.json()) as Record<string, unknown>
                return HttpResponse.json(record({ status: "approved" }))
            }),
        )
        renderPage()
        await screen.findByText("spe_h2o")

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.type(screen.getByLabelText(/Why/), "the h2o geometry is fine")
        await user.click(reviewButtonIn(await subjectFor("spe_ch4")))

        expect(screen.getByLabelText(/Why/)).toHaveValue("")
        await user.type(screen.getByLabelText(/Why/), "the calculation converged")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await waitFor(() => expect(sent.note).toBe("the calculation converged"))
    })

    it("does not carry one row's chosen state over either", async () => {
        meIs(curator)
        queueIs([H2O, CH4_CALC])
        renderPage()
        await screen.findByText("spe_h2o")

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.selectOptions(screen.getByLabelText(/New review state/), "deprecated")
        await user.click(reviewButtonIn(await subjectFor("spe_ch4")))

        expect(screen.getByLabelText(/New review state/)).toHaveValue("under_review")
    })

    it("keeps each subject's own refusal, instead of one slot they overwrite", async () => {
        meIs(curator)
        queueIs([H2O, CH4_CALC])
        server.use(
            http.patch(`${PATCH_BASE}/thermo/987654`, () =>
                HttpResponse.json({ code: "domain_error", detail: "H2O record is stuck." }, { status: 400 }),
            ),
            http.patch(`${PATCH_BASE}/calculation/123456`, () =>
                HttpResponse.json({ code: "domain_error", detail: "CH4 calculation is stuck." }, { status: 400 }),
            ),
        )
        renderPage()
        await screen.findByText("spe_h2o")

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.type(screen.getByLabelText(/Why/), "a")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText("H2O record is stuck.")

        await user.click(reviewButtonIn(await subjectFor("spe_ch4")))
        await user.type(screen.getByLabelText(/Why/), "b")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText("CH4 calculation is stuck.")

        expect(screen.getByText("H2O record is stuck.")).toBeInTheDocument()
    })

    it("disables a row's own control while its write is in flight, and leaves the other subject alone", async () => {
        meIs(curator)
        queueIs([H2O, CH4_CALC])
        const write = gate()
        server.use(
            http.patch(`${PATCH_BASE}/thermo/987654`, async () => {
                await write.held
                return HttpResponse.json(record({ status: "approved" }))
            }),
        )
        renderPage()
        await screen.findByText("spe_h2o")

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        expect(screen.getByRole("button", { name: "Record this judgement" })).toBeDisabled()
        // The other subject's own control is untouched.
        expect(reviewButtonIn(await subjectFor("spe_ch4"))).toBeEnabled()

        write.release()
        await waitFor(() => expect(screen.queryByLabelText(/Why/)).not.toBeInTheDocument())
    })
})

describe("a refusal is still said when its row has gone", () => {
    it("says what was refused, and about which record, after the row leaves the view", async () => {
        meIs(curator)
        let moved = false
        queueByStatus(() => [subject([record({ status: moved ? "approved" : "not_reviewed" })])])
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, () => {
                moved = true
                return HttpResponse.json(
                    { code: "domain_error", detail: "Transition not_reviewed -> rejected is not allowed." },
                    { status: 400 },
                )
            }),
        )
        renderPage()
        await screen.findByText("spe_h2o")

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.selectOptions(screen.getByLabelText(/New review state/), "rejected")
        await user.type(screen.getByLabelText(/Why/), "wrong")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        const banner = await screen.findByText(/no longer in this view/i)
        expect(banner).toHaveTextContent(/is not allowed/)
    })
})

describe("what a curator may not do to their own deposit", () => {
    it("does not offer approval on a record this curator deposited", async () => {
        meIs(curator)
        queueIs([subject([record({ created_by: curator.id })])])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())

        expect(options).not.toContain("approved")
        expect(options).toContain("rejected")
    })

    it("still offers approval on somebody else's deposit", async () => {
        meIs(curator)
        queueIs([subject([record({ created_by: curator.id + 999 })])])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())

        expect(options).toContain("approved")
    })

    it("offers approval when the depositor is unknown", async () => {
        meIs(curator)
        queueIs([subject([record({ created_by: null })])])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())

        expect(options).toContain("approved")
    })
})

describe("the filter control", () => {
    it("asks the server for the status chosen", async () => {
        meIs(curator)
        const asked: (string | null)[] = []
        server.use(
            http.get(QUEUE, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("status"))
                return HttpResponse.json(queuePageBody([subject([record({ status: "approved" })])]))
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByText("spe_h2o")

        await user.selectOptions(screen.getByLabelText("Showing"), "approved")
        await waitFor(() => expect(asked).toEqual(["not_reviewed", "approved"]))
    })

    it("sends no status at all for 'every record'", async () => {
        meIs(curator)
        const asked: (string | null)[] = []
        server.use(
            http.get(QUEUE, ({ request }) => {
                asked.push(new URL(request.url).searchParams.get("status"))
                return HttpResponse.json(queuePageBody([H2O]))
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByText("spe_h2o")

        await user.selectOptions(screen.getByLabelText("Showing"), "all")
        await waitFor(() => expect(asked).toEqual(["not_reviewed", null]))
    })

    it("asks for a page of the size it claims -- SUBJECTS, not records", async () => {
        meIs(curator)
        let limit: string | null = null
        server.use(
            http.get(QUEUE, ({ request }) => {
                limit = new URL(request.url).searchParams.get("limit")
                return HttpResponse.json(queuePageBody([H2O]))
            }),
        )
        renderPage()
        await screen.findByText("spe_h2o")

        expect(limit).toBe("50")
    })
})

describe("paging counts subjects, never records", () => {
    function manySubjects(n: number) {
        return Array.from({ length: n }, (_, i) =>
            subject([record({ id: 100 + i, record_id: 100 + i })], { subject_ref: `spe_${i}` }),
        )
    }

    it("asks for the next page by subject offset, and says where it is", async () => {
        meIs(curator)
        const asked: (string | null)[] = []
        server.use(
            http.get(QUEUE, ({ request }) => {
                const url = new URL(request.url)
                asked.push(url.searchParams.get("skip"))
                const skip = Number(url.searchParams.get("skip") ?? 0)
                return HttpResponse.json(
                    queuePageBody(manySubjects(50), { subject_total: 120, offset: skip }),
                )
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByText("spe_0")

        await user.click(screen.getByRole("button", { name: "Older" }))
        await waitFor(() => expect(asked).toEqual(["0", "50"]))
        expect(await screen.findByText(/showing subjects 51-100/i)).toBeInTheDocument()
    })

    it("disables Older once every subject has been shown", async () => {
        meIs(curator)
        queueIs([H2O])
        renderPage()
        await screen.findByText("spe_h2o")

        expect(screen.getByRole("button", { name: "Older" })).toBeDisabled()
        expect(screen.getByRole("button", { name: "Newer" })).toBeDisabled()
    })

    it("a subject with several records still counts as one against the page limit", async () => {
        meIs(curator)
        // Server-side truth: 1 subject on this page even though it carries
        // 2 records, and 1 subject total -- the frontend must print the
        // server's numbers rather than counting nested records itself.
        queueIs([subject([record({ id: 1 }), record({ id: 2, record_type: "statmech" })])])
        renderPage()

        expect(await screen.findByText(/2 records awaiting review/i)).toBeInTheDocument()
        expect(await screen.findByText(/1 subject\b/i)).toBeInTheDocument()
    })

    it("returns to the first page when the filter changes", async () => {
        meIs(curator)
        const asked: string[] = []
        server.use(
            http.get(QUEUE, ({ request }) => {
                const url = new URL(request.url)
                asked.push(`${url.searchParams.get("status")}@${url.searchParams.get("skip")}`)
                return HttpResponse.json(queuePageBody(manySubjects(50), { subject_total: 120 }))
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByText("spe_0")

        await user.click(screen.getByRole("button", { name: "Older" }))
        await waitFor(() => expect(asked).toHaveLength(2))
        await user.selectOptions(screen.getByLabelText("Showing"), "approved")

        await waitFor(() => expect(asked.at(-1)).toBe("approved@0"))
    })
})

/**
 * Review #492, finding 4: this file was rewritten wholesale for the new
 * `/queue` wire shape and markup, and several guarantees the OLD suite
 * pinned were never carried over -- not because the behaviour changed, but
 * because nothing re-asserted it against the new DOM. The first two below
 * are #488's own guarantees and are explicitly not negotiable; the rest are
 * the redesign's own regressions-to-avoid, ported to subject/record
 * fixtures instead of the old flat rows.
 */
describe("guarantees carried over from the flat queue (review #492, finding 4)", () => {
    it("opens every queue link in a new tab, safely (rel carries both noopener and noreferrer)", async () => {
        // Scoped to the subject list, not the whole page: the lede's own
        // "Machine findings" link is deliberate ordinary in-app
        // navigation (see the module docstring), not one of the
        // stateful queue links this guarantee is about.
        meIs(curator)
        queueIs([H2O, CH4_CALC])
        renderPage()
        await screen.findByText("spe_h2o")

        const sections = document.querySelectorAll(".review-subject")
        expect(sections.length).toBeGreaterThan(0)
        const links = Array.from(sections).flatMap((section) =>
            Array.from(section.querySelectorAll("a")),
        )
        expect(links.length).toBeGreaterThan(0)
        for (const link of links) {
            expect(link).toHaveAttribute("target", "_blank")
            const rel = link.getAttribute("rel") ?? ""
            expect(rel).toMatch(/noopener/)
            expect(rel).toMatch(/noreferrer/)
        }
    })

    it("puts no internal row id anywhere in the page", async () => {
        meIs(curator)
        queueIs([
            subject([record({ id: 777, record_id: 888111 })]),
            subject(
                [
                    record({
                        id: 778,
                        record_type: "calculation",
                        record_id: 999222,
                        record_public_ref: "calc_hidden_id_check",
                    }),
                ],
                { subject_ref: "spe_second" },
            ),
        ])
        const { container } = renderPage()
        await screen.findByText("spe_h2o")
        await screen.findByText("spe_second")

        expect(container.innerHTML).not.toContain("888111")
        expect(container.innerHTML).not.toContain("999222")
    })

    it("still offers a way back from a page that came back empty", async () => {
        // A page can come back empty even though "Older" was enabled: the
        // total this page reports is honest AS OF each request, not a
        // snapshot, so a subject reviewed by someone else between the two
        // page loads can shrink the true count from underneath a curator
        // already mid-page -- see `list_review_queue`'s own "offset drift"
        // documentation. Page 1 truthfully reports 51 (Older enabled);
        // by the time page 2 is requested only 50 remain, and page 2 --
        // subjects 51-51 -- is honestly empty.
        meIs(curator)
        const fifty = Array.from({ length: 50 }, (_, i) =>
            subject([record({ id: 100 + i, record_id: 100 + i })], { subject_ref: `spe_p_${i}` }),
        )
        server.use(
            http.get(QUEUE, ({ request }) => {
                const skip = Number(new URL(request.url).searchParams.get("skip") ?? 0)
                return HttpResponse.json(
                    skip === 0
                        ? queuePageBody(fifty, { subject_total: 51 })
                        : queuePageBody([], { subject_total: 50, offset: skip }),
                )
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByText("spe_p_0")
        expect(screen.getByRole("button", { name: "Older" })).toBeEnabled()

        await user.click(screen.getByRole("button", { name: "Older" }))

        expect(await screen.findByText(/Nothing older than this/i)).toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Newer" })).toBeEnabled()
        expect(screen.getByRole("button", { name: "Older" })).toBeDisabled()
    })

    it("goes back one page at a time, not to the start", async () => {
        meIs(curator)
        const asked: string[] = []
        const fifty = Array.from({ length: 50 }, (_, i) =>
            subject([record({ id: 100 + i, record_id: 100 + i })], { subject_ref: `spe_q_${i}` }),
        )
        server.use(
            http.get(QUEUE, ({ request }) => {
                const skip = new URL(request.url).searchParams.get("skip") ?? "0"
                asked.push(skip)
                return HttpResponse.json(queuePageBody(fifty, { subject_total: 200 }))
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByText("spe_q_0")

        await user.click(screen.getByRole("button", { name: "Older" }))
        await waitFor(() => expect(asked).toHaveLength(2))
        await user.click(screen.getByRole("button", { name: "Older" }))
        await waitFor(() => expect(asked).toHaveLength(3))
        await user.click(screen.getByRole("button", { name: "Newer" }))
        await waitFor(() => expect(asked).toEqual(["0", "50", "100", "50"]))
    })

    it("does not offer rejected -> approved either", async () => {
        meIs(curator)
        queueIs([subject([record({ status: "rejected" })])])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        const options = within(screen.getByLabelText(/New review state/))
            .getAllByRole("option")
            .map((o) => (o.textContent ?? "").trim())
        expect(options).toEqual(["under review", "deprecated"])
        expect(options).not.toContain("approved")
    })

    it("re-derives the choice when a re-read makes it impossible", async () => {
        meIs(curator)
        // not_reviewed offers rejected; approved does not. A re-read that
        // moves the row to approved leaves "rejected" held in a draft that
        // no option matches -- a controlled <select> whose value matches
        // nothing displays the FIRST option, silently.
        let moved = false
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json(
                    queuePageBody([
                        subject([record({ status: moved ? "approved" : "not_reviewed" })]),
                    ]),
                ),
            ),
            http.patch(`${PATCH_BASE}/:type/:id`, async () => {
                if (!moved) {
                    moved = true
                    return HttpResponse.json(
                        { code: "domain_error", detail: "Not allowed." },
                        { status: 400 },
                    )
                }
                return HttpResponse.json(record({ status: "under_review" }))
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")
        const section = await subjectFor("spe_h2o")

        await user.click(reviewButtonIn(section))
        await user.selectOptions(screen.getByLabelText(/New review state/), "rejected")
        await user.type(screen.getByLabelText(/Why/), "first try")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await waitFor(() => expect(within(section).getByText("approved")).toBeInTheDocument())
        const select = screen.getByLabelText(/New review state/) as HTMLSelectElement
        expect(["under_review", "deprecated"]).toContain(select.value)
        expect(screen.queryByText(/judged this record wrong/i)).not.toBeInTheDocument()
    })

    it("does not close another row's open form when a write succeeds", async () => {
        meIs(curator)
        queueIs([H2O, CH4_CALC])
        const write = gate()
        server.use(
            http.patch(`${PATCH_BASE}/thermo/987654`, async () => {
                await write.held
                return HttpResponse.json(record({ status: "under_review" }))
            }),
        )
        renderPage()
        await screen.findByText("spe_h2o")

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        await user.type(screen.getByLabelText(/Why/), "species reason")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await user.click(reviewButtonIn(await subjectFor("spe_ch4")))
        await user.type(screen.getByLabelText(/Why/), "calculation reason")

        write.release()

        await waitFor(() => expect(reviewButtonIn(screen.getByText("spe_h2o").closest(".review-subject") as HTMLElement)).toBeEnabled())
        expect(screen.getByLabelText(/Why/)).toHaveValue("calculation reason")
    })

    it("shows each row's own state, not a neighbour's", async () => {
        meIs(curator)
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json(
                    queuePageBody([
                        subject([record({ status: "approved" })]),
                        CH4_CALC,
                    ]),
                ),
            ),
        )
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")

        const h2oSection = await subjectFor("spe_h2o")
        const ch4Section = await subjectFor("spe_ch4")
        expect(within(h2oSection).getByText("approved")).toBeInTheDocument()
        expect(within(ch4Section).getByText("not reviewed")).toBeInTheDocument()
        expect(within(ch4Section).queryByText("approved")).not.toBeInTheDocument()
    })

    it("shows the reason recorded on a judged record", async () => {
        meIs(curator)
        queueIs([subject([record({ status: "approved", note: "frequencies check out by hand" })])])
        renderPage()

        expect(await screen.findByText("frequencies check out by hand")).toBeInTheDocument()
    })

    it("reads a row from a server that has never heard of containers", async () => {
        // An older backend sends neither container_type nor container_ref
        // on a nested record. Dropping the whole page would be the worst
        // possible answer to "what still needs review" -- the schema
        // defaults both to null and the record still renders.
        meIs(curator)
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json({
                    subjects: [
                        {
                            subject_type: "species_entry",
                            subject_ref: "spe_h2o",
                            chemistry: chemistry({ formula: "H2O" }),
                            records: [
                                {
                                    id: 11,
                                    record_type: "thermo",
                                    record_id: 987654,
                                    status: "not_reviewed",
                                    created_at: "2026-09-14T10:00:00Z",
                                    // container_type/container_ref omitted entirely.
                                },
                            ],
                        },
                    ],
                    subject_total: 1,
                    record_total: 1,
                    offset: 0,
                    limit: 50,
                }),
            ),
        )
        renderPage()

        expect(await screen.findByText("spe_h2o")).toBeInTheDocument()
        expect(screen.getByText("Thermochemistry")).toBeInTheDocument()
    })

    it("re-reads a refused row, because a refusal usually means it moved", async () => {
        meIs(curator)
        // Another curator gets there first: our PATCH is refused, and the
        // next read shows the state they set. Asserted under "all" so
        // the status text is not suppressed by the filter-echo rule.
        let refused = false
        server.use(
            http.get(QUEUE, () =>
                HttpResponse.json(
                    queuePageBody([
                        subject([record({ status: refused ? "approved" : "not_reviewed" })]),
                    ]),
                ),
            ),
            http.patch(`${PATCH_BASE}/:type/:id`, () => {
                refused = true
                return HttpResponse.json(
                    { code: "domain_error", detail: "Transition not_reviewed -> rejected is not allowed." },
                    { status: 400 },
                )
            }),
        )
        const user = userEvent.setup()
        renderPage()
        await user.selectOptions(await screen.findByLabelText("Showing"), "all")
        const section = await subjectFor("spe_h2o")

        await user.click(reviewButtonIn(section))
        await user.selectOptions(screen.getByLabelText(/New review state/), "rejected")
        await user.type(screen.getByLabelText(/Why/), "wrong")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        expect(await screen.findByRole("alert")).toHaveTextContent(/is not allowed/)
        // Leaving the stale "not reviewed" on screen under a live control
        // invites the curator to try the same thing again against a state
        // the server has already left.
        await waitFor(() => expect(within(section).getByText("approved")).toBeInTheDocument())
    })

    it("is gone once the same row succeeds and leaves the view", async () => {
        meIs(curator)
        // Refused, then retried successfully. The row leaves the default
        // not_reviewed view because the retry moved it -- and the banner
        // must not then announce the OLD refusal about a write that has
        // just worked.
        let attempts = 0
        queueByStatus(() =>
            attempts >= 2
                ? [subject([record({ status: "approved" })])]
                : [subject([record({ status: "not_reviewed" })])],
        )
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, () => {
                attempts += 1
                if (attempts === 1) {
                    return HttpResponse.json(
                        { code: "domain_error", detail: "Someone else has it." },
                        { status: 400 },
                    )
                }
                return HttpResponse.json(record({ status: "approved" }))
            }),
        )
        const user = userEvent.setup()
        renderPage()
        const section = await subjectFor("spe_h2o")

        await user.click(reviewButtonIn(section))
        await user.type(screen.getByLabelText(/Why/), "first")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText(/Someone else has it/)

        // The form stays open after a non-saved refusal, so retry in it --
        // clicking "Review..." again would close it.
        await user.clear(screen.getByLabelText(/Why/))
        await user.type(screen.getByLabelText(/Why/), "second")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))

        await waitFor(() =>
            expect(screen.queryByText(/Someone else has it/)).not.toBeInTheDocument(),
        )
        expect(screen.queryByText(/no longer in this view/i)).not.toBeInTheDocument()
    })

    it("does not follow the curator to another page", async () => {
        meIs(curator)
        // The same rule as the filter, through the paging door: a refusal
        // is about a subject in a view, and a different page is a
        // different view. Without `offset` in the clearing effect the
        // banner rides along, naming a subject from page two as "no
        // longer in this view" while the curator reads page one.
        const fifty = Array.from({ length: 50 }, (_, i) =>
            subject([record({ id: 200 + i, record_id: 200 + i })], { subject_ref: `spe_p1_${i}` }),
        )
        server.use(
            http.get(QUEUE, ({ request }) => {
                const skip = Number(new URL(request.url).searchParams.get("skip") ?? 0)
                return HttpResponse.json(
                    skip === 0
                        ? queuePageBody(fifty, { subject_total: 51 })
                        : queuePageBody(
                              [subject([record({ id: 900, record_id: 900 })], { subject_ref: "spe_page_two" })],
                              { subject_total: 51, offset: skip },
                          ),
                )
            }),
            http.patch(`${PATCH_BASE}/:type/:id`, () =>
                HttpResponse.json(
                    { code: "service_unavailable", detail: "Page two refusal." },
                    { status: 503 },
                ),
            ),
        )
        const user = userEvent.setup()
        renderPage()
        await screen.findByText("spe_p1_0")

        await user.click(screen.getByRole("button", { name: "Older" }))
        await screen.findByText("spe_page_two")

        await user.click(reviewButtonIn(await subjectFor("spe_page_two")))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText(/Page two refusal/)

        await user.click(screen.getByRole("button", { name: "Newer" }))

        await waitFor(() =>
            expect(screen.queryByText(/Page two refusal/)).not.toBeInTheDocument(),
        )
    })

    it("does not follow the curator into another view", async () => {
        meIs(curator)
        // A refusal that leaves the row where it is (a 503, say). Change
        // filter and the subject is absent for a reason that has nothing
        // to do with the refusal -- announcing "no longer in this view"
        // there is simply false, and there was no way to dismiss it.
        queueByStatus(() => [subject([record({ status: "not_reviewed" })])])
        server.use(
            http.patch(`${PATCH_BASE}/:type/:id`, () =>
                HttpResponse.json(
                    { code: "service_unavailable", detail: "Try again shortly." },
                    { status: 503 },
                ),
            ),
        )
        const user = userEvent.setup()
        renderPage()
        const section = await subjectFor("spe_h2o")

        await user.click(reviewButtonIn(section))
        await user.type(screen.getByLabelText(/Why/), "fine")
        await user.click(screen.getByRole("button", { name: "Record this judgement" }))
        await screen.findByText(/Try again shortly/)

        await user.selectOptions(screen.getByLabelText("Showing"), "approved")

        await waitFor(() =>
            expect(screen.queryByText(/Try again shortly/)).not.toBeInTheDocument(),
        )
    })

    it("describes the state it is actually offering", async () => {
        // The hint and the select must agree: they are the two things a
        // curator reads before deciding. Starting from "approved" (not
        // the default not_reviewed) so the FIRST offered option -- not
        // just any option -- is asserted against its own hint.
        meIs(curator)
        queueIs([subject([record({ status: "approved" })])])
        renderPage()

        const user = userEvent.setup()
        await user.click(reviewButtonIn(await subjectFor("spe_h2o")))
        const select = screen.getByLabelText(/New review state/) as HTMLSelectElement

        expect(select.value).toBe("under_review")
        expect(screen.getByText(/No judgement is recorded yet/i)).toBeInTheDocument()
    })

    it("offers a control on every status a row can actually hold", async () => {
        // No status this page can render leaves a row with nothing to
        // click -- every member of ALL_STATUSES has at least one allowed
        // transition (see ALLOWED_TRANSITIONS), so "Review..." must
        // appear for each one, never "no transition available".
        for (const status of ["not_reviewed", "under_review", "approved", "rejected", "deprecated"] as const) {
            meIs(curator)
            queueIs([subject([record({ status })])])
            renderPage()

            const section = await subjectFor("spe_h2o")
            expect(within(section).getByRole("button", { name: "Review…" })).toBeEnabled()
            expect(within(section).queryByText(/no transition available/i)).not.toBeInTheDocument()

            cleanup()
        }
    })
})
