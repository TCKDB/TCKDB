import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
import { GeometryViewer } from "./GeometryViewer"

afterEach(cleanup)

/**
 * Component-level tests for the hand-rolled SVG projection math itself —
 * separate from `GeometryDetailPage.test.tsx`, which exercises this
 * component only through a full page render. These exist because a
 * mutation-testing pass found the projection math had NO test pinning an
 * actual screen coordinate: flipping the x axis, doubling the scale
 * factor, and reversing the depth (paint-order) sort all survived 108/108
 * with only an atom-*count* assertion in place. An x-axis flip in
 * particular is a mirror reflection — on a chiral geometry it silently
 * shows the wrong enantiomer while every count-only assertion stays
 * green, since a mirror image has exactly as many atoms as the original.
 */
describe("GeometryViewer", () => {
    it("pins each atom's projected screen position for the default rotation — catches an axis flip or a scale error", () => {
        // Three atoms placed only along the z-axis (x=y=0 for all), so
        // their centroid is already the origin and centering is a no-op —
        // the projection formula can be hand-verified exactly rather than
        // approximately. At the default yaw=-30deg / pitch=15deg:
        //   x1 = x*cos(yaw) + z*sin(yaw) = z*sin(-30deg) = z*(-0.5)
        //   z1 = -x*sin(yaw) + z*cos(yaw) = z*cos(-30deg) = z*0.8660254
        //   y2 = y1*cos(pitch) - z1*sin(pitch) = -z1*sin(15deg)  (y1=0)
        //   depth (z2) = y1*sin(pitch) + z1*cos(pitch) = z1*cos(15deg)
        // maxNorm = 2 (the |z|=2 atoms), scale = (160-34)/2 = 63, giving
        // exact screenX = 160 + x1*63 of 223 / 160 / 97 for z = -2 / 0 / 2.
        const { container } = render(
            <GeometryViewer
                atoms={[
                    { atom_index: 1, element: "C", x: 0, y: 0, z: -2 },
                    { atom_index: 2, element: "N", x: 0, y: 0, z: 0 },
                    { atom_index: 3, element: "O", x: 0, y: 0, z: 2 },
                ]}
                formula="CNO"
            />,
        )
        const carbon = container.querySelector('circle[fill="#3c4856"]') as SVGCircleElement
        const nitrogen = container.querySelector('circle[fill="#205493"]') as SVGCircleElement
        const oxygen = container.querySelector('circle[fill="#b23b3b"]') as SVGCircleElement
        expect(carbon).not.toBeNull()
        expect(nitrogen).not.toBeNull()
        expect(oxygen).not.toBeNull()

        // Negating x1 (a naive "flip the x axis" mutation) would swap the
        // carbon and oxygen screenX values below (223 <-> 97); doubling
        // `scale` would push every value roughly 2x farther from center
        // (160). Both are observable here.
        expect(Number(carbon.getAttribute("cx"))).toBeCloseTo(223, 1)
        expect(Number(carbon.getAttribute("cy"))).toBeCloseTo(131.76, 1)
        expect(Number(nitrogen.getAttribute("cx"))).toBeCloseTo(160, 1)
        expect(Number(nitrogen.getAttribute("cy"))).toBeCloseTo(160, 1)
        expect(Number(oxygen.getAttribute("cx"))).toBeCloseTo(97, 1)
        expect(Number(oxygen.getAttribute("cy"))).toBeCloseTo(188.24, 1)
    })

    it("paints atoms back-to-front by projected depth — farthest first, nearest last on top", () => {
        // Same three atoms as above. Their depth (z2) is z*0.8365163: the
        // carbon (z=-2) has the smallest (most negative) depth and must be
        // painted first; the oxygen (z=2) has the largest depth and must
        // be painted last, on top. A reversed sort comparator produces the
        // opposite DOM order without changing any individual atom's cx/cy,
        // so this needs its own assertion — the position-pin test above
        // cannot catch a reversed paint order on its own.
        const { container } = render(
            <GeometryViewer
                atoms={[
                    { atom_index: 1, element: "C", x: 0, y: 0, z: -2 },
                    { atom_index: 2, element: "N", x: 0, y: 0, z: 0 },
                    { atom_index: 3, element: "O", x: 0, y: 0, z: 2 },
                ]}
                formula="CNO"
            />,
        )
        const fills = [...container.querySelectorAll(".viewer-atoms circle")].map((c) => c.getAttribute("fill"))
        expect(fills).toEqual(["#3c4856", "#205493", "#b23b3b"])
    })

    it("draws no bond between atoms farther apart than any plausible covalent distance", () => {
        // The same C/N/O trio: every pairwise distance here (2 A, 2 A, 4 A)
        // exceeds every pairwise covalent-radius-sum cutoff this component
        // uses (all under 1.95 A) — deliberately, so that a "bond between
        // every pair" mutation is observable as 3 unwanted lines instead
        // of the correct 0.
        const { container } = render(
            <GeometryViewer
                atoms={[
                    { atom_index: 1, element: "C", x: 0, y: 0, z: -2 },
                    { atom_index: 2, element: "N", x: 0, y: 0, z: 0 },
                    { atom_index: 3, element: "O", x: 0, y: 0, z: 2 },
                ]}
                formula="CNO"
            />,
        )
        expect(container.querySelectorAll(".viewer-bonds line")).toHaveLength(0)
    })

    it("draws a bond between atoms within a plausible covalent distance", () => {
        // A realistic C-H bond length (1.09 A), well inside this
        // component's (0.76+0.31)*1.3 = 1.391 A cutoff for a C-H pair — a
        // positive control alongside the negative one above, so "bonds
        // never drawn at all" is also a distinguishable failure.
        const { container } = render(
            <GeometryViewer
                atoms={[
                    { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
                    { atom_index: 2, element: "H", x: 0, y: 0, z: 1.09 },
                ]}
                formula="CH"
            />,
        )
        expect(container.querySelectorAll(".viewer-bonds line")).toHaveLength(1)
    })

    it("discloses that bonds are inferred from interatomic distance, not deposited data", () => {
        const { container } = render(
            <GeometryViewer
                atoms={[
                    { atom_index: 1, element: "C", x: 0, y: 0, z: 0 },
                    { atom_index: 2, element: "H", x: 0, y: 0, z: 1.09 },
                ]}
                formula="CH"
            />,
        )
        // A distinct sentence from "not an interactive 3D molecular
        // viewer" — the two live in the same paragraph, and a mutation
        // that deletes only this sentence must not be able to hide behind
        // an assertion that only matches the other one.
        expect(container.textContent).toMatch(
            /Bonds shown are inferred from interatomic distance for legibility only; they are not part of the deposited record\./,
        )
    })
})
