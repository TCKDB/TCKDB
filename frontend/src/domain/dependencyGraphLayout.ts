/**
 * Pure geometry for `CalculationDependencyGraph.tsx` -- no charting library,
 * same precedent as `ThermoCpChart.tsx`/`domain/thermoCpChartLayout.ts`
 * (hand-rolled SVG, this project has no plotting dependency in
 * `package.json`). Kept out of the component so the math is unit-testable
 * without a DOM, and so the component itself stays a thin render of
 * whatever this module computes -- jsdom lays out and measures nothing
 * (no `getBBox`), so box sizes below are estimated from character counts
 * at a fixed monospace step, never measured at render time. That estimate
 * is generous enough for this archive's `calc_<24 lowercase-alnum>` refs
 * (~29 characters) to fit on one line; a real browser is where the visual
 * fit is actually confirmed (screenshots at 1920/680 in the PR).
 *
 * Two full, independent layouts -- `computeWideLayout` (parents/children
 * spread horizontally either side of the centre) and `computeNarrowLayout`
 * (everything in one vertical column, edges routed through a side lane
 * gutter) -- rather than one layout algebraically reflowed by breakpoint.
 * `CalculationDependencyGraph.tsx` renders BOTH into the DOM and lets CSS
 * (`calculation-dependency-graph.css`, `max-width: 680px`) pick which is
 * visible: a single SVG scaled down via `width: 100%` would shrink text
 * below the accessible floor once several siblings share a row, which is
 * exactly the case a narrow viewport is more likely to hit.
 */

import type { CalculationDependency } from "../api/calculationApi"
import { dependencyEdgeLabel } from "./dependencyWording"

// ---------------------------------------------------------------------------
// Node / edge extraction (direction-neutral, orientation-agnostic)
// ---------------------------------------------------------------------------

export interface DependencyGraphEdgeSpec {
    role: string
    label: string
    /** The OTHER calculation's ref -- never `ownRef`. */
    otherRef: string
    /** Which side of the centre this edge's other-calculation sits on. */
    tier: "parent" | "child"
}

export interface DependencyGraphModel {
    ownRef: string
    ownType: string
    /** Distinct parent refs, in first-seen order. */
    parentRefs: string[]
    /** Distinct child refs, in first-seen order. */
    childRefs: string[]
    /** One entry per dependency row (NOT deduped -- two roles pointing at
     * the same ref are two edges into the same node). */
    edges: DependencyGraphEdgeSpec[]
}

/**
 * `dep.direction` names which side of the edge THIS calculation (`ownRef`)
 * sits on, not the edge's own parent->child direction -- see
 * `dependencySentence`'s docstring in `CalculationDetailPage.tsx`, which
 * this mirrors:
 * - `direction === "child"`: this calc is the CHILD; `parent_calculation_ref`
 *   names a PARENT (tier "parent", drawn above centre).
 * - `direction === "parent"`: this calc is the PARENT; `child_calculation_ref`
 *   names a CHILD (tier "child", drawn below centre).
 */
export function buildDependencyGraphModel(
    ownRef: string,
    ownType: string,
    dependencies: CalculationDependency[],
): DependencyGraphModel {
    const parentRefs: string[] = []
    const childRefs: string[] = []
    const edges: DependencyGraphEdgeSpec[] = []
    const seenParents = new Set<string>()
    const seenChildren = new Set<string>()

    for (const dep of dependencies) {
        const label = dependencyEdgeLabel(dep.role)
        if (dep.direction === "child") {
            const otherRef = dep.parent_calculation_ref
            if (!seenParents.has(otherRef)) {
                seenParents.add(otherRef)
                parentRefs.push(otherRef)
            }
            edges.push({ role: dep.role, label, otherRef, tier: "parent" })
        } else {
            const otherRef = dep.child_calculation_ref
            if (!seenChildren.has(otherRef)) {
                seenChildren.add(otherRef)
                childRefs.push(otherRef)
            }
            edges.push({ role: dep.role, label, otherRef, tier: "child" })
        }
    }

    return { ownRef, ownType, parentRefs, childRefs, edges }
}

