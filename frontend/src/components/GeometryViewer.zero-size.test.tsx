import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { GeometryViewer } from "./GeometryViewer"

afterEach(cleanup)

/**
 * CORRECTION (post-review): this file's earlier docstring claimed it was
 * "the one environment that exercises 3Dmol's actual WebGL-unavailable
 * code path for real" — i.e. that jsdom's lack of a real WebGL context
 * made `$3Dmol.createViewer()` throw here. That was never true, and is
 * exactly the kind of stale/wrong coverage claim this repo has been
 * bitten by before: jsdom also never performs layout, so
 * `containerRef.current.offsetWidth`/`offsetHeight` are always 0, and
 * `GeometryViewer`'s size guard (see its module docstring) intercepts
 * *before* `import("3dmol")` is ever reached — `createViewer` is never
 * called in this file at all. jsdom additionally has no `ResizeObserver`
 * global (checked directly: `typeof ResizeObserver === "undefined"`
 * here), so the component takes its "no way to ever detect a resize"
 * fallback and reports `unavailable` immediately, without ever loading
 * 3Dmol.
 *
 * That is a real, distinct failure path worth testing on its own (a
 * container that never gets laid out, on a browser too old for
 * `ResizeObserver`, must still resolve to an honest status rather than
 * hang on "loading" forever) — but it is NOT the WebGL-context-creation
 * failure. That one is exercised for real in
 * `GeometryViewer.test.tsx`, in the "a genuine WebGL failure
 * (createViewer throws — not the zero-size path)" block, where a mocked
 * `createViewer` throws exactly as real 3Dmol does with no WebGL context
 * (see the module docstring in `GeometryViewer.tsx` for the call chain:
 * `Renderer#initGL()` swallows a failed `getContext`, then
 * `setDefaultGLState()` throws on the now-undefined context, and
 * `createViewer` re-throws that).
 */
describe("GeometryViewer — zero-size container, no ResizeObserver to notice a later resize", () => {
    it("shows an honest status message instead of hanging or crashing, with no canvas left half-initialised", async () => {
        expect(typeof (globalThis as Record<string, unknown>).ResizeObserver).toBe("undefined")
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
