import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { Lede } from "./Lede"
import { ValueLink } from "./ValueLink"
import { ValueSource } from "./ValueSource"
import { OrderNote } from "./OrderNote"
import { DepositorNote } from "./DepositorNote"
import { Breadcrumbs } from "./Breadcrumbs"
import { DEPOSITOR_SENTINEL, expectDepositorTextOnlyInNotes } from "../test/depositorSentinel"
import { ledeText, ledeWord } from "../domain/ledeText"
import blocksCss from "../record-blocks.css?raw"
import termCss from "../term.css?raw"

afterEach(cleanup)

const inRouter = (node: React.ReactNode) => render(<MemoryRouter>{node}</MemoryRouter>)

const OUTCOME = { converged: "converged", failed: "did not converge" } as const

describe("Lede", () => {
    it("renders a sentence built from fixed text, numbers and TCKDB wording as one paragraph", () => {
        const text = ledeText`A calculation that ${ledeWord("converged", OUTCOME)}, with ${3} frequencies.`
        inRouter(<Lede>{text}</Lede>)
        expect(screen.getByText("A calculation that converged, with 3 frequencies.").tagName).toBe("P")
    })

    it("never lets a code without TCKDB wording through as its own text", () => {
        expect(ledeText`Outcome: ${ledeWord(`${DEPOSITOR_SENTINEL}_x`, OUTCOME)}.`).toBe("Outcome: not recorded.")
        expect(ledeWord(null, OUTCOME)).toBe("not recorded")
        expect(ledeWord("toString", OUTCOME)).toBe("not recorded")
    })

    it("rejects a free string at compile time", () => {
        const fromDepositor: string = `${DEPOSITOR_SENTINEL}_note`
        // @ts-expect-error a plain string is not a LedeWord
        const sentence = ledeText`Note: ${fromDepositor}`
        expect(sentence).toBeDefined()
        // @ts-expect-error a plain string is not a LedeText
        render(<Lede>{fromDepositor}</Lede>)
    })
})

describe("ValueLink", () => {
    it("links the value and unit to the evidence, with the unit in a .u span that keeps its case", () => {
        inRouter(<ValueLink value="-74.5" unit="kJ/mol" to="/calculations/calc_a" />)
        const link = screen.getByRole("link")
        expect(link).toHaveAttribute("href", "/calculations/calc_a")
        expect(within(link).getByText("-74.5")).toHaveClass("value-link-number")
        const unit = within(link).getByText("kJ/mol")
        expect(unit).toHaveClass("u")
        expect(unit).toHaveClass("t-preserve-case")
    })

    it("is plain text, not a dead link, when no page exists for the evidence", () => {
        inRouter(<ValueLink value="12.3" unit="J/(mol K)" to={null} />)
        expect(screen.queryByRole("link")).toBeNull()
        expect(screen.getByText("12.3")).toBeVisible()
    })
})