/** "2 parents, 1 child" / "1 parent" / "3 children" -- the `aria-label`
 * summarising the whole graph. Never mentions a zero-count side. */
export function dependencyGraphAriaLabel(parentCount: number, childCount: number): string {
    const parts: string[] = []
    if (parentCount > 0) parts.push(`${parentCount} ${parentCount === 1 ? "parent" : "parents"}`)
    if (childCount > 0) parts.push(`${childCount} ${childCount === 1 ? "child" : "children"}`)
    return parts.join(", ")
}

// ---------------------------------------------------------------------------
// Shared box-sizing estimate
// ---------------------------------------------------------------------------

const CHAR_W = 7.5
const NODE_MIN_W = 140
const NODE_PAD_X = 24
const NODE_H = 40
const CENTRE_H = 58
const LABEL_CHAR_W = 6.6
const LABEL_PAD_X = 10
const LABEL_H = 18

function nodeWidth(ref: string): number {
    return Math.max(NODE_MIN_W, Math.round(ref.length * CHAR_W) + NODE_PAD_X)
}

function labelWidth(label: string): number {
    return Math.round(label.length * LABEL_CHAR_W) + LABEL_PAD_X * 2
}

export interface LayoutNode {
    ref: string
    tier: "parent" | "centre" | "child"
    type: string | null
    x: number
    y: number
    width: number
    height: number
}

export interface LayoutEdge {
    role: string
    label: string
    fromRef: string
    toRef: string
    /** SVG `<path d>`, always drawn FROM the source (parent) TO the
     * destination (child) -- `marker-end` (always the last point) is
     * therefore always the arrowhead in the parent -> child direction. */
    path: string
    labelX: number
    labelY: number
    labelWidth: number
    labelHeight: number
}

export interface GraphLayout {
    width: number
    height: number
    nodes: LayoutNode[]
    edges: LayoutEdge[]
}

// ---------------------------------------------------------------------------
// Wide layout: parents in a horizontal row above, children in a horizontal
// row below, each edge a 3-segment elbow into the centre, staggered into
// its own horizontal "band" per edge (so N edges converging on the centre
// never overlap each other or share a label row).
// ---------------------------------------------------------------------------

const WIDE_GAP_X = 24
const WIDE_MARGIN = 24
const WIDE_BAND_STEP = 26

function layoutWideRow(refs: string[], rowCenterX: number, y: number): { boxes: Map<string, { x: number; width: number }>; rowWidth: number } {
    const widths = refs.map(nodeWidth)
    const rowWidth = widths.reduce((sum, w) => sum + w, 0) + WIDE_GAP_X * Math.max(0, refs.length - 1)
    let cursor = rowCenterX - rowWidth / 2
    const boxes = new Map<string, { x: number; width: number }>()
    refs.forEach((ref, index) => {
        const width = widths[index]
        boxes.set(ref, { x: cursor + width / 2, width })
        cursor += width + WIDE_GAP_X
    })
    void y
    return { boxes, rowWidth }
}

