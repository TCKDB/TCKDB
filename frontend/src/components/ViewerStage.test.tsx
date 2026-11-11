import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { ViewerStage } from "./ViewerStage"
import { PageShell } from "./PageShell"
import { Announcer } from "./Announcer"
import stageSource from "./ViewerStage.tsx?raw"
import stageCss from "../viewer-stage.css?raw"

const loaded = vi.hoisted(() => ({ count: 0 }))
vi.mock("3dmol", () => {
    loaded.count += 1
    return {}
})

// A stand-in viewer: reports the status the test sets and renders a focusable controls group once ready.
let nextStatus: "loading" | "ready" | "unavailable" = "ready"
vi.mock("./GeometryViewer", async () => {
    const React = await import("react")
    return {
        GeometryViewer: ({ formula, onStatusChange }: { formula: string; onStatusChange?: (s: "loading" | "ready" | "unavailable") => void }) => {
            React.useEffect(() => { onStatusChange?.(nextStatus) }, [onStatusChange])
            return nextStatus !== "unavailable"
                ? <div className="geometry-viewer" data-testid={`live-${formula}`}>
                    <div className="viewer-controls" role="group" aria-label={`Rotate ${formula}`} tabIndex={-1}><button type="button">Zoom in</button></div>
                </div>
                : null
        },
    }
})

