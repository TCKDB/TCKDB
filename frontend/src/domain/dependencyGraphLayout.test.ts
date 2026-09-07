import { describe, expect, it } from "vitest"
import type { CalculationDependency } from "../api/calculationApi"
import {
    buildDependencyGraphModel,
    computeNarrowLayout,
    computeWideLayout,
    dependencyGraphAriaLabel,
    type GraphLayout,
} from "./dependencyGraphLayout"

/**
 * Geometric verification for both layouts, 1-4 siblings per tier -- the
 * exact range the post-review fix list asked this file to cover. jsdom
 * cannot measure real SVG geometry (no `getBBox`), so this tests the
 * LAYOUT MATH directly: every box this module computes, checked against
 * every other box, in plain 2D rectangle/segment arithmetic. No path
 * segment may intersect any label's background rect (review finding:
 * an edge's own connecting line drawing across ANOTHER edge's label
 * text, sometimes dead centre) and no label rect may intersect any node
 * rect (review finding, narrow layout: a lane label landing on top of an
 * unrelated sibling's node box).
 */

interface Rect { x: number; y: number; w: number; h: number }

function rectsIntersect(a: Rect, b: Rect): boolean {
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

/** Every one of this module's `path` strings is a plain axis-aligned
 * elbow ("M x y L x y L x y ..."), never a diagonal or curve -- parses
 * the point list and returns each consecutive pair as a segment. */
function parsePathPoints(d: string): { x: number; y: number }[] {
    const tokens = d.split(/\s+/)
    const points: { x: number; y: number }[] = []
    for (let i = 0; i < tokens.length; i++) {
        if (tokens[i] === "M" || tokens[i] === "L") {
            points.push({ x: Number(tokens[i + 1]), y: Number(tokens[i + 2]) })
            i += 2
        }
    }
    return points
}

/** Axis-aligned segment vs. rect intersection (both endpoints share
 * either x or y, per `parsePathPoints`'s own guarantee above). */
function segmentIntersectsRect(p1: { x: number; y: number }, p2: { x: number; y: number }, rect: Rect): boolean {
    const segRect: Rect = {
        x: Math.min(p1.x, p2.x),
        y: Math.min(p1.y, p2.y),
        w: Math.abs(p1.x - p2.x),
        h: Math.abs(p1.y - p2.y),
    }
    // A perfectly horizontal or vertical segment has zero width/height --
    // widen by a hair so a segment lying exactly ON a rect edge still
    // counts as touching it (the same conservative direction the review's
    // own "146px into a 152px label" measurement used).
    if (segRect.w === 0) { segRect.x -= 0.01; segRect.w = 0.02 }
    if (segRect.h === 0) { segRect.y -= 0.01; segRect.h = 0.02 }
    return rectsIntersect(segRect, rect)
}

function edgeKey(edge: GraphLayout["edges"][number]): string {
    return `${edge.role}-${edge.fromRef}-${edge.toRef}`
}

function labelRects(layout: GraphLayout): { key: string; rect: Rect }[] {
    return layout.edges.map((e) => ({
        key: edgeKey(e),
        rect: { x: e.labelX - e.labelWidth / 2, y: e.labelY - e.labelHeight / 2, w: e.labelWidth, h: e.labelHeight },
    }))
}

function nodeRects(layout: GraphLayout): Rect[] {
    return layout.nodes.map((n) => ({ x: n.x - n.width / 2, y: n.y - n.height / 2, w: n.width, h: n.height }))
}

function pathSegments(layout: GraphLayout): { key: string; p1: { x: number; y: number }; p2: { x: number; y: number } }[] {
    const segments: { key: string; p1: { x: number; y: number }; p2: { x: number; y: number } }[] = []
    for (const edge of layout.edges) {
        const points = parsePathPoints(edge.path)
        for (let i = 0; i < points.length - 1; i++) segments.push({ key: edgeKey(edge), p1: points[i], p2: points[i + 1] })
    }
    return segments
}

/**
 * An edge's OWN label sits directly ON its OWN path (deliberately -- the
 * label's opaque background "punches a hole" in its own connecting line
 * at the bend where the two meet, `calculation-dependency-graph.css`'s
 * `.dep-graph-edge-label-bg`), so a label always touches at least its own
 * two adjoining segments. What the review's fix asked this test to catch
 * is a DIFFERENT edge's line crossing through -- self-intersection is
 * excluded here by edge identity, not by geometry.
 */
function assertNoPathLabelIntersections(layout: GraphLayout, label: string) {
    const labels = labelRects(layout)
    const segments = pathSegments(layout)
    for (const [labelIndex, { key: labelKey, rect }] of labels.entries()) {
        for (const [segIndex, { key: segKey, p1, p2 }] of segments.entries()) {
            if (segKey === labelKey) continue
            expect(
                segmentIntersectsRect(p1, p2, rect),
                `${label}: path segment ${segIndex} (edge ${segKey}: ${JSON.stringify(p1)} -> ${JSON.stringify(p2)}) `
                + `intersects label rect ${labelIndex} (edge ${labelKey}) ${JSON.stringify(rect)}`,
            ).toBe(false)
        }
    }
}

function assertNoLabelNodeIntersections(layout: GraphLayout, label: string) {
    const labels = labelRects(layout)
    const nodes = nodeRects(layout)
    for (const [labelIndex, { rect: labelRect }] of labels.entries()) {
        for (const [nodeIndex, nodeRect] of nodes.entries()) {
            expect(
                rectsIntersect(labelRect, nodeRect),
                `${label}: label rect ${labelIndex} ${JSON.stringify(labelRect)} intersects node rect ${nodeIndex} ${JSON.stringify(nodeRect)}`,
            ).toBe(false)
        }
    }
}

function nodeRectByRef(layout: GraphLayout): Map<string, Rect> {
    const map = new Map<string, Rect>()
    for (const n of layout.nodes) map.set(n.ref, { x: n.x - n.width / 2, y: n.y - n.height / 2, w: n.width, h: n.height })
    return map
}

/** Shortest distance from a point to a rectangle's BORDER (not just
 * "is it inside") -- 0 for a point exactly on an edge or corner, the
 * distance to the nearest edge for a point inside, and the distance to
 * the nearest edge/corner for a point outside. */
function distanceToRectBorder(p: { x: number; y: number }, rect: Rect): number {
    const insideX = p.x >= rect.x && p.x <= rect.x + rect.w
    const insideY = p.y >= rect.y && p.y <= rect.y + rect.h
    if (insideX && insideY) {
        return Math.min(p.x - rect.x, rect.x + rect.w - p.x, p.y - rect.y, rect.y + rect.h - p.y)
    }
    const cx = Math.max(rect.x, Math.min(p.x, rect.x + rect.w))
    const cy = Math.max(rect.y, Math.min(p.y, rect.y + rect.h))
    return Math.hypot(p.x - cx, p.y - cy)
}

/**
 * Every edge's `path` is drawn FROM `fromRef`'s own node TO `toRef`'s
 * own node (`LayoutEdge`'s own docstring) -- so the path string's FIRST
 * point must sit on `fromRef`'s node border and its LAST point on
 * `toRef`'s, regardless of tier or layout. Review finding: the narrow
 * layout's centre-side entry/exit points could float outside the centre
 * node's own box (a fixed stagger step exceeding a fixed box height),
 * and a satellite node narrower than the shared column had its own
 * border to the side of where the path actually touched down.
 */
function assertPathEndpointsOnNodeBorders(layout: GraphLayout, label: string) {
    const rectsByRef = nodeRectByRef(layout)
    for (const edge of layout.edges) {
        const points = parsePathPoints(edge.path)
        const first = points[0]
        const last = points[points.length - 1]
        const fromRect = rectsByRef.get(edge.fromRef)
        const toRect = rectsByRef.get(edge.toRef)
        expect(fromRect, `${label}: no node rect for fromRef ${edge.fromRef}`).toBeDefined()
        expect(toRect, `${label}: no node rect for toRef ${edge.toRef}`).toBeDefined()
        const fromDistance = distanceToRectBorder(first, fromRect!)
        const toDistance = distanceToRectBorder(last, toRect!)
        expect(
            fromDistance,
            `${label}: edge ${edgeKey(edge)} start point ${JSON.stringify(first)} is ${fromDistance}px from ${edge.fromRef}'s border ${JSON.stringify(fromRect)}`,
        ).toBeLessThanOrEqual(0.5)
        expect(
            toDistance,
            `${label}: edge ${edgeKey(edge)} end point ${JSON.stringify(last)} is ${toDistance}px from ${edge.toRef}'s border ${JSON.stringify(toRect)}`,
        ).toBeLessThanOrEqual(0.5)
    }
}

// Realistic ref shape (`calc_<24 lowercase-alnum>`, ~29 chars) and every
// role this app has bespoke wording for -- `irc_start`'s "IRC started
// from this geometry" is the longest label this table produces (see
// `dependencyWording.ts`), so it is deliberately over-represented below.
const ROLES = ["optimized_from", "freq_on", "single_point_on", "irc_start"]

function refFor(tier: string, index: number): string {
    return `calc_${tier}${index}abcdefghijklmnopqrstuvwx`
}

function makeDependencies(parentCount: number, childCount: number): CalculationDependency[] {
    const deps: CalculationDependency[] = []
    for (let i = 0; i < parentCount; i++) {
        deps.push({
            role: ROLES[i % ROLES.length], direction: "child",
            parent_calculation_ref: refFor("parent", i), child_calculation_ref: "calc_own_ref_abcdefghijklmnopqrstuv",
        })
    }
    for (let i = 0; i < childCount; i++) {
        deps.push({
            role: ROLES[i % ROLES.length], direction: "parent",
            parent_calculation_ref: "calc_own_ref_abcdefghijklmnopqrstuv", child_calculation_ref: refFor("child", i),
        })
    }
    return deps
}

describe.each([1, 2, 3, 4])("wide layout, %i parent(s) and %i child(ren) each", (n) => {
    for (const [parentCount, childCount] of [[n, 0], [0, n], [n, n]] as const) {
        it(`no path segment intersects any label rect (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeWideLayout(model)
            assertNoPathLabelIntersections(layout, `wide p=${parentCount} c=${childCount}`)
        })

        it(`no label rect intersects any node rect (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeWideLayout(model)
            assertNoLabelNodeIntersections(layout, `wide p=${parentCount} c=${childCount}`)
        })

        it(`every label stays within the SVG's own [0, width] bounds (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeWideLayout(model)
            for (const edge of layout.edges) {
                expect(edge.labelX - edge.labelWidth / 2).toBeGreaterThanOrEqual(0)
                expect(edge.labelX + edge.labelWidth / 2).toBeLessThanOrEqual(layout.width)
            }
        })

        it(`every path's node-side endpoint lies on a node rect border (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeWideLayout(model)
            assertPathEndpointsOnNodeBorders(layout, `wide p=${parentCount} c=${childCount}`)
        })
    }
})