describe("ValueSource", () => {
    const source = { recordType: "calculation", ref: "calc_a" }

    it("shows the source link, what it did, each level with its role, the programs and the review of that record", () => {
        inRouter(<ValueSource
            source={source}
            did="sp"
            levels={[{ role: "geometry", label: "wB97X-D/def2-TZVP" }, { role: "energy", label: "CCSD(T)-F12/cc-pVTZ-F12" }]}
            programs={["ORCA 5.0.3", "Molpro 2024.1"]}
            review="not_reviewed"
        />)
        const line = document.querySelector(".value-source") as HTMLElement
        expect(within(line).getByRole("link")).toHaveAttribute("href", "/calculations/calc_a")
        for (const text of ["Source", "single-point", "geometry wB97X-D/def2-TZVP", "energy CCSD(T)-F12/cc-pVTZ-F12", "ORCA 5.0.3, Molpro 2024.1", "Review of this record: not reviewed"]) {
            expect(line).toHaveTextContent(text)
        }
    })

    it("links a record type that has its own page", () => {
        inRouter(<ValueSource source={{ recordType: "species_entry", ref: "spe_a" }} />)
        expect(screen.getByRole("link")).toHaveAttribute("href", "/species-entries/spe_a")
    })

    it("links a thermo record to the species entry that shows it", () => {
        inRouter(<ValueSource source={{ recordType: "thermo", ref: "thm_a", container: { recordType: "species_entry", ref: "spe_a" } }} />)
        expect(screen.getByRole("link")).toHaveAttribute("href", "/species-entries/spe_a")
        expect(screen.getByRole("link")).toHaveTextContent("thm_a")
    })

    it("sends a transition state ref (ts_) nowhere it is not, and a transition state entry to its page", () => {
        inRouter(<ValueSource source={{ recordType: "transition_state", ref: "ts_a" }} />)
        expect(screen.queryByRole("link")).toBeNull()
        expect(document.querySelector(".value-source code")).toHaveTextContent("ts_a")
        cleanup()
        inRouter(<ValueSource source={{ recordType: "transition_state_entry", ref: "tse_a" }} />)
        expect(screen.getByRole("link")).toHaveAttribute("href", "/transition-state-entries/tse_a")
    })

    it("shows an unknown record type as a plain code, with no link", () => {
        inRouter(<ValueSource source={{ recordType: "something_new", ref: "xyz_a" }} />)
        expect(screen.queryByRole("link")).toBeNull()
        expect(document.querySelector(".value-source")?.textContent).toBe("Source xyz_a")
    })

    it.each([
        ["opt", "optimisation"], ["freq", "frequency"], ["sp", "single-point"], ["irc", "IRC"],
        ["scan", "scan"], ["path_search", "path search"], ["conf", "conformer"], ["composite", "composite"],
    ])("shows the calculation type %s as %s", (code, words) => {
        inRouter(<ValueSource source={source} did={code} />)
        expect(document.querySelector(".value-source")?.textContent).toBe(`Source calc_a · ${words}`)
    })

    it("omits a calculation type code it has no wording for, and never shows the raw code", () => {
        inRouter(<ValueSource source={source} did="DEPOSITOR_SENTINEL_kind" />)
        expect(document.querySelector(".value-source")?.textContent).toBe("Source calc_a")
        cleanup()
        inRouter(<ValueSource source={source} did="toString" />)
        expect(document.querySelector(".value-source")?.textContent).toBe("Source calc_a")
    })

    it("shows nothing for a part the record lacks and never invents one", () => {
        inRouter(<ValueSource source={source} />)
        const line = document.querySelector(".value-source") as HTMLElement
        expect(line.textContent).toBe("Source calc_a")
        expect(line.querySelector(".review-chip")).toBeNull()
    })

    it("treats empty levels, programs and review as missing", () => {
        inRouter(<ValueSource source={source} levels={[{ role: "energy", label: "" }]} programs={["", ""]} review="" />)
        expect(document.querySelector(".value-source")?.textContent).toBe("Source calc_a")
    })

    it("has a short form that is only a link", () => {
        inRouter(<ValueSource source={source} programs={["ORCA"]} compact />)
        const line = document.querySelector(".value-source") as HTMLElement
        expect(line).toHaveClass("value-source--compact")
        expect(line.textContent).toBe("from calc_a")
    })
})

describe("OrderNote", () => {
    it("says deposit order is not a recommendation and links to the guide", () => {
        inRouter(<OrderNote />)
        expect(document.querySelector(".order-note")).toHaveTextContent("Listed in deposit order. This order is not a recommendation.")
        expect(screen.getByRole("link")).toHaveAttribute("href", "/guide#order")
    })

    it("states another order truthfully when a list has one", () => {
        inRouter(<OrderNote order="Listed by review status, then newest first." />)
        const note = document.querySelector(".order-note") as HTMLElement
        expect(note).toHaveTextContent("Listed by review status, then newest first. This order is not a recommendation.")
        expect(note).not.toHaveTextContent("deposit order")
    })
})

