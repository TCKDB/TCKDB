import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it } from "vitest"
import { inlineQualifiers, levelKey, levelLine, qualifiersOf } from "../domain/levelLabel"
import { LevelLabel, LevelLabelScope } from "./LevelLabel"

const base = { method: "dlpno-ccsd(t)-f12", basis: "cc-pvtz-f12", level_of_theory_ref: "lot_aaaaaaaaaaaaaaaaaaaaaaaaaa" }
const frozen = { ...base, core_treatment: "frozen_core" }
const allElectron = { ...base, level_of_theory_ref: "lot_bbbbbbbbbbbbbbbbbbbbbbbbbb", core_treatment: "all_electron" }

function page(children: React.ReactNode) {
    return render(<MemoryRouter><LevelLabelScope>{children}</LevelLabelScope></MemoryRouter>)
}

afterEach(cleanup)

describe("LevelLabel", () => {
    it("shows method/basis and the program on one line, in the stored spelling", () => {
        page(<LevelLabel levelOfTheory={{ method: "DLPNO-CCSD(T)-F12", basis: "cc-pVTZ-F12", level_of_theory_ref: "lot_x" }} program="ORCA 6.0.0" />)
        expect(screen.getByRole("link")).toHaveTextContent("DLPNO-CCSD(T)-F12/cc-pVTZ-F12 · ORCA 6.0.0")
    })

    it("folds the qualifiers behind a toggle that opens a panel of short labelled pieces", () => {
        const { container } = page(<LevelLabel levelOfTheory={{ ...base, aux_basis: "cc-pvtz-f12-cabs", cabs_basis: "cc-pvtz-f12-cabs", dispersion: "d3bj", solvent: "water", spin_treatment: "restricted_open", core_treatment: "frozen_core" }} />)
        const toggle = screen.getByRole("button", { name: /^Details of/ })
        expect(toggle).toHaveAttribute("aria-expanded", "false")
        expect(container.querySelector(".level-label-panel")).toBeNull()
        fireEvent.click(toggle)
        expect(toggle).toHaveAttribute("aria-expanded", "true")
        const panel = container.querySelector(".level-label-panel")!
        expect([...panel.querySelectorAll(".level-label-piece-name")].map((name) => name.textContent)).toEqual(["aux", "cabs", "dispersion", "solvent", "spin", "core"])
        expect(panel.textContent).toContain("restricted open")
        expect(panel.textContent).toContain("frozen core")
        fireEvent.keyDown(toggle, { key: "Escape" })
        expect(container.querySelector(".level-label-panel")).toBeNull()
    })

    it("keeps the label first and the program once: the toggle follows the text and the program is not repeated", () => {
        const { container } = page(<li><LevelLabel levelOfTheory={{ ...base, core_treatment: "frozen_core" }} program="ORCA 6.0.0" /></li>)
        expect(container.querySelector("li")?.textContent).toBe("dlpno-ccsd(t)-f12/cc-pvtz-f12 · ORCA 6.0.0details")
        expect((container.textContent?.match(/ORCA 6\.0\.0/g) ?? []).length).toBe(1)
    })

    it("sits inside a paragraph without a nesting warning and opening it adds no text to the line before the toggle", () => {
        const { container } = page(<p>at <LevelLabel levelOfTheory={{ ...base, core_treatment: "frozen_core" }} /> done</p>)
        fireEvent.click(screen.getByRole("button", { name: /^Details of/ }))
        expect(container.querySelector("p")?.textContent?.startsWith("at dlpno-ccsd(t)-f12/cc-pvtz-f12details")).toBe(true)
    })

    it("has no toggle when the level states no qualifier", () => {
        page(<LevelLabel levelOfTheory={base} />)
        expect(screen.queryByRole("button")).toBeNull()
    })

    it("two levels that share a short label but differ in a qualifier never read alike", async () => {
        page(<><LevelLabel levelOfTheory={frozen} program="ORCA 6.0.0" /><LevelLabel levelOfTheory={allElectron} program="ORCA 6.0.0" /></>)
        const links = await screen.findAllByRole("link")
        const texts = links.map((link) => link.textContent)
        expect(texts).toEqual([
            "dlpno-ccsd(t)-f12/cc-pvtz-f12 · frozen core · ORCA 6.0.0",
            "dlpno-ccsd(t)-f12/cc-pvtz-f12 · all electron · ORCA 6.0.0",
        ])
        expect(new Set(texts).size).toBe(2)
    })

    it("prints a differing qualifier of any kind, not only core", async () => {
        page(<><LevelLabel levelOfTheory={{ ...base, level_of_theory_ref: "lot_disp", dispersion: "d3bj" }} /><LevelLabel levelOfTheory={base} /></>)
        const links = await screen.findAllByRole("link")
        expect(links.map((link) => link.textContent)).toEqual(["dlpno-ccsd(t)-f12/cc-pvtz-f12 · dispersion d3bj", "dlpno-ccsd(t)-f12/cc-pvtz-f12"])
    })

    it("adds nothing when the same level is shown twice, or when the labels already differ", async () => {
        page(<><LevelLabel levelOfTheory={frozen} /><LevelLabel levelOfTheory={frozen} /><LevelLabel levelOfTheory={{ method: "b3lyp", basis: "6-31g(d)", level_of_theory_ref: "lot_c", core_treatment: "all_electron" }} /></>)
        const links = await screen.findAllByRole("link")
        expect(links.map((link) => link.textContent)).toEqual([
            "dlpno-ccsd(t)-f12/cc-pvtz-f12",
            "dlpno-ccsd(t)-f12/cc-pvtz-f12",
            "b3lyp/6-31g(d)",
        ])
    })

    it("names each disclosure by the level it opens", () => {
        page(<LevelLabel levelOfTheory={{ ...base, dispersion: "d3bj" }} />)
        expect(screen.getByRole("button", { name: "Details of dlpno-ccsd(t)-f12/cc-pvtz-f12" })).toBeInTheDocument()
    })

    it("names two colliding levels' disclosures apart, by qualifier and program", async () => {
        page(<><LevelLabel levelOfTheory={{ ...frozen, dispersion: "d3bj" }} program="ORCA 6.0.0" /><LevelLabel levelOfTheory={{ ...allElectron, dispersion: "d3bj" }} program="ORCA 6.0.0" /></>)
        await screen.findAllByRole("link")
        const names = screen.getAllByText("details").map((summary) => summary.getAttribute("aria-label"))
        expect(names).toEqual([
            "Details of dlpno-ccsd(t)-f12/cc-pvtz-f12 · frozen core · ORCA 6.0.0",
            "Details of dlpno-ccsd(t)-f12/cc-pvtz-f12 · all electron · ORCA 6.0.0",
        ])
    })

    it("reads DLPNO with tight PNO differently from normal PNO, inline and in the details", async () => {
        const tight = { ...base, level_of_theory_ref: "lot_tight", pno_truncation: "tight", relativistic_hamiltonian: "dkh2", ri_approximation: "rijcosx" }
        const normal = { ...base, level_of_theory_ref: "lot_normal", pno_truncation: "normal", relativistic_hamiltonian: "dkh2", ri_approximation: "rijcosx" }
        const { container } = page(<><LevelLabel levelOfTheory={tight} /><LevelLabel levelOfTheory={normal} /></>)
        const links = await screen.findAllByRole("link")
        expect(links.map((link) => link.textContent)).toEqual([
            "dlpno-ccsd(t)-f12/cc-pvtz-f12 · PNO tight",
            "dlpno-ccsd(t)-f12/cc-pvtz-f12 · PNO normal",
        ])
        screen.getAllByRole("button", { name: /^Details of/ }).forEach((toggle) => fireEvent.click(toggle))
        const details = [...container.querySelectorAll(".level-label-panel")].map((element) => element.textContent)
        expect(details[0]).toContain("tight")
        expect(details[1]).toContain("normal")
        expect(details[0]).toContain("relativistic")
        expect(details[0]).toContain("RI")
        expect(details[0]).not.toEqual(details[1])
    })
})

