import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { ListMeta } from "../api/listEnvelope"
import { ListPager } from "./ListPager"

afterEach(cleanup)

function meta(window: { offset: number; limit: number; returned: number; total: number }, extra: Partial<ListMeta> = {}): ListMeta {
    return { pagination: window, sort: null, hiddenByDefault: null, ...extra }
}

describe("ListPager: which records are on screen", () => {
    it("first page of several: says 1-50 of 120, Previous disabled, Next asks for offset 50", () => {
        const onPage = vi.fn()
        render(<ListPager meta={meta({ offset: 0, limit: 50, returned: 50, total: 120 })} noun="record" onPage={onPage} />)
        expect(screen.getByText("Showing 1–50 of 120 records.")).toBeVisible()
        expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()
        fireEvent.click(screen.getByRole("button", { name: "Next" }))
        expect(onPage).toHaveBeenCalledWith({ offset: 50, limit: 50 })
    })

    it("middle page: both buttons work and Previous goes back one window", () => {
        const onPage = vi.fn()
        render(<ListPager meta={meta({ offset: 50, limit: 50, returned: 50, total: 120 })} noun="record" onPage={onPage} />)
        expect(screen.getByText("Showing 51–100 of 120 records.")).toBeVisible()
        fireEvent.click(screen.getByRole("button", { name: "Previous" }))
        expect(onPage).toHaveBeenLastCalledWith({ offset: 0, limit: 50 })
        fireEvent.click(screen.getByRole("button", { name: "Next" }))
        expect(onPage).toHaveBeenLastCalledWith({ offset: 100, limit: 50 })
    })

    it("last page (short): says 101-120 of 120 and Next is disabled", () => {
        render(<ListPager meta={meta({ offset: 100, limit: 50, returned: 20, total: 120 })} noun="record" onPage={() => {}} />)
        expect(screen.getByText("Showing 101–120 of 120 records.")).toBeVisible()
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()
        expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled()
    })

    it("a list that fits one page shows no buttons and no range line", () => {
        render(<ListPager meta={meta({ offset: 0, limit: 50, returned: 3, total: 3 })} noun="record" onPage={() => {}} />)
        expect(screen.queryByRole("button")).not.toBeInTheDocument()
        expect(screen.queryByText(/Showing/)).not.toBeInTheDocument()
    })

    it("an exact multiple of the page size ends on the last full page, not a phantom empty one", () => {
        render(<ListPager meta={meta({ offset: 50, limit: 50, returned: 50, total: 100 })} noun="record" onPage={() => {}} />)
        expect(screen.getByText("Showing 51–100 of 100 records.")).toBeVisible()
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()
    })

    it("total 0: says there are none and offers no paging", () => {
        render(<ListPager meta={meta({ offset: 0, limit: 50, returned: 0, total: 0 })} noun="thermo record" onPage={() => {}} />)
        expect(screen.getByText("No thermo records.")).toBeVisible()
        expect(screen.queryByRole("button")).not.toBeInTheDocument()
    })

    it("a window past the end is not reported as an empty list, and Previous gets the reader back", () => {
        const onPage = vi.fn()
        render(<ListPager meta={meta({ offset: 150, limit: 50, returned: 0, total: 120 })} noun="record" onPage={onPage} />)
        expect(screen.getByText("No records at this position; 120 in all.")).toBeVisible()
        fireEvent.click(screen.getByRole("button", { name: "Previous" }))
        expect(onPage).toHaveBeenCalledWith({ offset: 100, limit: 50 })
    })

    it("uses the given plural", () => {
        render(<ListPager meta={meta({ offset: 0, limit: 2, returned: 2, total: 5 })} noun="reaction entry" nounPlural="reaction entries" onPage={() => {}} />)
        expect(screen.getByText("Showing 1–2 of 5 reaction entries.")).toBeVisible()
    })

    it("disables both buttons while a page is being fetched", () => {
        render(<ListPager meta={meta({ offset: 50, limit: 50, returned: 50, total: 120 })} noun="record" onPage={() => {}} busy />)
        expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()
    })
})