describe("DepositorNote", () => {
    it("quotes the depositor's text under a visible label", () => {
        inRouter(<DepositorNote>{`${DEPOSITOR_SENTINEL}_note_1`}</DepositorNote>)
        const note = screen.getByLabelText("Note from the depositor")
        expect(within(note).getByText("Note from the depositor")).toBeVisible()
        expect(note.querySelector("blockquote")).toHaveTextContent(`${DEPOSITOR_SENTINEL}_note_1`)
        expect(note).toHaveTextContent("Written by the person who deposited this record; TCKDB shows it as given and has not checked it.")
    })

    it("keeps the depositor's text inside the quote and nowhere else", () => {
        inRouter(<><h2>Heading</h2><DepositorNote>{`${DEPOSITOR_SENTINEL}_note_2`}</DepositorNote></>)
        expectDepositorTextOnlyInNotes()
    })

    it("the helper catches the text in an attribute, a heading or a label", () => {
        inRouter(<><h2 title={`${DEPOSITOR_SENTINEL}_a`}>Heading</h2><DepositorNote>quote</DepositorNote></>)
        expect(() => expectDepositorTextOnlyInNotes()).toThrow()
        cleanup()
        inRouter(<p>{`${DEPOSITOR_SENTINEL}_b`}</p>)
        expect(() => expectDepositorTextOnlyInNotes()).toThrow()
    })

    it.each([null, undefined, ""])("renders nothing for %p", (value) => {
        inRouter(<DepositorNote>{value}</DepositorNote>)
        expect(document.querySelector(".depositor-note")).toBeNull()
    })
})

describe("Breadcrumbs", () => {
    it("is a labelled nav with parents as links and the last crumb marked as the current page", () => {
        inRouter(<Breadcrumbs crumbs={[{ label: "Molecules", to: "/species" }, { label: "Ethanol", to: "/species/spc_a" }, { label: "Entry" }]} />)
        const nav = screen.getByRole("navigation", { name: "Breadcrumb" })
        expect(within(nav).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/species", "/species/spc_a"])
        const current = nav.querySelector('[aria-current="page"]') as HTMLElement
        expect(current).toHaveTextContent("Entry")
        expect(nav.querySelectorAll("[aria-current]")).toHaveLength(1)
    })

    it("does not link the last crumb even when it has a path", () => {
        inRouter(<Breadcrumbs crumbs={[{ label: "Molecules", to: "/species" }, { label: "Here", to: "/here" }]} />)
        expect(screen.queryByRole("link", { name: "Here" })).toBeNull()
    })

    it("renders nothing for an empty list", () => {
        inRouter(<Breadcrumbs crumbs={[]} />)
        expect(screen.queryByRole("navigation")).toBeNull()
    })
})

function ruleFor(source: string, selector: string): string {
    for (const match of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const selectors = match[1].replace(/\/\*[\s\S]*?\*\//g, "").split(",").map((part) => part.trim())
        if (selectors.includes(selector)) return match[2]
    }
    throw new Error(`no rule for ${selector}`)
}

describe("links in the blocks look like links", () => {
    const LINKS = [".breadcrumbs a", ".order-note a", ".value-source a", ".value-link"]

    it.each(LINKS)("%s is accent coloured and underlined", (selector) => {
        const rule = ruleFor(blocksCss, selector)
        expect(rule).toMatch(/color:\s*var\(--accent\)/)
        expect(rule).toMatch(/text-decoration[^:]*:[^;]*underline/)
        expect(rule).toMatch(/text-decoration-color:\s*var\(--accent-underline\)/)
    })

    it.each(LINKS)("%s keeps the 3px focus ring", (selector) => {
        expect(ruleFor(blocksCss, `${selector}:focus-visible`)).toMatch(/outline:\s*3px solid var\(--focus-ring\)/)
    })

    it("the breadcrumb separator is hidden from screen readers", () => {
        expect(ruleFor(blocksCss, ".breadcrumbs li + li::before")).toMatch(/content:\s*"\\203A"\s*\/\s*""/)
    })
})

describe("a closed glossary popover is out of layout", () => {
    it("is display none unless open", () => {
        expect(ruleFor(termCss, ".term-popover")).toMatch(/display:\s*none/)
        expect(ruleFor(termCss, '.term-popover[data-open="true"]')).toMatch(/display:\s*block/)
    })
})
