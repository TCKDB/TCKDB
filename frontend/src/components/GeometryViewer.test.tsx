import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { GeometryViewer } from "./GeometryViewer"

/**
 * These tests exercise the "3Dmol succeeded" path, which needs two things
 * jsdom does not provide on its own:
 *
 *  1. A working WebGL context — jsdom has none at all (see
 *     `GeometryViewer.webgl-unavailable.test.tsx`, which tests the real,
 *     unmocked failure path instead). "3dmol" is mocked here with a fake
 *     viewer that records what it was called with, so these tests can
 *     pin the exact data 3Dmol receives without needing real WebGL.
 *  2. A non-zero-size container — jsdom never lays elements out, so
 *     `offsetWidth`/`offsetHeight` are always 0 (see the size-check
 *     comment in `GeometryViewer.tsx`), which this component treats the
 *     same as a WebGL failure. `stubOffsetSize` below overrides both on
 *     `HTMLElement.prototype` for the lifetime of each test so the
 *     component takes the "container is real" branch.
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
}

function resetCalls() {
    for (const key of Object.keys(calls) as (keyof FakeViewerCalls)[]) calls[key] = []
}

vi.mock("3dmol", () => ({
    createViewer: (...args: unknown[]) => {
        calls.createViewer.push(args)
        return {
            addModel: (...a: unknown[]) => { calls.addModel.push(a) },
            setStyle: (...a: unknown[]) => { calls.setStyle.push(a) },
            zoomTo: (...a: unknown[]) => { calls.zoomTo.push(a) },
            spin: (...a: unknown[]) => { calls.spin.push(a) },
            animate: (...a: unknown[]) => { calls.animate.push(a) },
            render: (...a: unknown[]) => { calls.render.push(a) },
            clear: (...a: unknown[]) => { calls.clear.push(a) },
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
})
