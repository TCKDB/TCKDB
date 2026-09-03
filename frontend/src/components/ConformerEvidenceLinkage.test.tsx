import { afterEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { ConformerEvidenceLinkage } from "./ConformerEvidenceLinkage"

afterEach(cleanup)

// Deliberately DIFFERENT numbers at every level -- 3 observations, 7
// calculation rows (3 opt/2 freq/2 sp) in 2 optimization chains, 2 distinct
// geometries (one produced by 4 calculation outputs, one by 3) -- so a
// mutation that reads the wrong field (e.g. printing the chain count where
// the row count belongs, or the coverage count where the raw type count
// belongs) produces a value distinguishable from every other number in the
// fixture, not one that happens to coincide. The three top-level step
// counts (3 / 7 / 2) are ALSO mutually distinct, so a bare-number query is
// unambiguous about which step it read -- but see `step()` below: every
// assertion in this file binds a count to its unit inside the SAME step,
// because two independent `getByText` calls do NOT prove the two belong
// together (a review round caught exactly that gap: swapping the
// observations and geometries counts left every prior assertion green).
function conformer(overrides: Partial<ConformerProjection> = {}): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" },
        observations_summary: { total: 3 },
        evidence_summary: {
            calculation_count: 7,
            optimization_chain_count: 2,
            geometry_count: 2,
            evidence_coverage: { opt: 2, freq: 2, sp: 1 },
            levels_of_theory: {},
        },
        observations: [],
        calculations: [
            { calculation_ref: "c1", type: "opt" }, { calculation_ref: "c2", type: "opt" }, { calculation_ref: "c3", type: "opt" },
            { calculation_ref: "c4", type: "freq" }, { calculation_ref: "c5", type: "freq" },
            { calculation_ref: "c6", type: "sp" }, { calculation_ref: "c7", type: "sp" },
        ],
        geometries: [
            { calculation_ref: "c1", geometry: { geometry_ref: "geom_a" } },
            { calculation_ref: "c2", geometry: { geometry_ref: "geom_a" } },
            { calculation_ref: "c3", geometry: { geometry_ref: "geom_a" } },
            { calculation_ref: "c4", geometry: { geometry_ref: "geom_a" } },
            { calculation_ref: "c5", geometry: { geometry_ref: "geom_b" } },
            { calculation_ref: "c6", geometry: { geometry_ref: "geom_b" } },
            { calculation_ref: "c7", geometry: { geometry_ref: "geom_b" } },
        ],
        ...overrides,
    } as ConformerProjection
}

// The stable test hook (`data-linkage-step`, `ConformerEvidenceLinkage.tsx`)
// scopes a query to ONE step's own DOM subtree, so a count and its unit (or
// its detail text) can be asserted as belonging to the SAME step, not just
// present somewhere on the page.
function step(kind: "observations" | "calculations" | "geometries"): HTMLElement {
    const el = document.querySelector(`[data-linkage-step="${kind}"]`)
    if (!el) throw new Error(`no rendered step for "${kind}"`)
    return el as HTMLElement
}

// The mechanics (`linkage-flow`, stage coverage) live inside a `<details>`
// collapsed by default -- opening it is now part of getting to that content,
// the same way a real reader would click the summary. Every test below that
// inspects the flow/coverage opens it first via this helper.
function openMechanics(): HTMLDetailsElement {
    const details = document.querySelector(".evidence-linkage-detail") as HTMLDetailsElement
    fireEvent.click(within(details).getByText(/How this evidence connects/))
    return details
}

