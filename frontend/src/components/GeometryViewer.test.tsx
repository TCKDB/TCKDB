import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { GeometryViewer } from "./GeometryViewer"

/**
 * These tests exercise the "3Dmol succeeded" path, which needs two things
 * jsdom does not provide on its own:
 *
 *  1. A working WebGL context — jsdom has none at all (see
 *     `GeometryViewer.webgl-unavailable.test.tsx`, which tests the real,
 *     unmocked zero-size path instead — a genuinely different failure mode
 *     from the one below). "3dmol" is mocked here with a fake viewer that
 *     records what it was called with, so these tests can pin the exact
 *     data 3Dmol receives without needing real WebGL.
 *  2. A non-zero-size container — jsdom never lays elements out, so
 *     `offsetWidth`/`offsetHeight` are always 0 (see the size-check
 *     comment in `GeometryViewer.tsx`), which this component treats as
 *     "wait for a resize" rather than a WebGL failure. `stubOffsetSize`
 *     below overrides both on `HTMLElement.prototype` for the lifetime of
 *     each test so the component takes the "container is real" branch
 *     immediately, without needing a ResizeObserver in play.
 *
 * Kept in its own file from `GeometryViewer.webgl-unavailable.test.tsx`
 * for the same reason `GeometryDetailPage.errorBoundary.test.tsx` is
 * split out from `GeometryDetailPage.test.tsx`: `vi.mock` is hoisted to
 * the top of the file and would otherwise apply to every test in it,
 * including the ones that specifically need the real, unmocked module.
 */

type FakeViewerCalls = {
    createViewer: unknown[][]
    addModel: unknown[][]
    setStyle: unknown[][]
    zoomTo: unknown[][]
    spin: unknown[][]
    animate: unknown[][]
    render: unknown[][]
    clear: unknown[][]
    rotate: unknown[][]
    getView: unknown[][]
    setView: unknown[][]
}

const calls: FakeViewerCalls = {
    createViewer: [],
    addModel: [],
    setStyle: [],
    zoomTo: [],
    spin: [],
    animate: [],
    render: [],
    clear: [],
    rotate: [],
    getView: [],
    setView: [],
}

/** The view "pinned" by the fake viewer's getView() — arbitrary but fixed, so Reset view's setView() call can be pinned exactly. */
const FAKE_INITIAL_VIEW = [1, 2, 3, 4, 0.1, 0.2, 0.3, 0.4]

let createViewerShouldThrow = false

function resetCalls() {
    for (const key of Object.keys(calls) as (keyof FakeViewerCalls)[]) calls[key] = []
    createViewerShouldThrow = false
}

vi.mock("3dmol", () => ({
    createViewer: (...args: unknown[]) => {
        calls.createViewer.push(args)
        if (createViewerShouldThrow) {
            // Mirrors what real 3Dmol does with no WebGL context: throws
            // synchronously from inside createViewer (see the module
            // docstring in GeometryViewer.tsx for the exact call chain).
            throw new Error("error creating viewer: TypeError: Cannot read properties of null (reading 'clearDepth')")
        }
        // Mirrors 3Dmol appending its own canvas to the container it was
        // given (GLViewer.ts's initContainer: `this.container.append(...)`)
        // — real enough to let a test assert that teardown actually
        // removes it, not just that clear() was called.
        const container = args[0] as HTMLElement
        const canvas = document.createElement("canvas")
        canvas.setAttribute("data-testid", "fake-3dmol-canvas")
        container.appendChild(canvas)
        return {
            addModel: (...a: unknown[]) => { calls.addModel.push(a) },
            setStyle: (...a: unknown[]) => { calls.setStyle.push(a) },
            zoomTo: (...a: unknown[]) => { calls.zoomTo.push(a) },
            spin: (...a: unknown[]) => { calls.spin.push(a) },
            animate: (...a: unknown[]) => { calls.animate.push(a) },
            render: (...a: unknown[]) => { calls.render.push(a) },
            clear: (...a: unknown[]) => { calls.clear.push(a) },
            rotate: (...a: unknown[]) => { calls.rotate.push(a) },
            getView: (...a: unknown[]) => { calls.getView.push(a); return FAKE_INITIAL_VIEW },
            setView: (...a: unknown[]) => { calls.setView.push(a) },
        }
    },
}))

