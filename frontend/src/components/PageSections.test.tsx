import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { PageSectionsProvider, SectionHeading } from "./PageSections"

afterEach(cleanup)

/**
 * SHOULD-FIX-6 ("record-page residuals" re-review, widened on the
 * re-review pass): a section kicker earns its place only when it adds a
 * category the title itself lacks. MEASURED: four `SectionHeading`s on
 * `CalculationDetailPage`/`GeometryDetailPage` passed their own title
 * straight back in as `kicker` ("Result" over "Result"). A reviewer then
 * caught the equality-only version of this rule missing the same defect
 * in near-restated form ("Structure" over "Structure view", "Review" over
 * "Review history") -- widened from exact equality to "kicker is a
 * case-insensitive prefix of the title" (equality is the trivial case of
 * that). `SectionHeading` enforces this itself rather than trusting every
 * call site to get it right on its own.
 */
describe("SectionHeading suppresses a kicker that is a prefix of its own title", () => {
    it("does not render a kicker equal (case-insensitively) to the title text", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="Result">Result</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.getByRole("heading", { name: "Result" })).toBeVisible()
        expect(screen.queryByText("Result", { selector: ".section-kicker" })).toBeNull()
    })

    it("is case-insensitive", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="STRUCTURE VIEW">Structure view</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.queryByText("STRUCTURE VIEW", { selector: ".section-kicker" })).toBeNull()
    })

    // The case an equality-only rule missed (re-review finding): a kicker
    // that is only the title's OWN leading words still reads as a plain
    // restatement once stacked visually ("REVIEW" / "Review history").
    it("suppresses a kicker that is a leading-words prefix of the title, not just an exact match", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="Review">Review history</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.getByRole("heading", { name: "Review history" })).toBeVisible()
        expect(screen.queryByText("Review", { selector: ".section-kicker" })).toBeNull()
    })

    it("suppresses 'Structure' over 'Structure view' and 'Raw' over 'Raw XYZ' (the exact defects a reviewer caught)", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="a" kicker="Structure">Structure view</SectionHeading>
                <SectionHeading id="b" kicker="Raw">Raw XYZ</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.queryByText("Structure", { selector: ".section-kicker" })).toBeNull()
        expect(screen.queryByText("Raw", { selector: ".section-kicker" })).toBeNull()
    })

    it("still renders a kicker that adds a real category the title lacks", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="Deposited provenance">Software</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.getByText("Deposited provenance")).toBeVisible()
    })

    // Direction matters: the title being a prefix of the KICKER (the
    // opposite case) is a legitimate category-then-instance pairing, not
    // a restatement -- "Reaction context" ahead of the h2 "Reaction"
    // (TransitionStateEntryPage) reads as "category, then specific name",
    // not "the same word twice". Only kicker-prefixes-title trips the
    // guard.
    it("does not suppress a kicker the title is a prefix OF (the reverse direction is not a restatement)", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="Reaction context">Reaction</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.getByText("Reaction context")).toBeVisible()
    })

    // Mutation check: a kicker that merely SHARES a word with the title
    // somewhere in the middle, without being a leading prefix of it, must
    // NOT be suppressed -- only a match anchored at the title's own start
    // is a restatement. If the comparison were "kicker appears anywhere
    // in the title" instead of "title starts with kicker", this would
    // wrongly drop a legitimate kicker too.
    it("does not suppress a kicker that only shares a word with the title, not a leading prefix of it", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="Stage evidence">Machine-parsed stage evidence</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.getByText("Stage evidence")).toBeVisible()
    })

    it("renders no kicker paragraph at all when kicker is omitted", () => {
        const { container } = render(
            <PageSectionsProvider>
                <SectionHeading id="s">Review history</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(container.querySelector(".section-kicker")).toBeNull()
    })
})
