import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Link, MemoryRouter } from "react-router-dom"
import { SUPPRESS_SCROLL_SPY_EVENT, useScrollToHash } from "./useScrollToHash"

function Page() {
    useScrollToHash()
    return (
        <>
            <Link to="/p#inside">go inside</Link>
            <Link to="/p#late">go late</Link>
            <details data-testid="closed"><summary>more</summary><p id="inside">target</p></details>
            <p id="other">elsewhere</p>
        </>
    )
}

afterEach(cleanup)

describe("useScrollToHash", () => {
    it("opens a closed disclosure around the target, focuses it, and holds the contents list's scroll-spy", async () => {
        const user = userEvent.setup()
        let held = 0
        window.addEventListener(SUPPRESS_SCROLL_SPY_EVENT, () => { held += 1 })
        render(<MemoryRouter initialEntries={["/p"]}><Page /></MemoryRouter>)
        await user.click(screen.getByText("go inside"))
        expect((screen.getByTestId("closed") as HTMLDetailsElement).open).toBe(true)
        expect(document.getElementById("inside")).toHaveFocus()
        expect(held).toBe(1)
    })

    it("works again for a second click on the same link", async () => {
        const user = userEvent.setup()
        render(<MemoryRouter initialEntries={["/p"]}><Page /></MemoryRouter>)
        await user.click(screen.getByText("go inside"))
        expect(document.getElementById("inside")).toHaveFocus()
        document.getElementById("other")?.setAttribute("tabindex", "-1")
        document.getElementById("other")?.focus()
        await user.click(screen.getByText("go inside"))
        expect(document.getElementById("inside")).toHaveFocus()
    })

    it("finds a target that mounts after the navigation", async () => {
        const user = userEvent.setup()
        render(<MemoryRouter initialEntries={["/p"]}><Page /></MemoryRouter>)
        await user.click(screen.getByText("go late"))
        const late = document.createElement("p")
        late.id = "late"
        document.body.appendChild(late)
        await new Promise((resolve) => setTimeout(resolve, 20))
        expect(late).toHaveFocus()
        late.remove()
    })
})
