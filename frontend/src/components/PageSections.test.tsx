import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { PageSectionsProvider, SectionHeading } from "./PageSections"

afterEach(cleanup)

/**
 * SHOULD-FIX-6 ("record-page residuals" re-review): a section kicker
 * earns its place only when it adds a category the title itself lacks.
 * MEASURED: four `SectionHeading`s on `CalculationDetailPage`/
 * `GeometryDetailPage` passed their own title straight back in as
 * `kicker` ("Result" over "Result", "Structure" over "Structure view").
 * `SectionHeading` now suppresses a kicker equal (case-insensitively) to
 * the heading's own text, rather than trusting every call site to get
 * this right on its own.
 */
describe("SectionHeading suppresses a kicker equal to its own title", () => {
    it("does not render a kicker equal (case-insensitively) to the title text", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="Result">Result</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.getByRole("heading", { name: "Result" })).toBeVisible()
        expect(screen.queryByText("Result", { selector: ".section-kicker" })).toBeNull()
    })

    it("is case-insensitive ('STRUCTURE' over 'Structure view' still counts as redundant only when equal, not merely overlapping)", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="STRUCTURE VIEW">Structure view</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.queryByText("STRUCTURE VIEW", { selector: ".section-kicker" })).toBeNull()
    })

    it("still renders a kicker that adds a real category the title lacks", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="Deposited provenance">Software</SectionHeading>
            </PageSectionsProvider>,
        )
        expect(screen.getByText("Deposited provenance")).toBeVisible()
    })

    // Mutation check: a kicker that merely SHARES a word with the title
    // ("Structure" / "Structure view") must NOT be suppressed by this
    // rule -- only an EXACT (case-insensitive) match is redundant. If the
    // comparison were a substring/overlap check instead of equality, this
    // would wrongly drop a legitimate partial-overlap kicker too.
    it("does not suppress a kicker that is a strict prefix of the title, not equal to it", () => {
        render(
            <PageSectionsProvider>
                <SectionHeading id="s" kicker="Structure">Structure view</SectionHeading>
            </PageSectionsProvider>,
        )
        // This case is exactly why the four owned call sites drop the
        // prop outright rather than relying on this component alone --
        // see CalculationDetailPage.tsx/GeometryDetailPage.tsx's own
        // SHOULD-FIX-6 comments. The component-level guard only catches
        // an EXACT match; a near-duplicate like this one is a judgment
        // call made at the call site.
        expect(screen.getByText("Structure")).toBeVisible()
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
