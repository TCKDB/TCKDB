import { afterEach, describe, expect, it } from "vitest"
import { act, cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Term } from "./Term"
import { GLOSSARY } from "../domain/glossary"

afterEach(cleanup)

function popover() {
    return screen.getByRole("tooltip", { hidden: true })
}

describe("Term", () => {
    it("is a button whose description is in the DOM from the start and resolves by aria-describedby", () => {
        render(<p>A <Term term="conformer">conformer</Term> is a shape.</p>)
        const button = screen.getByRole("button", { name: "conformer" })
        expect(button).toHaveAttribute("type", "button")
        const id = button.getAttribute("aria-describedby")
        expect(id).toBeTruthy()
        expect(document.getElementById(id!)).toBe(popover())
        expect(popover()).toHaveTextContent(GLOSSARY.conformer.definition)
        expect(popover()).toHaveAttribute("data-open", "false")
    })

    it("opens on keyboard focus and closes on Esc with focus kept on the button", async () => {
        const user = userEvent.setup()
        render(<Term term="transition-state" />)
        await user.tab()
        const button = screen.getByRole("button", { name: "Transition state" })
        expect(button).toHaveFocus()
        expect(popover()).toHaveAttribute("data-open", "true")
        await user.keyboard("{Escape}")
        expect(popover()).toHaveAttribute("data-open", "false")
        expect(button).toHaveFocus()
    })

    it("opens on a tap and closes on a tap elsewhere", async () => {
        const user = userEvent.setup()
        render(<><Term term="network" /><button type="button">elsewhere</button></>)
        await user.click(screen.getByRole("button", { name: "Network" }))
        expect(popover()).toHaveAttribute("data-open", "true")
        await user.click(screen.getByRole("button", { name: "elsewhere" }))
        expect(popover()).toHaveAttribute("data-open", "false")
    })

    it("stays open while the pointer moves from the term onto its explanation", async () => {
        const user = userEvent.setup()
        render(<Term term="species" />)
        await user.hover(screen.getByRole("button", { name: "Species" }))
        expect(popover()).toHaveAttribute("data-open", "true")
        await user.hover(popover())
        await new Promise((resolve) => setTimeout(resolve, 250))
        expect(popover()).toHaveAttribute("data-open", "true")
        await user.unhover(popover())
        await new Promise((resolve) => setTimeout(resolve, 250))
        expect(popover()).toHaveAttribute("data-open", "false")
    })

    it("closes on Esc pressed anywhere in the document after a hover-only open", async () => {
        const user = userEvent.setup()
        render(<Term term="species" />)
        await user.hover(screen.getByRole("button", { name: "Species" }))
        expect(popover()).toHaveAttribute("data-open", "true")
        expect(screen.getByRole("button", { name: "Species" })).not.toHaveFocus()
        await act(async () => { document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })) })
        expect(popover()).toHaveAttribute("data-open", "false")
    })

    it("takes free text and an accessible name for a mark, and uses a given id for the description", () => {
        render(<Term note="Matches either side." label="About SMILES" popoverId="smiles-hint">?</Term>)
        const button = screen.getByRole("button", { name: "About SMILES" })
        expect(button).toHaveTextContent("?")
        expect(button).toHaveAttribute("aria-describedby", "smiles-hint")
        expect(popover()).toHaveAttribute("id", "smiles-hint")
        expect(popover()).toHaveTextContent("Matches either side.")
    })

    it("has a glossary entry, with a definition, for every key", () => {
        for (const entry of Object.values(GLOSSARY)) {
            expect(entry.term.length).toBeGreaterThan(0)
            expect(entry.definition.length).toBeGreaterThan(20)
        }
        expect(Object.keys(GLOSSARY)).toHaveLength(15)
    })
})
