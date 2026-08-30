import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { GeometryViewer } from "./GeometryViewer"
import { NAMED_DIHEDRAL_ORACLE_CASES } from "../domain/dihedralOracle.fixture"

/**
 * Atom-picking measurements — see `GeometryViewer.tsx`'s module docstring,
 * "Atom-picking measurements" section, for the click/drag distinction this
 * relies on (3Dmol's own `setClickable`/`closeEnoughForClick`, not
 * anything this component implements itself) and the selection model this
 * exercises.
 *
 * Split into its own file from `GeometryViewer.test.tsx` for the same
 * `vi.mock` hoisting reason `GeometryViewer.zero-size.test.tsx` already
 * is: this file's fake 3Dmol needs `setClickable`/`addSphere`/
 * `addCylinder`/`removeShape`/`removeLabel` the other file's fake viewer
 * doesn't define, and `vi.mock` applies to the whole file it's declared
 * in.
 *
 * jsdom cannot perform real WebGL raycasting, so "clicking an atom" here
 * means invoking the callback this component registered via
 * `setClickable(sel, true, callback)` directly — the same callback 3Dmol
 * itself would invoke from inside `handleClickSelection` after its own
 * click/drag check passes. This tests everything downstream of that
 * decision (which is this component's own logic); the click/drag
 * disambiguation itself is 3Dmol's, verified by reading its source (see
 * the module docstring) and covered by this repo's live-browser
 * verification, not by a jsdom unit test.
 */

type FakeViewerCalls = {
    createViewer: unknown[][]
    setStyle: unknown[][]
    render: unknown[][]
    setClickable: unknown[][]
    addLabel: unknown[][]
    removeAllLabels: unknown[][]
    removeLabel: unknown[][]
    addSphere: unknown[][]
    addCylinder: unknown[][]
    removeShape: unknown[][]
}

const calls: FakeViewerCalls = {
    createViewer: [],
    setStyle: [],
    render: [],
    setClickable: [],
    addLabel: [],
    removeAllLabels: [],
    removeLabel: [],
    addSphere: [],
    addCylinder: [],
    removeShape: [],
}

let capturedClickCallback: ((atom: { serial?: number }) => void) | null = null

function resetCalls() {
    for (const key of Object.keys(calls) as (keyof FakeViewerCalls)[]) calls[key] = []
    capturedClickCallback = null
}

vi.mock("3dmol", () => ({
    createViewer: (...args: unknown[]) => {
        calls.createViewer.push(args)
        const container = args[0] as HTMLElement
        const canvas = document.createElement("canvas")
        container.appendChild(canvas)
        return {
            addModel: () => {},
            setStyle: (...a: unknown[]) => { calls.setStyle.push(a) },
            zoomTo: () => {},
            render: (...a: unknown[]) => { calls.render.push(a) },
            clear: () => {},
            rotate: () => {},
            zoom: () => {},
            getView: () => [0, 0, 0, 1, 0, 0, 0, 1],
            setView: () => {},
            addLabel: (...a: unknown[]) => { calls.addLabel.push(a); return { id: `label-${calls.addLabel.length}` } },
            removeAllLabels: (...a: unknown[]) => { calls.removeAllLabels.push(a) },
            removeLabel: (...a: unknown[]) => { calls.removeLabel.push(a) },
            setClickable: (sel: unknown, clickable: unknown, callback: (atom: { serial?: number }) => void) => {
                calls.setClickable.push([sel, clickable, callback])
                capturedClickCallback = callback
            },
            addSphere: (...a: unknown[]) => { calls.addSphere.push(a); return { id: `sphere-${calls.addSphere.length}` } },
            addCylinder: (...a: unknown[]) => { calls.addCylinder.push(a); return { id: `cylinder-${calls.addCylinder.length}` } },
            removeShape: (...a: unknown[]) => { calls.removeShape.push(a) },
        }
    },
}))

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