describe.each([1, 2, 3, 4])("narrow layout, %i parent(s) and %i child(ren) each", (n) => {
    for (const [parentCount, childCount] of [[n, 0], [0, n], [n, n]] as const) {
        it(`no path segment intersects any label rect (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeNarrowLayout(model)
            assertNoPathLabelIntersections(layout, `narrow p=${parentCount} c=${childCount}`)
        })

        it(`no label rect intersects any node rect (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeNarrowLayout(model)
            assertNoLabelNodeIntersections(layout, `narrow p=${parentCount} c=${childCount}`)
        })

        it(`is a single x column -- every node shares the SAME x (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeNarrowLayout(model)
            const xs = new Set(layout.nodes.map((n) => n.x))
            expect(xs.size).toBe(1)
        })

        it(`every lane label is anchored at x >= its own laneX (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeNarrowLayout(model)
            for (const edge of layout.edges) {
                expect(edge.laneX).toBeDefined()
                expect(edge.labelX - edge.labelWidth / 2).toBeGreaterThanOrEqual(edge.laneX!)
            }
        })

        it(`no label's right edge extends past the SVG's own width (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeNarrowLayout(model)
            for (const edge of layout.edges) {
                expect(edge.labelX + edge.labelWidth / 2).toBeLessThanOrEqual(layout.width)
            }
        })

        it(`every path's node-side endpoint lies on a node rect border (parents=${parentCount}, children=${childCount})`, () => {
            const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(parentCount, childCount))
            const layout = computeNarrowLayout(model)
            assertPathEndpointsOnNodeBorders(layout, `narrow p=${parentCount} c=${childCount}`)
        })
    }
})