export function computeWideLayout(model: DependencyGraphModel): GraphLayout {
    const { ownRef, ownType, parentRefs, childRefs, edges } = model
    const centreWidth = nodeWidth(ownRef)
    const parentWidths = parentRefs.map(nodeWidth)
    const childWidths = childRefs.map(nodeWidth)
    const parentRowWidth = parentWidths.reduce((s, w) => s + w, 0) + WIDE_GAP_X * Math.max(0, parentRefs.length - 1)
    const childRowWidth = childWidths.reduce((s, w) => s + w, 0) + WIDE_GAP_X * Math.max(0, childRefs.length - 1)
    const contentWidth = Math.max(centreWidth, parentRowWidth, childRowWidth)
    const svgWidth = contentWidth + WIDE_MARGIN * 2
    const centreX = svgWidth / 2

    const parentEdgeCount = edges.filter((e) => e.tier === "parent").length
    const childEdgeCount = edges.filter((e) => e.tier === "child").length
    const parentTierGap = parentRefs.length > 0 ? WIDE_BAND_STEP * (parentEdgeCount + 1) : 0
    const childTierGap = childRefs.length > 0 ? WIDE_BAND_STEP * (childEdgeCount + 1) : 0

    let cursorY = WIDE_MARGIN
    const nodes: LayoutNode[] = []
    let parentRow: { boxes: Map<string, { x: number; width: number }> } | null = null
    let parentTierBottomY = cursorY

    if (parentRefs.length > 0) {
        const row = layoutWideRow(parentRefs, centreX, cursorY)
        parentRow = row
        parentRefs.forEach((ref) => {
            const box = row.boxes.get(ref)!
            nodes.push({ ref, tier: "parent", type: null, x: box.x, y: cursorY + NODE_H / 2, width: box.width, height: NODE_H })
        })
        parentTierBottomY = cursorY + NODE_H
        cursorY = parentTierBottomY + parentTierGap
    }

    const centreTopY = cursorY
    nodes.push({ ref: ownRef, tier: "centre", type: ownType, x: centreX, y: centreTopY + CENTRE_H / 2, width: centreWidth, height: CENTRE_H })
    const centreBottomY = centreTopY + CENTRE_H
    cursorY = centreBottomY + childTierGap

    let childRow: { boxes: Map<string, { x: number; width: number }> } | null = null
    const childTierTopY = cursorY
    if (childRefs.length > 0) {
        const row = layoutWideRow(childRefs, centreX, cursorY)
        childRow = row
        childRefs.forEach((ref) => {
            const box = row.boxes.get(ref)!
            nodes.push({ ref, tier: "child", type: null, x: box.x, y: cursorY + NODE_H / 2, width: box.width, height: NODE_H })
        })
        cursorY = cursorY + NODE_H
    }

    const svgHeight = cursorY + WIDE_MARGIN

    const edgesOut: LayoutEdge[] = []
    let parentBandIndex = 0
    let childBandIndex = 0
    for (const edge of edges) {
        if (edge.tier === "parent" && parentRow) {
            parentBandIndex += 1
            const box = parentRow.boxes.get(edge.otherRef)!
            const bandY = parentTierBottomY + WIDE_BAND_STEP * parentBandIndex
            const path = `M ${box.x} ${parentTierBottomY} L ${box.x} ${bandY} L ${centreX} ${bandY} L ${centreX} ${centreTopY}`
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: edge.otherRef, toRef: ownRef, path,
                labelX: (box.x + centreX) / 2, labelY: bandY - 6, labelWidth: labelWidth(edge.label), labelHeight: LABEL_H,
            })
        } else if (edge.tier === "child" && childRow) {
            childBandIndex += 1
            const box = childRow.boxes.get(edge.otherRef)!
            const bandY = centreBottomY + WIDE_BAND_STEP * childBandIndex
            const path = `M ${centreX} ${centreBottomY} L ${centreX} ${bandY} L ${box.x} ${bandY} L ${box.x} ${childTierTopY}`
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: ownRef, toRef: edge.otherRef, path,
                labelX: (centreX + box.x) / 2, labelY: bandY - 6, labelWidth: labelWidth(edge.label), labelHeight: LABEL_H,
            })
        }
    }

    return { width: svgWidth, height: svgHeight, nodes, edges: edgesOut }
}

// ---------------------------------------------------------------------------
// Narrow layout (<=680px): single vertical column (parents, then centre,
// then children), each non-centre node's edge routed through its own lane
// in a side gutter so N siblings stacked in the same tier never need to
// cross each other or the centre box.
// ---------------------------------------------------------------------------

const NARROW_MARGIN = 20
const NARROW_GAP_Y = 14
const NARROW_TIER_GAP = 22
const NARROW_LANE_GUTTER = 20
const NARROW_LANE_STEP = 16

