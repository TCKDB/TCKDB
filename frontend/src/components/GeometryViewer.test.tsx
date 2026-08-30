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
    zoom: unknown[][]
    spin: unknown[][]
    animate: unknown[][]
    render: unknown[][]
    clear: unknown[][]
    rotate: unknown[][]
    getView: unknown[][]
    setView: unknown[][]
    addLabel: unknown[][]
    removeAllLabels: unknown[][]
}

const calls: FakeViewerCalls = {
    createViewer: [],
    addModel: [],
    setStyle: [],
    zoomTo: [],
    zoom: [],
    spin: [],
    animate: [],
    render: [],
    clear: [],
    rotate: [],
    getView: [],
    setView: [],
    addLabel: [],
    removeAllLabels: [],
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
            zoom: (...a: unknown[]) => { calls.zoom.push(a) },
            spin: (...a: unknown[]) => { calls.spin.push(a) },
            animate: (...a: unknown[]) => { calls.animate.push(a) },
            render: (...a: unknown[]) => { calls.render.push(a) },
            clear: (...a: unknown[]) => { calls.clear.push(a) },
            rotate: (...a: unknown[]) => { calls.rotate.push(a) },
            getView: (...a: unknown[]) => { calls.getView.push(a); return FAKE_INITIAL_VIEW },
            setView: (...a: unknown[]) => { calls.setView.push(a) },
            addLabel: (...a: unknown[]) => { calls.addLabel.push(a) },
            removeAllLabels: (...a: unknown[]) => { calls.removeAllLabels.push(a) },
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

    it("does NOT pass nomouse:true — 3Dmol's own drag-to-rotate/multi-touch handling stays enabled; only the scroll-trapping gestures are intercepted separately (see below)", async () => {
        render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
        await waitFor(() => expect(calls.createViewer).toHaveLength(1))
        const config = calls.createViewer[0][1] as Record<string, unknown>
        expect(config.nomouse).not.toBe(true)
    })

    describe("scroll-trap fix: capture-phase wheel/touchmove interception on the container", () => {
        // jsdom implements real DOM event propagation/capture semantics
        // (this is plain synchronous JS, not layout or native input), so
        // it CAN prove that this component's own listener calls
        // stopPropagation() (and never preventDefault()) for the right
        // gestures. What jsdom cannot show is whether a real browser then
        // actually still scrolls the page — that needs a real browser,
        // verified separately (see the PR description for the measured
        // real-Chromium numbers). These two kinds of coverage are
        // deliberately not the same test.
        it("a plain wheel event dispatched on the nested canvas never reaches a listener registered on that canvas — stopPropagation, not preventDefault", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const container = await waitFor(() => {
                const el = document.querySelector(".viewer-canvas") as HTMLElement
                expect(el.querySelector('[data-testid="fake-3dmol-canvas"]')).not.toBeNull()
                return el
            })
            const canvas = container.querySelector('[data-testid="fake-3dmol-canvas"]') as HTMLElement
            // Mirrors 3Dmol's own real listener registration exactly
            // (GLViewer.ts:295): directly on the canvas, non-passive.
            let sawWheelOnCanvas = false
            canvas.addEventListener("wheel", () => { sawWheelOnCanvas = true }, { passive: false })

            const event = new Event("wheel", { bubbles: true, cancelable: true })
            canvas.dispatchEvent(event)

            expect(sawWheelOnCanvas).toBe(false)
            expect(event.defaultPrevented).toBe(false)
        })

        it("a one-finger touchmove dispatched on the nested canvas never reaches a listener registered on that canvas", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const container = await waitFor(() => {
                const el = document.querySelector(".viewer-canvas") as HTMLElement
                expect(el.querySelector('[data-testid="fake-3dmol-canvas"]')).not.toBeNull()
                return el
            })
            const canvas = container.querySelector('[data-testid="fake-3dmol-canvas"]') as HTMLElement
            let sawTouchMoveOnCanvas = false
            canvas.addEventListener("touchmove", () => { sawTouchMoveOnCanvas = true }, { passive: false })

            const event = new Event("touchmove", { bubbles: true, cancelable: true })
            Object.defineProperty(event, "touches", { value: [{}], configurable: true })
            canvas.dispatchEvent(event)

            expect(sawTouchMoveOnCanvas).toBe(false)
            expect(event.defaultPrevented).toBe(false)
        })

        it("a two-finger touchmove is left alone — it does reach the nested canvas, so pinch/two-finger drag still work", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const container = await waitFor(() => {
                const el = document.querySelector(".viewer-canvas") as HTMLElement
                expect(el.querySelector('[data-testid="fake-3dmol-canvas"]')).not.toBeNull()
                return el
            })
            const canvas = container.querySelector('[data-testid="fake-3dmol-canvas"]') as HTMLElement
            let sawTouchMoveOnCanvas = false
            canvas.addEventListener("touchmove", () => { sawTouchMoveOnCanvas = true }, { passive: false })

            const event = new Event("touchmove", { bubbles: true, cancelable: true })
            Object.defineProperty(event, "touches", { value: [{}, {}], configurable: true })
            canvas.dispatchEvent(event)

            expect(sawTouchMoveOnCanvas).toBe(true)
        })

        it("a one-finger touchstart is ALSO intercepted, not just touchmove — measured live in a real browser (Playwright/Chromium): 3Dmol's own touchstart handler (_handleMouseDown) calls preventDefault() unconditionally too, and Chromium suppresses scrolling for the WHOLE touch sequence once touchstart has been prevented, even when every later touchmove in that sequence is left alone. Intercepting only touchmove reproduces the scroll trap; this is not redundant with the touchmove interception above", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const container = await waitFor(() => {
                const el = document.querySelector(".viewer-canvas") as HTMLElement
                expect(el.querySelector('[data-testid="fake-3dmol-canvas"]')).not.toBeNull()
                return el
            })
            const canvas = container.querySelector('[data-testid="fake-3dmol-canvas"]') as HTMLElement
            let sawTouchStartOnCanvas = false
            canvas.addEventListener("touchstart", () => { sawTouchStartOnCanvas = true }, { passive: false })

            const event = new Event("touchstart", { bubbles: true, cancelable: true })
            Object.defineProperty(event, "touches", { value: [{}], configurable: true })
            canvas.dispatchEvent(event)

            expect(sawTouchStartOnCanvas).toBe(false)
            expect(event.defaultPrevented).toBe(false)
        })

        it("a two-finger touchstart is left alone — 3Dmol needs both touch points at touchstart to compute its pinch baseline distance", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const container = await waitFor(() => {
                const el = document.querySelector(".viewer-canvas") as HTMLElement
                expect(el.querySelector('[data-testid="fake-3dmol-canvas"]')).not.toBeNull()
                return el
            })
            const canvas = container.querySelector('[data-testid="fake-3dmol-canvas"]') as HTMLElement
            let sawTouchStartOnCanvas = false
            canvas.addEventListener("touchstart", () => { sawTouchStartOnCanvas = true }, { passive: false })

            const event = new Event("touchstart", { bubbles: true, cancelable: true })
            Object.defineProperty(event, "touches", { value: [{}, {}], configurable: true })
            canvas.dispatchEvent(event)

            expect(sawTouchStartOnCanvas).toBe(true)
        })
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

    describe("rotate/zoom/reset controls (buttons are an addition to mouse/touch, not the only way to move the view)", () => {
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

        it("Zoom in/out call viewer.zoom with a reciprocal factor pair, no animation duration argument (reduced-motion-safe by construction)", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const group = await screen.findByRole("group", { name: /Rotate the 3D view/ })

            fireEvent.click(within(group).getByRole("button", { name: "Zoom in" }))
            fireEvent.click(within(group).getByRole("button", { name: "Zoom out" }))

            expect(calls.zoom).toHaveLength(2)
            const [inFactor] = calls.zoom[0] as [number]
            const [outFactor] = calls.zoom[1] as [number]
            expect(inFactor).toBeGreaterThan(1)
            expect(outFactor).toBeCloseTo(1 / inFactor)
            expect(calls.zoom[0]).toHaveLength(1)
            expect(calls.zoom[1]).toHaveLength(1)
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
            for (const name of ["Rotate left", "Rotate right", "Rotate up", "Rotate down", "Zoom in", "Zoom out", "Reset view"]) {
                const button = within(group).getByRole("button", { name })
                button.focus()
                expect(button).toHaveFocus()
            }
        })
    })

    describe("representation style (ball & stick / spacefill / wireframe)", () => {
        it("defaults to ball & stick, marked pressed", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const group = await screen.findByRole("group", { name: /Display options/ })
            expect(within(group).getByRole("button", { name: "Ball & stick" })).toHaveAttribute("aria-pressed", "true")
            expect(within(group).getByRole("button", { name: "Spacefill" })).toHaveAttribute("aria-pressed", "false")
        })

        it("switching to Spacefill calls setStyle again with the spacefill spec, without resetting the camera (no extra zoomTo)", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            await waitFor(() => expect(calls.setStyle).toHaveLength(1))
            const group = await screen.findByRole("group", { name: /Display options/ })

            fireEvent.click(within(group).getByRole("button", { name: "Spacefill" }))

            await waitFor(() => expect(calls.setStyle).toHaveLength(2))
            expect(calls.setStyle[1][0]).toEqual({})
            expect(calls.setStyle[1][1]).toEqual({ sphere: {} })
            expect(calls.zoomTo).toHaveLength(1)
            expect(within(group).getByRole("button", { name: "Spacefill" })).toHaveAttribute("aria-pressed", "true")
        })

        it("switching back to a previously-selected style renders that style again (not stuck on the last one applied)", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            await waitFor(() => expect(calls.setStyle).toHaveLength(1))
            const group = await screen.findByRole("group", { name: /Display options/ })

            fireEvent.click(within(group).getByRole("button", { name: "Wireframe" }))
            await waitFor(() => expect(calls.setStyle).toHaveLength(2))
            fireEvent.click(within(group).getByRole("button", { name: "Ball & stick" }))
            await waitFor(() => expect(calls.setStyle).toHaveLength(3))

            expect(calls.setStyle[1][1]).toEqual({ line: { linewidth: 2 } })
            expect(calls.setStyle[2][1]).toEqual({ stick: { radius: 0.14 }, sphere: { scale: 0.28 } })
        })

        it("spacefill swaps the bond-disclosure sentence for a style-accurate one — spacefill draws no explicit bond primitive", async () => {
            render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={CH_XYZ} />)
            const group = await screen.findByRole("group", { name: /Display options/ })
            fireEvent.click(within(group).getByRole("button", { name: "Spacefill" }))
            expect(screen.getByText(/An interactive 3D view/).textContent).toMatch(
                /This spacefill style does not draw an explicit bond between atoms/,
            )
            expect(screen.getByText(/An interactive 3D view/).textContent).not.toMatch(
                /Bonds shown are inferred from interatomic distance/,
            )
        })
    })

    describe("atom labels — must follow the coordinate table's own atom_index, not 3Dmol's internal numbering", () => {
        // Deliberately non-sequential / non-zero-based atom_index values,
        // so a mutation that labels from array position (0-based) or from
        // some other counter cannot accidentally produce the same text as
        // the correct atom_index-based label.
        const ATOMS = [
            { atom_index: 7, element: "C", x: 0, y: 0, z: 0 },
            { atom_index: 12, element: "H", x: 0, y: 0, z: 1.09 },
        ]
        const XYZ = "2\n\nC 0 0 0\nH 0 0 1.09"

        it("label mode defaults to None — no addLabel calls", async () => {
            render(<GeometryViewer atoms={ATOMS} formula="CH" xyzText={XYZ} />)
            await waitFor(() => expect(calls.render).toHaveLength(1))
            expect(calls.addLabel).toHaveLength(0)
        })

        it("'Numbers' labels each atom with its own atom_index from the coordinate table, not a 0-based or serial-based counter", async () => {
            render(<GeometryViewer atoms={ATOMS} formula="CH" xyzText={XYZ} />)
            const group = await screen.findByRole("group", { name: /Display options/ })
            fireEvent.change(within(group).getByLabelText("Atom labels"), { target: { value: "numbers" } })

            await waitFor(() => expect(calls.addLabel).toHaveLength(2))
            const texts = calls.addLabel.map((call) => call[0])
            expect(texts).toEqual(["7", "12"])
            const positions = calls.addLabel.map((call) => (call[1] as { position: unknown }).position)
            expect(positions).toEqual([
                { x: 0, y: 0, z: 0 },
                { x: 0, y: 0, z: 1.09 },
            ])
        })

        it("'Symbols' labels each atom with its element only", async () => {
            render(<GeometryViewer atoms={ATOMS} formula="CH" xyzText={XYZ} />)
            const group = await screen.findByRole("group", { name: /Display options/ })
            fireEvent.change(within(group).getByLabelText("Atom labels"), { target: { value: "symbols" } })
            await waitFor(() => expect(calls.addLabel).toHaveLength(2))
            expect(calls.addLabel.map((call) => call[0])).toEqual(["C", "H"])
        })

        it("'Symbols + numbers' concatenates element and atom_index", async () => {
            render(<GeometryViewer atoms={ATOMS} formula="CH" xyzText={XYZ} />)
            const group = await screen.findByRole("group", { name: /Display options/ })
            fireEvent.change(within(group).getByLabelText("Atom labels"), { target: { value: "both" } })
            await waitFor(() => expect(calls.addLabel).toHaveLength(2))
            expect(calls.addLabel.map((call) => call[0])).toEqual(["C7", "H12"])
        })

        it("switching back to None clears labels via removeAllLabels and adds none", async () => {
            render(<GeometryViewer atoms={ATOMS} formula="CH" xyzText={XYZ} />)
            const group = await screen.findByRole("group", { name: /Display options/ })
            const select = within(group).getByLabelText("Atom labels")
            fireEvent.change(select, { target: { value: "numbers" } })
            await waitFor(() => expect(calls.addLabel).toHaveLength(2))
            fireEvent.change(select, { target: { value: "none" } })
            await waitFor(() => expect(calls.removeAllLabels.length).toBeGreaterThanOrEqual(2))
            expect(calls.addLabel).toHaveLength(2)
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