describe("centre node type pill", () => {
    it("wide layout: the centre node carries a pill sized to fit typeLabel(type)", () => {
        const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(1, 1))
        const layout = computeWideLayout(model)
        const centre = layout.nodes.find((n) => n.tier === "centre")!
        expect(centre.pill).toBeDefined()
        expect(centre.pill!.label).toBe("Optimisation")
        expect(centre.pill!.width).toBeGreaterThan(0)
    })

    it("no parent/child node carries a pill", () => {
        const model = buildDependencyGraphModel("calc_own_ref_abcdefghijklmnopqrstuv", "opt", makeDependencies(2, 2))
        const layout = computeWideLayout(model)
        for (const node of layout.nodes.filter((n) => n.tier !== "centre")) {
            expect(node.pill).toBeUndefined()
        }
    })
})

describe("longest label fits inside its own background box", () => {
    it("'IRC started from this geometry' text width does not exceed its background rect width", () => {
        // Same estimate the layout module itself uses -- this pins the
        // CONSTANT, not the layout: a text-vs-box regression (the
        // pre-fix `LABEL_CHAR_W = 6.6` under-measured a real 7.6px/char
        // font) would show up here as `textWidth > bgWidth`.
        const label = "IRC started from this geometry"
        const model = buildDependencyGraphModel(
            "calc_own_ref_abcdefghijklmnopqrstuv", "opt",
            [{ role: "irc_start", direction: "parent", parent_calculation_ref: "calc_own_ref_abcdefghijklmnopqrstuv", child_calculation_ref: "calc_child0abcdefghijklmnopqrstuvwx" }],
        )
        const layout = computeWideLayout(model)
        const edge = layout.edges[0]
        expect(edge.label).toBe(label)
        const MEASURED_CHAR_W = 7.6 // this app's real rendered advance width at this font/size/tracking
        const textWidth = label.length * MEASURED_CHAR_W
        expect(textWidth).toBeLessThanOrEqual(edge.labelWidth)
    })
})

describe("dependencyGraphAriaLabel", () => {
    it("still reports singular/plural counts correctly (unchanged behaviour)", () => {
        expect(dependencyGraphAriaLabel(1, 0)).toBe("1 parent")
        expect(dependencyGraphAriaLabel(0, 1)).toBe("1 child")
        expect(dependencyGraphAriaLabel(2, 3)).toBe("2 parents, 3 children")
    })
})