// CH3 (planar radical) live record — geom_qcnisbgb4abax5oxym3dtjxu34,
// measured 2026-08-30. atom_index is 1-based (archive numbering);
// `serial` in the click callback below is 0-based (3Dmol's own XYZ-parse
// position) — atoms[0] has atom_index 1, matching the real serial<->index
// relationship this component's docstring documents.
const CH3_XYZ = "4\n\nC 0.000000000000 0.000000000000 0.000000000000\nH -0.000000000000 1.078957000000 0.000000000000\nH 0.934405000000 -0.539479000000 0.000000000000\nH -0.934405000000 -0.539479000000 0.000000000000"
const CH3_ATOMS = [
    { atom_index: 1, element: "C", x: 0.0, y: 0.0, z: 0.0 },
    { atom_index: 2, element: "H", x: -0.0, y: 1.078957, z: 0.0 },
    { atom_index: 3, element: "H", x: 0.934405, y: -0.539479, z: 0.0 },
    { atom_index: 4, element: "H", x: -0.934405, y: -0.539479, z: 0.0 },
]

// A non-planar 4-atom fixture (real, RDKit-verified coordinates — the
// "butane_gauche_plus_60" case from `dihedralOracle.fixture.ts`, expected
// dihedral +60.00°). Deliberately NOT the planar CH3 fixture above: every
// permutation of 4 coplanar points still gives a 0/180 dihedral, so a
// planar fixture cannot tell "clicked in the right order" apart from
// "clicked in a permuted order" (see this describe block's own tests
// below, and PR #295's review, which found exactly this gap).
const NONPLANAR_CASE = NAMED_DIHEDRAL_ORACLE_CASES.find((c) => c.label === "butane_gauche_plus_60")!
const NONPLANAR_ATOMS = [
    { atom_index: 1, element: "C", ...NONPLANAR_CASE.a },
    { atom_index: 2, element: "C", ...NONPLANAR_CASE.b },
    { atom_index: 3, element: "C", ...NONPLANAR_CASE.c },
    { atom_index: 4, element: "C", ...NONPLANAR_CASE.d },
]

async function renderReady(props: Partial<React.ComponentProps<typeof GeometryViewer>> = {}) {
    render(<GeometryViewer atoms={CH3_ATOMS} formula="CH3" xyzText={CH3_XYZ} {...props} />)
    await waitFor(() => expect(calls.setClickable).toHaveLength(1))
    return capturedClickCallback!
}

/** Simulates 3Dmol invoking this component's registered click callback for
 * the atom at the given (0-based) `serial` — see this file's module
 * comment for why this is the right thing to simulate, not a raw DOM
 * click on the canvas.
 *
 * Wrapped in `act()` deliberately: unlike `fireEvent.click(...)` elsewhere
 * in this file, this calls the captured callback directly, bypassing
 * React's own event system entirely — the same way 3Dmol's real
 * `handleClickSelection` would (it is a vanilla addEventListener callback,
 * not a React synthetic event). Under React 18 automatic batching, a
 * `setState` triggered from outside a React-managed event or an `act()`
 * scope is not guaranteed to flush synchronously before the next line of
 * test code runs; measured here as real, not hypothetical, flakiness — a
 * dihedral test intermittently read a stale 3-atom angle instead of the
 * finalised 4-atom dihedral because the 4th click's state update hadn't
 * committed yet. `act()` forces each click's updates to flush before this
 * function returns, so back-to-back `click()` calls in one test see each
 * other's results exactly as the selection-model logic in
 * `GeometryViewer.tsx` expects. */
function click(cb: (atom: { serial?: number }) => void, serial: number) {
    act(() => {
        cb({ serial })
    })
}

