import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { ViewerStage } from "./ViewerStage"
import { PageShell } from "./PageShell"
import { Announcer } from "./Announcer"

const loaded = vi.hoisted(() => ({ count: 0 }))
vi.mock("3dmol", () => {
    loaded.count += 1
    return {}
})

// A stand-in viewer: reports the status the test sets and renders a focusable controls group once ready.
let nextStatus: "ready" | "unavailable" = "ready"
vi.mock("./GeometryViewer", async () => {
    const React = await import("react")
    return {
        GeometryViewer: ({ formula, onStatusChange }: { formula: string; onStatusChange?: (s: "ready" | "unavailable") => void }) => {
            React.useEffect(() => { onStatusChange?.(nextStatus) }, [onStatusChange])
            return nextStatus === "ready"
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

    it("preloads the 3D library on hover or focus, before the button is pressed", async () => {
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
        expect(await screen.findByText(/The 3D view could not start in this browser/)).toBeInTheDocument()
        expect(screen.getByRole("img", { name: "Ball-and-stick picture of CH" })).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: "Show in 3D" })).toBeNull()
        expect(container.querySelector(".geometry-viewer, .viewer-canvas")).toBeNull()
        act(() => {})
    })
})
