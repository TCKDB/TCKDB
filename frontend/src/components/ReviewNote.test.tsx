import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import "../design-system.css"
import { ReviewNote } from "./ReviewNote"

afterEach(cleanup)

const NOTE =
    "Barriers are sound; bond-fission asymptotes are systematically low. " +
    "N2H4 -> 2 NH2 is 27.4 kJ/mol below the Active Thermochemical Tables value."

describe("ReviewNote", () => {
    it("renders the note text inside a disclosure when a note is present, collapsed by default", () => {
        const { container } = render(<ReviewNote note={NOTE} />)
        // It is a real disclosure, not an inline paragraph -- a reader can
        // collapse/expand it, and a thousands-of-characters note does not
        // sit uncollapsed on the page by default (jsdom, like a real
        // browser, does not render a closed <details>'s body content).
        const details = container.querySelector("details.disclosure") as HTMLDetailsElement
        expect(details).not.toBeNull()
        expect(details.open).toBe(false)
        expect(screen.getByText(NOTE)).not.toBeVisible()
    })

    it("shows the note text once the disclosure is opened", async () => {
        const user = userEvent.setup()
        render(<ReviewNote note={NOTE} />)
        await user.click(screen.getByText("Curator's review note", { exact: false }))
        expect(screen.getByText(NOTE)).toBeVisible()
    })

    // MUTATION TABLE (required, brief item 3): a record with NO note must
    // render NOTHING -- not an empty box, not a heading with no body. This
    // is the absence-vs-zero rule this codebase applies everywhere else.
    it("renders nothing at all when note is null", () => {
        const { container } = render(<ReviewNote note={null} />)
        expect(container).toBeEmptyDOMElement()
    })

    it("renders nothing at all when note is undefined", () => {
        const { container } = render(<ReviewNote note={undefined} />)
        expect(container).toBeEmptyDOMElement()
    })

    it("renders nothing for an empty string, the same as null -- an empty reason is still absent", () => {
        const { container } = render(<ReviewNote note="" />)
        expect(container).toBeEmptyDOMElement()
    })

    it("uses the given label as the disclosure summary", () => {
        render(<ReviewNote note={NOTE} label="Network solve review note" />)
        expect(screen.getByText("Network solve review note", { exact: false })).toBeVisible()
    })

    it("defaults to a generic label when none is given", () => {
        render(<ReviewNote note={NOTE} />)
        expect(screen.getByText("Curator's review note", { exact: false })).toBeVisible()
    })

    it("does not gate the note on any status -- the component takes no status prop at all", async () => {
        // There is no `status` prop on this component by design (see its
        // own docstring): a caller cannot accidentally wire it to only
        // show for `under_review`. This test pins the component's public
        // shape so that invariant cannot silently regress.
        const user = userEvent.setup()
        render(<ReviewNote note={NOTE} />)
        await user.click(screen.getByText("Curator's review note", { exact: false }))
        expect(screen.getByText(NOTE)).toBeVisible()
    })
})