describe("GeometryViewer — atom-picking measurements", () => {
    it("registers 3Dmol's own click detection (setClickable), and does not add a second render() call beyond the style effect's one", async () => {
        await renderReady()
        expect(calls.setClickable).toHaveLength(1)
        const [sel, clickable] = calls.setClickable[0]
        expect(sel).toEqual({})
        expect(clickable).toBe(true)
        // Exactly one render on the ready transition — the style effect's.
        // A second render() from the click-registration effect would
        // silently double this, which is exactly what the module
        // docstring says this component avoids.
        expect(calls.render).toHaveLength(1)
    })

    it("2 atoms picked -> a distance measurement appears in the DOM list, in Å by default", async () => {
        const onPick = await renderReady()
        click(onPick, 0) // C, atom_index 1
        click(onPick, 1) // H, atom_index 2

        const list = await screen.findByRole("list")
        const item = within(list).getByText(/Distance C1–H2/)
        expect(item.parentElement).toHaveTextContent("1.0790 Å")
    })

    it("adding a 3rd atom to the SAME selection turns the distance into an angle — one list entry, not two", async () => {
        const onPick = await renderReady()
        click(onPick, 1) // H2
        click(onPick, 0) // C1 (vertex)
        click(onPick, 2) // H3

        const list = await screen.findByRole("list")
        expect(within(list).getAllByRole("listitem")).toHaveLength(1)
        const item = within(list).getByText(/Angle H2–C1–H3/)
        // Real CH3 H-C-H angle, ~120 degrees (planar radical).
        expect(item.parentElement?.textContent).toMatch(/12\d\.\d\d°/)
    })

    it("adding a 4th atom turns it into a dihedral — still one entry", async () => {
        const onPick = await renderReady()
        click(onPick, 0) // C1
        click(onPick, 1) // H2
        click(onPick, 2) // H3
        click(onPick, 3) // H4

        const list = await screen.findByRole("list")
        const items = within(list).getAllByRole("listitem")
        expect(items).toHaveLength(1)
        expect(within(list).getByText(/Dihedral C1–H2–H3–H4/)).toBeInTheDocument()
        // CH3 is planar: any improper dihedral among its own atoms is 0 or
        // 180 (which of the two depends on the specific atom order/chain
        // shape, not on anything this test needs to pin down) — never a
        // value strictly between them. Queried from `.measurement-value`
        // specifically (not the whole list's text), since the adjacent
        // atom tag "H4" run directly into a value like "0.00°" with no
        // separator would otherwise let a naive digit regex misread
        // "H40.00°" as the number 40.
        const valueEl = items[0].querySelector(".measurement-value")
        const match = valueEl?.textContent?.match(/^(-?\d+\.\d\d)°$/)
        expect(match).not.toBeNull()
        const value = Number(match?.[1])
        const distanceFromFlat = Math.min(Math.abs(value), Math.abs(180 - Math.abs(value)))
        expect(distanceFromFlat).toBeLessThan(0.01)
    })

    it("5th distinct atom starts a new selection, leaving the finalised 4-atom measurement untouched", async () => {
        const fiveAtoms = [
            ...CH3_ATOMS,
            { atom_index: 5, element: "H", x: 0.5, y: 0.5, z: 1.5 },
        ]
        const fiveXyz = `${CH3_XYZ}\nH 0.500000000000 0.500000000000 1.500000000000`
        render(<GeometryViewer atoms={fiveAtoms} formula="C+4H" xyzText={fiveXyz} />)
        await waitFor(() => expect(calls.setClickable).toHaveLength(1))
        const onPick = capturedClickCallback!

        click(onPick, 0)
        click(onPick, 1)
        click(onPick, 2)
        click(onPick, 3) // 4 atoms -> dihedral, finalised-so-far

        await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(1))

        click(onPick, 4) // 5th DISTINCT atom -> starts a new selection

        // Waits for the click's state update to flush before querying —
        // unlike the earlier clicks in this test (each followed by a
        // `waitFor`/`find*` before the next assertion), this is the first
        // query after this specific click.
        await waitFor(() => expect(screen.getByText(/1 atom selected \(H5\)/)).toBeInTheDocument())

        const list = screen.getByRole("list")
        // The old dihedral is still there, untouched...
        expect(within(list).getByText(/Dihedral C1–H2–H3–H4/)).toBeInTheDocument()
        // ...and no NEW measurement entry yet, since the new selection has
        // only 1 atom in it so far (not enough for a distance).
        expect(within(list).getAllByRole("listitem")).toHaveLength(1)
    })

    it("clicking an already-selected atom deselects it, shrinking the live measurement", async () => {
        const onPick = await renderReady()
        click(onPick, 0) // C1 -> selection [C1]
        click(onPick, 1) // H2 -> selection [C1, H2], distance
        click(onPick, 2) // H3 -> selection [C1, H2, H3], angle (vertex = H2)

        await waitFor(() => expect(screen.getByText(/Angle C1–H2–H3/)).toBeInTheDocument())

        click(onPick, 2) // deselect H3 -> back down to a 2-atom distance

        const list = await screen.findByRole("list")
        expect(within(list).getAllByRole("listitem")).toHaveLength(1)
        expect(within(list).getByText(/Distance C1–H2/)).toBeInTheDocument()
        expect(within(list).queryByText(/Angle/)).toBeNull()
    })

    it("Clear measurements empties the list and resets the status message", async () => {
        const onPick = await renderReady()
        click(onPick, 0)
        click(onPick, 1)
        await waitFor(() => expect(screen.getByRole("list")).toBeInTheDocument())

        fireEvent.click(screen.getByRole("button", { name: "Clear measurements" }))

        expect(screen.queryByRole("list")).toBeNull()
        expect(screen.getByRole("status")).toHaveTextContent("Measurements cleared.")
    })

    it("Clear measurements button is disabled when there is nothing to clear", async () => {
        await renderReady()
        expect(screen.getByRole("button", { name: "Clear measurements" })).toBeDisabled()
    })

    it("the aria-live status region holds a short message only — the list itself lives in ordinary DOM outside it", async () => {
        const onPick = await renderReady()
        click(onPick, 0)
        click(onPick, 1)

        const status = await screen.findByRole("status")
        expect(status).toHaveTextContent("Distance measured.")
        // The list is not inside the status region.
        expect(status.querySelector("ol")).toBeNull()
        expect(status.querySelector("li")).toBeNull()
    })

    it("a distance measurement follows the coordinateUnitMode prop into bohr, with the unit spelled out explicitly", async () => {
        render(<GeometryViewer atoms={CH3_ATOMS} formula="CH3" xyzText={CH3_XYZ} coordinateUnitMode="bohr" />)
        await waitFor(() => expect(calls.setClickable).toHaveLength(1))
        const onPick = capturedClickCallback!
        click(onPick, 0)
        click(onPick, 1)

        const list = await screen.findByRole("list")
        // 1.078957 Å / 0.529177210903 (CODATA 2018 Bohr radius, Å) = 2.038933 bohr
        expect(list.textContent).toMatch(/2\.038933 bohr/)
        expect(list.textContent).not.toMatch(/Å/)
    })

    it("draws on-canvas markers (spheres + connectors) as a convenience, but they are not required for the DOM list to be correct", async () => {
        const onPick = await renderReady()
        click(onPick, 0)
        click(onPick, 1)

        await waitFor(() => expect(calls.addSphere.length).toBeGreaterThan(0))
        expect(calls.addCylinder.length).toBeGreaterThan(0)
    })

    it("an undefined torsion (3 of the 4 picked atoms collinear) reads as an honest 'undefined', never a confident 0.00° — the GeometryViewer.tsx NaN guard's only reachable path", async () => {
        // a, b, c on a line; d off it. Any linear fragment a reader can
        // actually click (CO2, HCN, acetylene, a near-linear abstraction
        // saddle point) reaches this. Before the fix in geometryMeasure.ts,
        // this returned exactly 0 — indistinguishable from a genuine
        // syn-periplanar torsion — which is also why GeometryViewer.tsx's
        // own `if (Number.isNaN(value)) ...` branch was unreachable: a
        // mutation that replaced its condition with `false` still passed
        // 406/406, because nothing ever produced a NaN to trigger it.
        const collinearAtoms = [
            { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
            { atom_index: 2, element: "C", x: 1, y: 0, z: 0 },
            { atom_index: 3, element: "C", x: 2, y: 0, z: 0 },
            { atom_index: 4, element: "H", x: 2, y: 1, z: 1 },
        ]
        render(<GeometryViewer atoms={collinearAtoms} formula="C3H" xyzText={null} />)
        await waitFor(() => expect(calls.setClickable).toHaveLength(1))
        const onPick = capturedClickCallback!
        click(onPick, 0)
        click(onPick, 1)
        click(onPick, 2)
        click(onPick, 3)

        const list = await screen.findByRole("list")
        expect(within(list).getByText(/undefined \(coincident or collinear atom positions\)/)).toBeInTheDocument()
        expect(within(list).queryByText(/^0\.00°$/)).toBeNull()
    })
})