describe("inlineQualifiers", () => {
    it("is empty for one level and for levels with different short labels", () => {
        expect(inlineQualifiers([frozen]).size).toBe(0)
        expect(inlineQualifiers([frozen, { ...base, basis: "cc-pvdz-f12" }]).size).toBe(0)
    })

    it("lists only the qualifiers that differ", () => {
        const a = { ...frozen, dispersion: "d3bj" }
        const b = { ...allElectron, dispersion: "d3bj" }
        const result = inlineQualifiers([a, b])
        expect(result.get(levelKey(a))?.map((q) => q.key)).toEqual(["core_treatment"])
        expect(result.get(levelKey(b))?.map((q) => q.key)).toEqual(["core_treatment"])
    })

    it("keys a level on its ref when it has one, and on its base label and qualifiers when it has none", () => {
        expect(levelKey({ ...base, dispersion: "d3bj" })).toBe(base.level_of_theory_ref)
        const unreferenced = { method: "b3lyp", basis: "6-31g(d)" }
        expect(levelKey(unreferenced)).not.toBe(levelKey({ ...unreferenced, dispersion: "d3bj" }))
        expect(levelKey(unreferenced)).toBe(levelKey({ ...unreferenced }))
    })

    it("writes the plain line of a level for a heading, with the qualifiers the page set names and then the program", () => {
        const a = { ...frozen, dispersion: "d3bj" }
        const b = { ...allElectron, dispersion: "d3bj" }
        const inline = inlineQualifiers([a, b])
        expect(levelLine(a, inline, "ORCA 6.0.0")).toBe("dlpno-ccsd(t)-f12/cc-pvtz-f12 · frozen core · ORCA 6.0.0")
        expect(levelLine(a, new Map())).toBe("dlpno-ccsd(t)-f12/cc-pvtz-f12")
    })

    it("builds qualifiers from the structured fields only", () => {
        const wire = { method: "x", label: "x (core=frozen_core)" }
        expect(qualifiersOf(wire)).toEqual([])
    })
})