describe("ConformerEvidenceLinkage", () => {
    it("labels the heading with the conformer's own display label (auto-numbered basin rendered as 'Conformer Group N')", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(screen.getByRole("heading", { name: "Evidence for Conformer Group 1" })).toBeVisible()
    })

    it("shows only the staging sentence WITHOUT opening anything -- the ONE fact the card doesn't already state", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        const story = document.querySelector(".evidence-linkage-story")
        expect(story).toBeVisible()
        // 3 observations; `observations: []` means the per-observation calc
        // lists were never loaded, so this fixture cannot use the safe
        // per-observation attribution -- it falls back to the aggregate-safe
        // sentence, which still directly answers "does the opt count include
        // the pre-opt" without naming which sighting was staged.
        expect(story).toHaveTextContent(
            "Three optimisation calculations are on file across two independent optimisation chains -- one of "
            + "those calculations is a coarse pass later refined within the same chain, though the archive "
            + "does not say which sighting they belong to.",
        )
        // The lead ("sighted N times") and the freq/sp coverage sentences are
        // ALL already on the conformer card (in a different form) -- they
        // must not be repeated here.
        expect(story).not.toHaveTextContent(/sighted/)
        expect(story).not.toHaveTextContent(/frequency calculation/)
        expect(story).not.toHaveTextContent(/single-point energy/)
    })

    it("drops the separate 'Evidence' eyebrow -- the picker above and the tab strip below already carry one", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        expect(document.querySelector(".evidence-linkage .eyebrow")).not.toBeInTheDocument()
    })

    // Finding 14: the card above prints "11 obs" / "opt 7/11 obs" with no
    // expansion anywhere on the page -- this mechanics note is the one
    // place "obs" (and "calc") ever gets tied back to its full word, so it
    // must actually say so, not just define "observation" in isolation.
    it("ties the card's 'obs'/'calc' abbreviations back to their full words, inside the mechanics disclosure", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        openMechanics()
        const note = document.querySelector(".evidence-linkage-note")
        expect(note).toHaveTextContent(
            "the card above abbreviates the first two as \"obs\" and \"calc\": an observation is a deposited "
            + "sighting of this basin",
        )
    })

    it("renders no prose paragraph at all when there is no staging fact to add beyond the card's own counts", () => {
        // `calculations: null` -> `optimizationStaging` returns "unknown" ->
        // no staging sentence exists at all, so the trimmed prose has
        // nothing left to show -- the paragraph should not render an empty
        // shell.
        const noBreakdown = conformer({ calculations: null } as Partial<ConformerProjection>)
        render(<ConformerEvidenceLinkage conformer={noBreakdown} />)
        expect(document.querySelector(".evidence-linkage-story")).not.toBeInTheDocument()
    })

    it("collapses the mechanics disclosure by default, with a summary that previews what's behind it", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        const details = document.querySelector(".evidence-linkage-detail") as HTMLDetailsElement
        expect(details.open).toBe(false)
        expect(within(details).getByText("How this evidence connects (7 calculation rows, 2 distinct geometries)")).toBeVisible()
    })

    it("binds the observation count to its OWN step and unit -- swapping it with the geometry count is caught here", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        openMechanics()
        expect(within(step("observations")).getByText("3")).toBeVisible()
        expect(within(step("observations")).getByText("deposited observations")).toBeVisible()
        expect(within(step("observations")).getByText("each a separate sighting of this basin")).toBeVisible()
        // Not present in this step under a swap: the OTHER two steps' own counts.
        expect(within(step("observations")).queryByText("7")).not.toBeInTheDocument()
        expect(within(step("observations")).queryByText("2")).not.toBeInTheDocument()
    })

    it("binds the calculation-row total, its own opt/freq/sp breakdown, and the (different) chain count to the SAME step", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        openMechanics()
        expect(within(step("calculations")).getByText("7")).toBeVisible()
        expect(within(step("calculations")).getByText("calculation rows")).toBeVisible()
        // Breakdown by TYPE (3 opt/2 freq/2 sp) is the raw row count --
        // different from the 2 optimization CHAINS reported alongside it.
        expect(within(step("calculations")).getByText(
            "3 opt · 2 freq · 2 sp, in 2 optimisation chains (a staged coarse-then-fine reoptimisation counts as one chain)",
        )).toBeVisible()
    })

    it("binds the distinct-geometry count to its OWN step, and shows how many calculation outputs converge on EACH one", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        openMechanics()
        const geometries = step("geometries")
        expect(within(geometries).getByText("2")).toBeVisible()
        expect(within(geometries).getByText("distinct stored geometries")).toBeVisible()
        // Not present in this step under a swap: the observation count.
        expect(within(geometries).queryByText("3")).not.toBeInTheDocument()
        expect(within(geometries).getByText("geom_a")).toBeVisible()
        expect(within(geometries).getByText(/4 calculation outputs/)).toBeVisible()
        expect(within(geometries).getByText("geom_b")).toBeVisible()
        expect(within(geometries).getByText(/3 calculation outputs/)).toBeVisible()
    })

    it("prints the archive's PUBLISHED calculation_count even when the row breakdown hasn't loaded -- never recomputed from a missing list", () => {
        // `calculation_count: 7` is still published; `calculations` (the
        // list the breakdown is derived from) is null, e.g. not yet
        // fetched. A component that recomputes the total from the
        // breakdown instead of trusting the published field would print 0
        // here, silently disagreeing with the archive's own count.
        const noBreakdown = conformer({ calculations: null } as Partial<ConformerProjection>)
        render(<ConformerEvidenceLinkage conformer={noBreakdown} />)
        openMechanics()
        expect(within(step("calculations")).getByText("7")).toBeVisible()
        expect(within(step("calculations")).getByText("breakdown not loaded")).toBeVisible()
        expect(within(step("calculations")).queryByText(/opt/)).not.toBeInTheDocument()
        expect(within(step("calculations")).queryByText("no calculation rows recorded")).not.toBeInTheDocument()
    })

    it("says 'no calculation rows recorded' only when the archive's OWN published count is genuinely zero", () => {
        const zero = conformer({
            evidence_summary: {
                calculation_count: 0, optimization_chain_count: 0, geometry_count: 0,
                evidence_coverage: { opt: 0, freq: 0, sp: 0 }, levels_of_theory: {},
            },
            calculations: [],
        })
        render(<ConformerEvidenceLinkage conformer={zero} />)
        openMechanics()
        expect(within(step("calculations")).getByText("no calculation rows recorded")).toBeVisible()
        expect(within(step("calculations")).queryByText("breakdown not loaded")).not.toBeInTheDocument()
    })

    it("prints the archive's PUBLISHED geometry_count even when the geometry links haven't loaded -- never recomputed from a missing list", () => {
        // `geometry_count: 2` is still published; `geometries` (the link
        // list the convergence breakdown is derived from) is null. A
        // component that derives the count from the links instead of
        // trusting the published field would print 0 here.
        const noLinks = conformer({ geometries: null } as Partial<ConformerProjection>)
        render(<ConformerEvidenceLinkage conformer={noLinks} />)
        openMechanics()
        expect(within(step("geometries")).getByText("2")).toBeVisible()
        expect(within(step("geometries")).getByText("breakdown not loaded")).toBeVisible()
        expect(within(step("geometries")).queryByText("geom_a")).not.toBeInTheDocument()
    })

    it("labels stage coverage as a share of the 3 OBSERVATIONS, not of the 7 calculation rows", () => {
        render(<ConformerEvidenceLinkage conformer={conformer()} />)
        openMechanics()
        const coverage = screen.getByText(/Stage coverage/).closest("p") as HTMLElement
        expect(coverage).toHaveTextContent("opt 2/3 · freq 2/3 · sp 1/3")
        expect(coverage).toHaveTextContent(
            "This counts which observations have at least one calculation of that stage, not the number of calculation rows.",
        )
    })

    it("uses singular wording at exactly one, for every unit independently", () => {
        const single = conformer({
            observations_summary: { total: 1 },
            evidence_summary: {
                calculation_count: 1,
                optimization_chain_count: 1,
                geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 0, sp: 0 },
                levels_of_theory: {},
            },
            calculations: [{ calculation_ref: "c1", type: "opt" }] as ConformerProjection["calculations"],
            geometries: [{ calculation_ref: "c1", geometry: { geometry_ref: "geom_solo" } }] as ConformerProjection["geometries"],
        })
        render(<ConformerEvidenceLinkage conformer={single} />)
        openMechanics()
        expect(within(step("observations")).getByText("deposited observation")).toBeVisible()
        expect(within(step("calculations")).getByText("calculation row")).toBeVisible()
        expect(within(step("geometries")).getByText("distinct stored geometry")).toBeVisible()
        expect(within(step("calculations")).getByText(/1 optimisation chain\b/)).toBeVisible()
        expect(within(step("geometries")).getByText(/1 calculation output\b/)).toBeVisible()
    })

    it("renders a depositor-chosen label verbatim, never coerced into 'Conformer Group N'", () => {
        const named = conformer({ conformer_group: { conformer_group_ref: "cg_x", label: "anti-periplanar" } })
        render(<ConformerEvidenceLinkage conformer={named} />)
        expect(screen.getByRole("heading", { name: "Evidence for anti-periplanar" })).toBeVisible()
    })

    it("falls back to the group's own ref for a blank/whitespace-only label, never an empty heading", () => {
        const blank = conformer({ conformer_group: { conformer_group_ref: "cg_blank", label: "   " } })
        render(<ConformerEvidenceLinkage conformer={blank} />)
        expect(screen.getByRole("heading", { name: "Evidence for cg_blank" })).toBeVisible()
    })

    // --- The CH3 case from the brief, verbatim -- the safe per-observation
    // attribution path (chainCount === coverageOpt AND every observation's
    // own calculation list is loaded), matching the live archive's own
    // spe_bcbdjwkip75yoziblpntwzblzu / cg_rsoqvj37biuvkucdr6dpaba6iy. ---
    function ch3Conformer(): ConformerProjection {
        function observation(ref: string, types: string[]) {
            return {
                conformer_observation: { conformer_observation_ref: ref },
                calculations: types.map((type, index) => ({ calculation_ref: `${ref}_${index}`, type })),
            } as ConformerProjection["observations"] extends (infer T)[] | null | undefined ? T : never
        }
        return conformer({
            observations_summary: { total: 4 },
            evidence_summary: {
                calculation_count: 14,
                optimization_chain_count: 4,
                geometry_count: 2,
                evidence_coverage: { opt: 4, freq: 4, sp: 3, geometry_validation: 4, scf_stability: 0 },
                levels_of_theory: {},
            },
            observations: [
                observation("co_w6u6yzwblmv7iq7t3fan2cfap4", ["opt", "opt", "freq", "sp"]),
                observation("co_mcszoxsfahmkqsfil323o64bou", ["opt", "opt", "freq", "sp"]),
                observation("co_abrdh2kjzdg7p7mvk5qdn7yq4e", ["opt", "opt", "freq", "sp"]),
                observation("co_3xt5t5cwjrdsc4ibdcvuck2p5y", ["opt", "freq"]),
            ],
            calculations: [
                ...Array.from({ length: 3 }, (_, index) => [
                    { calculation_ref: `g${index}_opt1`, type: "opt" },
                    { calculation_ref: `g${index}_opt2`, type: "opt" },
                    { calculation_ref: `g${index}_freq`, type: "freq" },
                    { calculation_ref: `g${index}_sp`, type: "sp" },
                ]).flat(),
                { calculation_ref: "g3_opt1", type: "opt" },
                { calculation_ref: "g3_freq", type: "freq" },
            ] as ConformerProjection["calculations"],
        })
    }

    it("tells CH3's real story: three sightings optimised in two stages, one in a single pass", () => {
        render(<ConformerEvidenceLinkage conformer={ch3Conformer()} />)
        const story = document.querySelector(".evidence-linkage-story")
        expect(story).toHaveTextContent(
            "Three were optimised in two stages, one in a single pass. "
            + "A staged optimisation runs a coarse pass first, then refines it.",
        )
        expect(story).not.toHaveTextContent(/sighted/)
        expect(story).not.toHaveTextContent(/frequency calculation/)
        expect(story).not.toHaveTextContent(/single-point energy/)
    })

    it("never claims staging for a conformer whose optimizations are all standalone single passes (aggregate path)", () => {
        // No `observations` loaded -- the aggregate-only path -- with raw opt
        // rows exactly equal to the chain count, i.e. genuinely no staging
        // anywhere in this basin.
        const noStaging = conformer({
            observations_summary: { total: 2 },
            evidence_summary: {
                calculation_count: 2,
                optimization_chain_count: 2,
                geometry_count: 2,
                evidence_coverage: { opt: 2, freq: 0, sp: 0 },
                levels_of_theory: {},
            },
            calculations: [
                { calculation_ref: "co_a_opt", type: "opt" },
                { calculation_ref: "co_b_opt", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        render(<ConformerEvidenceLinkage conformer={noStaging} />)
        const story = document.querySelector(".evidence-linkage-story")
        expect(story).toHaveTextContent(
            "Two optimisation calculations are on file, one per chain -- no chain was staged in more than one pass.",
        )
        // "staged" itself appears in the honest NEGATIVE sentence ("no
        // chain was staged in more than one pass"), so only the AFFIRMATIVE
        // multi-stage phrasing is the thing that must not appear.
        expect(story).not.toHaveTextContent(/\bstages\b/)
        expect(story).toHaveTextContent(/no chain was staged in more than one pass/)
        expect(story).not.toHaveTextContent(/sighted/)
    })

    it("never claims staging for a conformer whose optimizations are all standalone single passes (per-observation path)", () => {
        // Every observation's own calculation list IS loaded here, and the
        // chain count equals the observations-with-opt count -- the SAFE
        // per-observation attribution path. Each observation has exactly
        // one opt row, so this must read as "optimised in a single pass"
        // for every one of them, never "stages".
        function observation(ref: string) {
            return {
                conformer_observation: { conformer_observation_ref: ref },
                calculations: [{ calculation_ref: `${ref}_opt`, type: "opt" }],
            } as ConformerProjection["observations"] extends (infer T)[] | null | undefined ? T : never
        }
        const noStaging = conformer({
            observations_summary: { total: 2 },
            evidence_summary: {
                calculation_count: 2,
                optimization_chain_count: 2,
                geometry_count: 2,
                evidence_coverage: { opt: 2, freq: 0, sp: 0 },
                levels_of_theory: {},
            },
            observations: [observation("co_a"), observation("co_b")],
            calculations: [
                { calculation_ref: "co_a_opt", type: "opt" },
                { calculation_ref: "co_b_opt", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        render(<ConformerEvidenceLinkage conformer={noStaging} />)
        const story = document.querySelector(".evidence-linkage-story")
        expect(story).toHaveTextContent("Two were optimised in a single pass.")
        expect(story).not.toHaveTextContent(/\bstages\b/)
        expect(story).not.toHaveTextContent(/\bstaged\b/)
        expect(story).not.toHaveTextContent(/sighted/)
    })

    // Finding 3 of the BLOCK review: this test used to assert the SEQUENTIAL
    // reading ("optimised in three stages" + "a coarse pass first, then
    // refines it") for a 3-row/1-chain observation, and in doing so
    // ENSHRINED an inference the wire cannot support rather than guarding
    // against it. The backend's `_feeds_a_refinement_on_the_same_observation`
    // (backend/app/services/scientific_read/conformers.py:645-666) collapses
    // on ANY `optimized_from` parent without constraining the child, so this
    // exact fixture -- 3 opt rows, 1 chain, 1 observation -- is
    // byte-identical on the wire whether it is a genuine coarse->medium->fine
    // SEQUENCE or two independent coarse attempts (A->C, B->C) both refined
    // into the same final geometry in PARALLEL. Per that same backend
    // docstring, the deployed database has no chain longer than two nodes
    // today, making the parallel reading the MORE probable one for this
    // shape, not a rare edge case to hedge against. The prose must say only
    // what three rows in one chain actually proves -- that they belong to a
    // single chain -- and must NOT claim a specific pass count or a
    // coarse-then-fine sequence it cannot distinguish from parallel
    // attempts.
    it("says three rows belong to a single chain, without asserting a sequence the wire cannot distinguish from parallel attempts", () => {
        function observation(ref: string, optCount: number) {
            return {
                conformer_observation: { conformer_observation_ref: ref },
                calculations: Array.from({ length: optCount }, (_, index) => ({ calculation_ref: `${ref}_opt${index}`, type: "opt" })),
            } as ConformerProjection["observations"] extends (infer T)[] | null | undefined ? T : never
        }
        const threeRowsOneChain = conformer({
            observations_summary: { total: 1 },
            evidence_summary: {
                calculation_count: 3,
                optimization_chain_count: 1,
                geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 0, sp: 0 },
                levels_of_theory: {},
            },
            observations: [observation("co_only", 3)],
            calculations: [
                { calculation_ref: "co_only_opt0", type: "opt" },
                { calculation_ref: "co_only_opt1", type: "opt" },
                { calculation_ref: "co_only_opt2", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        render(<ConformerEvidenceLinkage conformer={threeRowsOneChain} />)
        const story = document.querySelector(".evidence-linkage-story")
        expect(story).toHaveTextContent("One was optimised in three calculations belonging to a single chain.")
        // Neither a specific step count nor the coarse-then-fine SEQUENCE
        // gloss may appear -- both assert more than three rows folded into
        // one chain actually proves.
        expect(story).not.toHaveTextContent(/\btwo stages\b/)
        expect(story).not.toHaveTextContent(/\bthree stages\b/)
        expect(story).not.toHaveTextContent(/coarse pass first, then refines it/)
        expect(story).not.toHaveTextContent(/sighted/)
    })

    // The two-row case is the one shape where "two stages" and "a coarse
    // pass first, then refines it" are still licensed: with exactly two
    // rows in one chain there is only one possible relationship between
    // them (one refines the other) -- the parallel-attempts ambiguity only
    // opens up at three rows and above. This guards that the finding-3 fix
    // didn't overcorrect and start suppressing the sequential gloss here
    // too.
    it("still names two stages and the sequential gloss for a genuine two-row chain", () => {
        function observation(ref: string, optCount: number) {
            return {
                conformer_observation: { conformer_observation_ref: ref },
                calculations: Array.from({ length: optCount }, (_, index) => ({ calculation_ref: `${ref}_opt${index}`, type: "opt" })),
            } as ConformerProjection["observations"] extends (infer T)[] | null | undefined ? T : never
        }
        const twoRowsOneChain = conformer({
            observations_summary: { total: 1 },
            evidence_summary: {
                calculation_count: 2,
                optimization_chain_count: 1,
                geometry_count: 1,
                evidence_coverage: { opt: 1, freq: 0, sp: 0 },
                levels_of_theory: {},
            },
            observations: [observation("co_only", 2)],
            calculations: [
                { calculation_ref: "co_only_opt0", type: "opt" },
                { calculation_ref: "co_only_opt1", type: "opt" },
            ] as ConformerProjection["calculations"],
        })
        render(<ConformerEvidenceLinkage conformer={twoRowsOneChain} />)
        const story = document.querySelector(".evidence-linkage-story")
        expect(story).toHaveTextContent(
            "One was optimised in two stages. A staged optimisation runs a coarse pass first, then refines it.",
        )
        expect(story).not.toHaveTextContent(/sighted/)
    })

    it("never leaks the 'sighted N times' lead sentence into the trimmed prose, regardless of observation count", () => {
        // The full paragraph `describeConformerEvidence` builds always
        // opens with "This conformer was sighted N times." -- the count is
        // already the conformer card's own line-1 number, so it must never
        // reach the trimmed `.evidence-linkage-story`, for any N.
        render(<ConformerEvidenceLinkage conformer={conformer({ observations_summary: { total: 5 } })} />)
        const story = document.querySelector(".evidence-linkage-story")
        expect(story).not.toHaveTextContent(/sighted/)
        expect(story).not.toHaveTextContent(/sighted once/)
    })
})