// R1 (PR #295 review): the atom order at the component's dihedral call
// site (GeometryViewer.tsx, dihedralOf(atoms[0], atoms[1], atoms[2],
// atoms[3])) was unverified — permuting it survived the entire suite,
// because the only component-level dihedral test used planar CH3, where
// every permutation still gives 0/180. This describe block uses a
// non-planar fixture with a real signed expected value instead.
describe("GeometryViewer — component-level dihedral wiring (non-planar fixture)", () => {
    it("wires the 4 clicked atoms into dihedral() in click order — a-b-c-d, not a permutation", async () => {
        render(<GeometryViewer atoms={NONPLANAR_ATOMS} formula="C4" xyzText={null} />)
        await waitFor(() => expect(calls.setClickable).toHaveLength(1))
        const onPick = capturedClickCallback!
        click(onPick, 0) // C1 = a
        click(onPick, 1) // C2 = b
        click(onPick, 2) // C3 = c
        click(onPick, 3) // C4 = d

        const list = await screen.findByRole("list")
        const item = within(list).getByText(/Dihedral C1–C2–C3–C4/)
        // Target dihedral for this fixture is exactly +60.00°; a swapped
        // operand order at the call site (e.g. dihedralOf(atoms[1],
        // atoms[0], atoms[2], atoms[3])) produces a completely different
        // value (~-87.8°) for this same fixture, not merely a sign flip —
        // see this file's module-level comment on NONPLANAR_ATOMS.
        expect(item.parentElement).toHaveTextContent("60.00°")

        // The on-canvas convenience label is wired from the SAME
        // measurement object as the DOM list, not recomputed separately —
        // assert both actually agree.
        const dihedralLabelCall = calls.addLabel.find((c) => typeof c[0] === "string" && (c[0] as string).includes("60.00°"))
        expect(dihedralLabelCall).toBeDefined()
    })
})