describe("ListPager: what the server said about the list", () => {
    it("names the records the default hid, by status, even on a list that fits one page", () => {
        render(<ListPager
            meta={meta({ offset: 0, limit: 50, returned: 3, total: 3 }, { hiddenByDefault: { counts: { rejected: 2, deprecated: 1 }, include_with: {}, counted: "records" } })}
            noun="record"
            onPage={() => {}}
        />)
        expect(screen.getByText("Not listed by default: 2 rejected and 1 deprecated records.")).toBeVisible()
    })

    it("says nothing about hidden records when none were hidden", () => {
        render(<ListPager
            meta={meta({ offset: 0, limit: 50, returned: 3, total: 3 }, { hiddenByDefault: { counts: { rejected: 0, deprecated: 0 }, include_with: {}, counted: "records" } })}
            noun="record"
            onPage={() => {}}
        />)
        expect(screen.queryByText(/Not listed by default/)).not.toBeInTheDocument()
    })

    it("counts what the server says it counted (species entries, not records)", () => {
        render(<ListPager
            meta={meta({ offset: 0, limit: 50, returned: 1, total: 1 }, { hiddenByDefault: { counts: { rejected: 4 }, include_with: {}, counted: "species_entries" } })}
            noun="record"
            onPage={() => {}}
        />)
        expect(screen.getByText("Not listed by default: 4 rejected species entries.")).toBeVisible()
    })

    it("states the display order in the server's own words, without naming internal tie-break keys, as an order for browsing and not a ranking", () => {
        render(<ListPager
            meta={meta({ offset: 0, limit: 50, returned: 3, total: 3 }, {
                sort: { keys: [
                        { key: "review_rank", direction: "asc", meaning: "review status: approved first; under_review and not_reviewed rank equally; deprecated, then rejected, after them" },
                        { key: "created_at", direction: "desc", meaning: "deposition time, the row's created_at: when it was deposited in TCKDB, newest first" },
                        { key: "id", direction: "desc", meaning: "unique tie-breaker that makes the order total (the internal row id); no scientific meaning" },
                    ] },
            })}
            noun="record"
            onPage={() => {}}
        />)
        expect(screen.getByText("Display order: approved first, then newest deposited first. An order for browsing, not a ranking or a recommendation.")).toBeVisible()
        expect(screen.queryByText(/tie-breaker/)).not.toBeInTheDocument()
        expect(screen.queryByText(/\bid\b/)).not.toBeInTheDocument()
    })

    it("adds the direction when the server's wording does not state it", () => {
        render(<ListPager
            meta={meta({ offset: 0, limit: 50, returned: 3, total: 3 }, {
                sort: { keys: [{ key: "method", direction: "asc", meaning: "level-of-theory method name, alphabetical" }] },
            })}
            noun="record"
            onPage={() => {}}
        />)
        expect(screen.getByText(/level-of-theory method name, alphabetical \(ascending\)/)).toBeVisible()
    })

    it("an order the request asked for is reported as requested and is never called 'not a ranking'", () => {
        render(<ListPager
            meta={meta({ offset: 0, limit: 50, returned: 3, total: 3 }, {
                sort: {
                    requested_by: "ranking=lowest_energy",
                    keys: [{ key: "energy_hartree", direction: "asc", meaning: "electronic energy in hartree, lowest first; records with no energy last" }],
                },
            })}
            noun="record"
            onPage={() => {}}
        />)
        expect(screen.getByText("Order requested (ranking=lowest_energy): electronic energy in hartree, lowest first.")).toBeVisible()
        expect(screen.queryByText(/not a ranking/)).not.toBeInTheDocument()
    })

    it("makes no order statement when the server made none", () => {
        render(<ListPager meta={meta({ offset: 0, limit: 50, returned: 3, total: 3 })} noun="record" onPage={() => {}} />)
        expect(screen.queryByText(/Display order/)).not.toBeInTheDocument()
    })
})
