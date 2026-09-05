import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, waitFor } from "@testing-library/react"
import { GeometryViewer } from "./GeometryViewer"

/**
 * Owner report ("record-page residuals" re-review, item 1): the
 * measurements panel rendered to the right of and below the 3D picture
 * instead of directly under it (see the CSS-source test in
 * `../geometry-measure.layout.css.test.ts` and the PR body's CDP rects
 * for the real-browser measurement). jsdom does not lay anything out, so
 * it cannot see the float itself -- what it CAN confirm is the one thing
 * that made the float possible even to reach: `.viewer-measurements`
 * must render as a DOM sibling immediately after `.viewer-stage`, both
 * inside `.geometry-viewer`, so the shared-left-edge CSS fix has
 * adjacent, same-parent boxes to align in the first place.
 *
 * Minimal 3Dmol mock, same shape as `GeometryViewer.test.tsx` -- see
 * that file's module docstring for why a fake viewer + a non-zero
 * container size are both needed to reach `status === "ready"` (the
 * measurements panel only renders once ready).
 */

vi.mock("3dmol", () => ({
    createViewer: (...args: unknown[]) => {
        const container = args[0] as HTMLElement
        const canvas = document.createElement("canvas")
        container.appendChild(canvas)
        return {
            addModel: () => {},
            setStyle: () => {},
            setBackgroundColor: () => {},
            zoomTo: () => {},
            zoom: () => {},
            spin: () => {},
            animate: () => {},
            render: () => {},
            clear: () => {},
            rotate: () => {},
            getView: () => [1, 2, 3, 4, 0.1, 0.2, 0.3, 0.4],
            setView: () => {},
            addLabel: () => {},
            removeAllLabels: () => {},
        }
    },
}))

let restoreOffsetWidth: PropertyDescriptor | undefined
let restoreOffsetHeight: PropertyDescriptor | undefined

beforeEach(() => {
    restoreOffsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth")
    restoreOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight")
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, value: 320 })
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, value: 320 })
})

afterEach(() => {
    cleanup()
    if (restoreOffsetWidth) Object.defineProperty(HTMLElement.prototype, "offsetWidth", restoreOffsetWidth)
    if (restoreOffsetHeight) Object.defineProperty(HTMLElement.prototype, "offsetHeight", restoreOffsetHeight)
})

const CH_ATOMS = [
    { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
    { atom_index: 2, element: "H", x: 0, y: 0, z: 1.09 },
]

describe("GeometryViewer DOM order: measurements panel sits directly under the stage", () => {
    it("`.viewer-measurements` is the next sibling of `.viewer-stage`, both children of `.geometry-viewer`", async () => {
        const { container } = render(<GeometryViewer atoms={CH_ATOMS} formula="CH" xyzText={null} />)

        const measurements = await waitFor(() => {
            const el = container.querySelector(".viewer-measurements")
            expect(el).not.toBeNull()
            return el as HTMLElement
        })

        const stage = container.querySelector(".viewer-stage") as HTMLElement
        const root = container.querySelector(".geometry-viewer") as HTMLElement

        expect(stage).not.toBeNull()
        expect(root).not.toBeNull()
        expect(stage.parentElement).toBe(root)
        expect(measurements.parentElement).toBe(root)
        // Document order: the stage (picture) comes before the measurements
        // panel, and nothing else sits between them.
        expect(stage.nextElementSibling).toBe(measurements)
    })
})