export function computeNarrowLayout(model: DependencyGraphModel): GraphLayout {
    const { ownRef, ownType, parentRefs, childRefs, edges } = model
    const allWidths = [nodeWidth(ownRef), ...parentRefs.map(nodeWidth), ...childRefs.map(nodeWidth)]
    const colWidth = Math.max(...allWidths)
    const centerX = NARROW_MARGIN + colWidth / 2

    const parentEdgeCount = edges.filter((e) => e.tier === "parent").length
    const childEdgeCount = edges.filter((e) => e.tier === "child").length
    const laneCount = Math.max(parentEdgeCount, childEdgeCount, 1)
    const gutterX = centerX + colWidth / 2 + NARROW_LANE_GUTTER
    const maxLabelWidth = Math.max(0, ...edges.map((e) => labelWidth(e.label)))
    const svgWidth = gutterX + NARROW_LANE_STEP * laneCount + maxLabelWidth + NARROW_MARGIN

    const nodes: LayoutNode[] = []
    let y = NARROW_MARGIN
    const parentCenters = new Map<string, number>()
    for (const ref of parentRefs) {
        const width = nodeWidth(ref)
        const centerY = y + NODE_H / 2
        nodes.push({ ref, tier: "parent", type: null, x: centerX, y: centerY, width, height: NODE_H })
        parentCenters.set(ref, centerY)
        y += NODE_H + NARROW_GAP_Y
    }
    if (parentRefs.length > 0) y += NARROW_TIER_GAP - NARROW_GAP_Y

    const centreCenterY = y + CENTRE_H / 2
    nodes.push({ ref: ownRef, tier: "centre", type: ownType, x: centerX, y: centreCenterY, width: nodeWidth(ownRef), height: CENTRE_H })
    y += CENTRE_H

    if (childRefs.length > 0) y += NARROW_TIER_GAP

    const childCenters = new Map<string, number>()
    for (const ref of childRefs) {
        const width = nodeWidth(ref)
        const centerY = y + NODE_H / 2
        nodes.push({ ref, tier: "child", type: null, x: centerX, y: centerY, width, height: NODE_H })
        childCenters.set(ref, centerY)
        y += NODE_H + NARROW_GAP_Y
    }
    const svgHeight = (childRefs.length > 0 ? y - NARROW_GAP_Y : y) + NARROW_MARGIN

    const rightEdgeX = centerX + colWidth / 2
    // Every label is LEFT-anchored at its own lane (`labelX` is the box's
    // CENTER, so `laneX + halfWidth` puts its left edge exactly at the
    // lane) rather than centred on it. MEASURED (screenshot review,
    // 680px): a centred label straddled back over the node column by half
    // its own width -- for the second (or later) sibling in a stacked
    // tier, that put the label's box on top of an INTERVENING sibling's
    // node box, not just its own two endpoints. Left-anchoring keeps the
    // whole label box at x >= laneX > rightEdgeX, strictly to the right of
    // every node in the column, so it can never overlap ANY node
    // regardless of which lane or which Y it sits at -- the label's Y can
    // go back to a plain centre-to-centre midpoint of the two endpoints
    // it connects, since there is no longer a box for it to land inside.
    const edgesOut: LayoutEdge[] = []
    let parentLane = 0
    let childLane = 0
    for (const edge of edges) {
        if (edge.tier === "parent") {
            parentLane += 1
            const laneX = gutterX + NARROW_LANE_STEP * parentLane
            const nodeY = parentCenters.get(edge.otherRef)!
            const path = `M ${rightEdgeX} ${nodeY} L ${laneX} ${nodeY} L ${laneX} ${centreCenterY} L ${rightEdgeX} ${centreCenterY}`
            const width = labelWidth(edge.label)
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: edge.otherRef, toRef: ownRef, path,
                labelX: laneX + width / 2, labelY: (nodeY + centreCenterY) / 2, labelWidth: width, labelHeight: LABEL_H,
            })
        } else {
            childLane += 1
            const laneX = gutterX + NARROW_LANE_STEP * childLane
            const nodeY = childCenters.get(edge.otherRef)!
            const path = `M ${rightEdgeX} ${centreCenterY} L ${laneX} ${centreCenterY} L ${laneX} ${nodeY} L ${rightEdgeX} ${nodeY}`
            const width = labelWidth(edge.label)
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: ownRef, toRef: edge.otherRef, path,
                labelX: laneX + width / 2, labelY: (centreCenterY + nodeY) / 2, labelWidth: width, labelHeight: LABEL_H,
            })
        }
    }

    return { width: svgWidth, height: svgHeight, nodes, edges: edgesOut }
}