const ATOMS = [
    { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
    { atom_index: 2, element: "H", x: 0, y: 0, z: 1.09 },
]

beforeEach(() => { nextStatus = "ready" })
afterEach(cleanup)

function stage(formula: string) {
    return <ViewerStage atoms={ATOMS} formula={formula} xyzText={null} />
}

describe("ViewerStage", () => {
    it("shows the picture and a Show in 3D button first, with no live viewer", () => {
        render(<MemoryRouter><PageShell>{stage("CH")}</PageShell></MemoryRouter>)
        expect(screen.getByRole("img", { name: "Ball-and-stick picture of CH" })).toBeInTheDocument()
        expect(screen.getByRole("button", { name: "Show in 3D" })).toBeInTheDocument()
        expect(screen.queryByTestId("live-CH")).toBeNull()
    })

    it("preloads the 3D library on hover, before the button is pressed", async () => {
        const user = userEvent.setup()
        render(<MemoryRouter><PageShell>{stage("CH")}</PageShell></MemoryRouter>)
        expect(loaded.count).toBe(0)
        await user.hover(screen.getByRole("button", { name: "Show in 3D" }))
        await waitFor(() => expect(loaded.count).toBe(1))
        expect(screen.queryByTestId("live-CH")).toBeNull()
    })

    it("mounts the viewer on Enter from the keyboard and moves focus to the viewer controls", async () => {
        const user = userEvent.setup()
        render(<MemoryRouter><PageShell>{stage("CH")}</PageShell></MemoryRouter>)
        await user.tab()
        expect(screen.getByRole("button", { name: "Show in 3D" })).toHaveFocus()
        await user.keyboard("{Enter}")
        expect(screen.getByTestId("live-CH")).toBeInTheDocument()
        await waitFor(() => expect(screen.getByRole("group", { name: "Rotate CH" })).toHaveFocus())
    })

    it("puts focus on Show picture while the viewer loads", async () => {
        nextStatus = "loading"
        const user = userEvent.setup()
        render(<MemoryRouter><PageShell>{stage("CH")}</PageShell></MemoryRouter>)
        await user.click(screen.getByRole("button", { name: "Show in 3D" }))
        expect(screen.getByRole("button", { name: "Show picture" })).toHaveFocus()
    })

    it("returns focus to Show in 3D after Show picture, but not after being evicted", async () => {
        const user = userEvent.setup()
        render(<MemoryRouter><PageShell>{stage("AAA")}{stage("BBB")}</PageShell></MemoryRouter>)
        await user.click(screen.getAllByRole("button", { name: "Show in 3D" })[0])
        await user.click(screen.getByRole("button", { name: "Show picture" }))
        await waitFor(() => expect(screen.getAllByRole("button", { name: "Show in 3D" })[0]).toHaveFocus())
        await user.click(screen.getAllByRole("button", { name: "Show in 3D" })[0])
        await user.click(screen.getAllByRole("button", { name: "Show in 3D" })[0])
        expect(screen.getByTestId("live-BBB")).toBeInTheDocument()
        expect(screen.getAllByRole("button", { name: "Show in 3D" })[0]).not.toHaveFocus()
    })

    it("keeps one live viewer per page: opening a second returns the first to its picture and announces it", async () => {
        const user = userEvent.setup()
        render(
            <MemoryRouter>
                <Announcer />
                <PageShell>{stage("AAA")}{stage("BBB")}</PageShell>
            </MemoryRouter>,
        )
        const [first, second] = screen.getAllByRole("button", { name: "Show in 3D" })
        await user.click(first)
        expect(screen.getByTestId("live-AAA")).toBeInTheDocument()
        await user.click(second)
        expect(screen.getByTestId("live-BBB")).toBeInTheDocument()
        expect(screen.queryByTestId("live-AAA")).toBeNull()
        expect(screen.getByRole("img", { name: "Ball-and-stick picture of AAA" })).toBeInTheDocument()
        await waitFor(() => expect(document.querySelector(".announcer")).toHaveTextContent(/3D view of AAA was closed/))
    })

    it("keeps focus off the evicting stage's button in the reverse order too", async () => {
        const user = userEvent.setup()
        render(<MemoryRouter><PageShell>{stage("AAA")}{stage("BBB")}</PageShell></MemoryRouter>)
        await user.click(screen.getAllByRole("button", { name: "Show in 3D" })[1])
        expect(screen.getByTestId("live-BBB")).toBeInTheDocument()
        await user.click(screen.getAllByRole("button", { name: "Show in 3D" })[0])
        expect(screen.getByTestId("live-AAA")).toBeInTheDocument()
        expect(screen.queryByTestId("live-BBB")).toBeNull()
        expect(screen.getByRole("button", { name: "Show in 3D" })).not.toHaveFocus()
    })

    it("outlines the focused failure sentence with the focus ring", () => {
        expect(stageCss).toMatch(/\.empty-projection:focus-visible\s*\{[^}]*outline:\s*3px solid var\(--focus-ring\)[^}]*outline-offset:\s*2px/)
    })

    it("says bonds are not drawn for very large molecules", () => {
        const many = Array.from({ length: 401 }, (_, i) => ({ atom_index: i + 1, element: "C", x: i * 3, y: 0, z: 0 }))
        render(<MemoryRouter><PageShell><ViewerStage atoms={many} formula="C401" xyzText={null} /></PageShell></MemoryRouter>)
        expect(screen.getByText("Bonds are not drawn for molecules this large.")).toBeInTheDocument()
    })

    it("imports the viewer's canvas sizing itself, so it works on pages that do not", () => {
        expect(stageSource).toContain('import "../geometry-viewer.css"')
    })

    it("returns to the picture on Show picture", async () => {
        const user = userEvent.setup()
        render(<MemoryRouter><PageShell>{stage("CH")}</PageShell></MemoryRouter>)
        await user.click(screen.getByRole("button", { name: "Show in 3D" }))
        await user.click(screen.getByRole("button", { name: "Show picture" }))
        expect(screen.queryByTestId("live-CH")).toBeNull()
        expect(screen.getByRole("button", { name: "Show in 3D" })).toBeInTheDocument()
    })

    it("keeps the picture and gives one plain sentence when WebGL cannot start, with no empty viewer box", async () => {
        nextStatus = "unavailable"
        const user = userEvent.setup()
        const { container } = render(<MemoryRouter><PageShell>{stage("CH")}</PageShell></MemoryRouter>)
        await user.click(screen.getByRole("button", { name: "Show in 3D" }))
        const sentence = await screen.findByText("The interactive 3D view could not start in this browser; the picture shows the same stored coordinates.")
        expect(sentence).toHaveFocus()
        expect(sentence.textContent).not.toMatch(/WebGL|table/)
        expect(screen.getByRole("img", { name: "Ball-and-stick picture of CH" })).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: "Show in 3D" })).toBeNull()
        expect(container.querySelector(".geometry-viewer, .viewer-canvas")).toBeNull()
        act(() => {})
    })
})