// R3 (PR #295 review): claimed the on-canvas label ignores the unit
// toggle (hardcoded to "angstrom"), citing GeometryViewer.tsx:977. Read
// against the actual shipped code at commit 838e8474: both the DOM list
// AND the canvas label call `measurementValueText(m, coordinateUnitMode)`
// — the SAME variable, not a hardcoded literal — so this specific claim
// does not reproduce. This is a regression guard, not a fix: it locks in
// the (already correct) behaviour so a future change can't reintroduce
// exactly the divergence the review described.
describe("GeometryViewer — canvas label follows the unit toggle (R3 regression guard)", () => {
    it("the on-canvas label text uses the SAME unit as the DOM panel — bohr, when the page is on bohr", async () => {
        render(<GeometryViewer atoms={CH3_ATOMS} formula="CH3" xyzText={CH3_XYZ} coordinateUnitMode="bohr" />)
        await waitFor(() => expect(calls.setClickable).toHaveLength(1))
        const onPick = capturedClickCallback!
        click(onPick, 0)
        click(onPick, 1)

        const list = await screen.findByRole("list")
        expect(list.textContent).toMatch(/2\.038933 bohr/)

        const distanceLabelCall = calls.addLabel.find((c) => typeof c[0] === "string" && (c[0] as string).includes("bohr"))
        expect(distanceLabelCall).toBeDefined()
        expect(calls.addLabel.some((c) => typeof c[0] === "string" && (c[0] as string).includes("Å"))).toBe(false)
    })
})