/** jsdom reports 0 for both on every element; 3Dmol needs a real size. */
function stubNonZeroContainerSize() {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 320 })
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 320 })
}

let restoreOffsetWidth: PropertyDescriptor | undefined
let restoreOffsetHeight: PropertyDescriptor | undefined

beforeEach(() => {
    restoreOffsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth")
    restoreOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight")
    stubNonZeroContainerSize()
    resetCalls()
})

afterEach(() => {
    cleanup()
    if (restoreOffsetWidth) Object.defineProperty(HTMLElement.prototype, "offsetWidth", restoreOffsetWidth)
    if (restoreOffsetHeight) Object.defineProperty(HTMLElement.prototype, "offsetHeight", restoreOffsetHeight)
})

const CH_XYZ = "2\n\nC 0.000000 0.000000 0.000000\nH 0.000000 0.000000 1.090000"
const CH_ATOMS = [
    { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
    { atom_index: 2, element: "H", x: 0, y: 0, z: 1.09 },
]

describe("GeometryViewer", () => {
    it("passes the archive's own xyz_text to 3Dmol unmodified — the picture and the raw XYZ block read the same string", async () => {
        render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
        await waitFor(() => expect(calls.addModel).toHaveLength(1))
        expect(calls.addModel[0][0]).toBe(CH_XYZ)
        expect(calls.addModel[0][1]).toBe("xyz")
    })

    it("falls back to a synthesized XYZ block, built from the same atom rows as the coordinate table, when xyz_text is absent — and that block matches the raw atom data exactly", async () => {
        render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={null} />)
        await waitFor(() => expect(calls.addModel).toHaveLength(1))
        expect(calls.addModel[0][0]).toBe(
            "2\n\nC 0 0 0\nH 0 0 1.09",
        )
    })

    it("passes nomouse:true to createViewer — 3Dmol's default wheel/touch handlers would otherwise trap page scroll on the canvas", async () => {
        render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
        await waitFor(() => expect(calls.createViewer).toHaveLength(1))
        const config = calls.createViewer[0][1] as Record<string, unknown>
        expect(config.nomouse).toBe(true)
    })

    it("actually styles the model — an unstyled 3Dmol model renders nothing visible, so setStyle must be called with a non-empty style", async () => {
        render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
        await waitFor(() => expect(calls.setStyle).toHaveLength(1))
        expect(calls.setStyle[0][0]).toEqual({})
        expect(calls.setStyle[0][1]).toEqual({ stick: { radius: 0.14 }, sphere: { scale: 0.28 } })
    })

    it("never calls spin or animate, and calls zoomTo with no animation duration — reduced motion is the only possible outcome, not a media-query branch", async () => {
        render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
        await waitFor(() => expect(calls.render).toHaveLength(1))
        expect(calls.spin).toHaveLength(0)
        expect(calls.animate).toHaveLength(0)
        expect(calls.zoomTo).toHaveLength(1)
        expect(calls.zoomTo[0]).toEqual([])
    })

    it("marks the WebGL canvas container as a supplementary picture, not the accessible representation", async () => {
        render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
        const container = document.querySelector(".viewer-canvas")
        expect(container).toHaveAttribute("aria-hidden", "true")
        await waitFor(() => expect(container).toHaveAttribute("data-viewer-status", "ready"))
    })

    it("discloses that bonds are inferred from interatomic distance, not deposited data", () => {
        render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
        // A distinct sentence from the "interactive 3D view" framing
        // sentence in the same paragraph, so a mutation that deletes only
        // this sentence cannot hide behind an assertion on the other one.
        expect(screen.getByText(/An interactive 3D view/).textContent).toMatch(
            /Bonds shown are inferred from interatomic distance for legibility only; they are not part of the deposited record\./,
        )
    })

    describe("a genuine WebGL failure (createViewer throws — not the zero-size path)", () => {
        it("reports the honest unavailable status instead of crashing or hanging", async () => {
            createViewerShouldThrow = true
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const status = await screen.findByText(/could not be initialised/)
            expect(status).toHaveAttribute("role", "status")
            const container = document.querySelector(".viewer-canvas")
            expect(container).toHaveAttribute("data-viewer-status", "unavailable")
        })
    })

    describe("rotate/reset controls (nomouse:true means these are the only way to move the view)", () => {
        it("renders no controls until the viewer is actually ready", () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            expect(screen.queryByRole("group")).toBeNull()
        })

        it("each rotate button calls viewer.rotate with the expected angle and axis, and every call uses the default (0/no) animation duration", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const group = await screen.findByRole("group", { name: /Rotate the 3D view/ })

            fireEvent.click(within(group).getByRole("button", { name: "Rotate left" }))
            fireEvent.click(within(group).getByRole("button", { name: "Rotate right" }))
            fireEvent.click(within(group).getByRole("button", { name: "Rotate up" }))
            fireEvent.click(within(group).getByRole("button", { name: "Rotate down" }))

            expect(calls.rotate).toEqual([
                [-20, "y"],
                [20, "y"],
                [-20, "x"],
                [20, "x"],
            ])
        })

        it("Reset view snaps back to the view captured right after load, via setView — not a fresh zoomTo that could land somewhere new", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const group = await screen.findByRole("group", { name: /Rotate the 3D view/ })
            fireEvent.click(within(group).getByRole("button", { name: "Reset view" }))
            expect(calls.setView).toEqual([[FAKE_INITIAL_VIEW]])
        })

        it("buttons are ordinary focusable elements, not a mouse-only affordance", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const group = await screen.findByRole("group", { name: /Rotate the 3D view/ })
            for (const name of ["Rotate left", "Rotate right", "Rotate up", "Rotate down", "Reset view"]) {
                const button = within(group).getByRole("button", { name })
                button.focus()
                expect(button).toHaveFocus()
            }
        })
    })

    describe("zero-size container that later gets a size (ResizeObserver retry)", () => {
        // jsdom has no ResizeObserver at all (see GeometryViewer.zero-size
        // .test.tsx), so proving the retry itself works — not just that a
        // permanently-zero container degrades honestly — needs a fake one
        // installed for just this block, plus offsetWidth/offsetHeight
        // that can actually change value (the file-level stub above is a
        // static `value`, not a getter).
        class FakeResizeObserver {
            static instances: FakeResizeObserver[] = []
            callback: ResizeObserverCallback
            constructor(callback: ResizeObserverCallback) {
                this.callback = callback
                FakeResizeObserver.instances.push(this)
            }
            observe() { /* no-op: the test triggers resizes manually */ }
            unobserve() { /* no-op */ }
            disconnect() { /* no-op */ }
            trigger() {
                this.callback([] as unknown as ResizeObserverEntry[], this as unknown as ResizeObserver)
            }
        }

        let currentWidth = 0
        let currentHeight = 0
        let originalResizeObserver: typeof ResizeObserver | undefined

        beforeEach(() => {
            currentWidth = 0
            currentHeight = 0
            FakeResizeObserver.instances = []
            originalResizeObserver = (globalThis as { ResizeObserver?: typeof ResizeObserver }).ResizeObserver
            ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver
            Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => currentWidth })
            Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => currentHeight })
        })

        afterEach(() => {
            (globalThis as { ResizeObserver?: unknown }).ResizeObserver = originalResizeObserver
        })

        it("does not call createViewer while zero-size, then calls it once a resize is observed — never latches unavailable and never hangs", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)

            // Give the effect a tick to run; with a zero-size container it
            // must watch for a resize rather than attempt (or give up).
            await waitFor(() => expect(FakeResizeObserver.instances).toHaveLength(1))
            expect(calls.createViewer).toHaveLength(0)
            expect(screen.getByText(/Loading the 3D view/)).toBeVisible()

            currentWidth = 320
            currentHeight = 320
            FakeResizeObserver.instances[0].trigger()

            await waitFor(() => expect(calls.createViewer).toHaveLength(1))
            await waitFor(() => expect(document.querySelector(".viewer-canvas")).toHaveAttribute("data-viewer-status", "ready"))
        })
    })

    describe("teardown", () => {
        it("clears the 3Dmol viewer and removes its canvas on unmount — clear() alone leaves 3Dmol's own canvas attached", async () => {
            const { unmount } = render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const container = await waitFor(() => {
                const el = document.querySelector(".viewer-canvas") as HTMLElement
                expect(el.querySelector('[data-testid="fake-3dmol-canvas"]')).not.toBeNull()
                return el
            })
            unmount()
            expect(calls.clear).toHaveLength(1)
            expect(container.querySelector('[data-testid="fake-3dmol-canvas"]')).toBeNull()
            expect(container.children).toHaveLength(0)
        })
    })
})
