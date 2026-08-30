import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { GeometryViewer } from "./GeometryViewer"

afterEach(cleanup)

/**
 * Deliberately does NOT mock the "3dmol" module — jsdom (this project's
 * test environment) implements `HTMLCanvasElement.getContext` for the 2D
 * context only; `getContext('webgl'|'webgl2'|'experimental-webgl')`
 * genuinely returns `null` here, exactly as it would in a real browser
 * with WebGL disabled, blocked, or exhausted. That makes this the one
 * environment that exercises 3Dmol's actual WebGL-unavailable code path
 * for real, rather than through a stand-in.
 *
 * Per the module docstring in `GeometryViewer.tsx`: `$3Dmol.createViewer`
 * throws synchronously in this situation, but from inside this
 * component's own `useEffect`, not during render — so a render-only
 * error boundary (`SectionErrorBoundary`, exercised separately in
 * `GeometryDetailPage.errorBoundary.test.tsx`) is not the mechanism doing
 * the catching here. This test pins the local, explicit handling instead.
 */
describe("GeometryViewer — WebGL unavailable", () => {
    it("shows an honest status message instead of hanging or crashing, with no canvas left half-initialised", async () => {
        render(
            <GeometryViewer
                atoms={[
                    { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
                    { atom_index: 2, element: "H", x: 0, y: 0, z: 1.09 },
                ]}
                formula="CH"
                xyzText="2\n\nC 0.000000 0.000000 0.000000\nH 0.000000 0.000000 1.090000"
            />,
        )

        // `findByRole("status")` alone would match the transient "Loading
        // the 3D view…" status too and could resolve on that one before
        // the effect settles — wait on the specific failure text instead.
        const status = await screen.findByText(/could not be initialised/)
        expect(status).toHaveAttribute("role", "status")
        expect(status).toHaveTextContent(
            "The 3D view could not be initialised — this browser or environment may not support WebGL.",
        )

        // The container div this component renders into is left in place
        // (not removed), so page layout does not jump — but it carries no
        // accessible content of its own, since it is a supplementary
        // picture the failure state explains in prose instead.
        const container = document.querySelector(".viewer-canvas")
        expect(container).not.toBeNull()
        expect(container).toHaveAttribute("aria-hidden", "true")
        expect(container).toHaveAttribute("data-viewer-status", "unavailable")
    })

    it("never leaves the section stuck on the loading message", async () => {
        render(
            <GeometryViewer
                atoms={[{ atom_index: 1, element: "C", x: 0, y: 0, z: 0 }]}
                formula="C"
                xyzText="1\n\nC 0.000000 0.000000 0.000000"
            />,
        )

        await screen.findByText(/could not be initialised/)
        expect(screen.queryByText(/Loading the 3D view/)).toBeNull()
    })
})