// R5 (PR #295 review): claimed measurementCentroid divides by 2 instead
// of atoms.length. Read against the actual shipped code: it already
// divides by `atoms.length`. This is a regression guard, not a fix — a
// distance (2-atom) measurement can't distinguish /2 from /atoms.length,
// so this uses a 4-atom dihedral with atoms spread away from the origin,
// where the two formulas give clearly different results.
describe("GeometryViewer — measurement label placement (R5 regression guard)", () => {
    it("the on-canvas value label sits at the average of ALL measured atoms, not the sum divided by 2", async () => {
        render(<GeometryViewer atoms={NONPLANAR_ATOMS} formula="C4" xyzText={null} />)
        await waitFor(() => expect(calls.setClickable).toHaveLength(1))
        const onPick = capturedClickCallback!
        click(onPick, 0)
        click(onPick, 1)
        click(onPick, 2)
        click(onPick, 3)

        await waitFor(() => expect(calls.addLabel.length).toBeGreaterThan(0))
        // This effect redraws from scratch on every click and `calls.addLabel`
        // accumulates every call ever made (removed or not), so an
        // intermediate 3-atom angle label ("111.xx°" for this butane
        // skeleton) is ALSO in this array by the time all 4 clicks have
        // landed. Match the exact final dihedral text ("60.00°"), not a
        // generic "contains a degree sign" check, or this would silently
        // grab the 3-atom angle's centroid instead of the 4-atom one.
        const dihedralLabelCall = calls.addLabel.find((c) => c[0] === "60.00°")
        expect(dihedralLabelCall).toBeDefined()
        const options = dihedralLabelCall![1] as { position: { x: number; y: number; z: number } }

        const { a, b, c, d } = NONPLANAR_CASE
        const expectedCentroid = {
            x: (a.x + b.x + c.x + d.x) / 4,
            y: (a.y + b.y + c.y + d.y) / 4,
            z: (a.z + b.z + c.z + d.z) / 4,
        }
        const wrongDivideByTwo = {
            x: (a.x + b.x + c.x + d.x) / 2,
            y: (a.y + b.y + c.y + d.y) / 2,
            z: (a.z + b.z + c.z + d.z) / 2,
        }

        expect(options.position.x).toBeCloseTo(expectedCentroid.x, 6)
        expect(options.position.y).toBeCloseTo(expectedCentroid.y, 6)
        expect(options.position.z).toBeCloseTo(expectedCentroid.z, 6)
        // A real, non-cosmetic assertion that the buggy /2 formula is
        // NOT what produced this position — these two candidate points
        // are far enough apart (the y coordinate alone differs by ~0.54)
        // that toBeCloseTo would fail if the divisor were wrong.
        expect(Math.abs(options.position.y - wrongDivideByTwo.y)).toBeGreaterThan(0.1)
    })
})

// R9 (PR #295 review): claimed dropping aria-labelledby={headingId} from
// <section className="viewer-measurements"> survived, since that
// attribute is the measurement <ol>'s only accessible name. Read against
// the actual shipped code: it's present. This is a regression guard, not
// a fix.
describe("GeometryViewer — measurements section accessible name (R9 guard)", () => {
    it("the measurements section is labelled by its own visible 'Measurements' heading", async () => {
        await renderReady()
        const section = document.querySelector(".viewer-measurements") as HTMLElement
        expect(section).not.toBeNull()
        const labelledBy = section.getAttribute("aria-labelledby")
        expect(labelledBy).toBeTruthy()
        const heading = document.getElementById(labelledBy!)
        expect(heading).not.toBeNull()
        expect(heading).toHaveTextContent("Measurements")
    })
})
