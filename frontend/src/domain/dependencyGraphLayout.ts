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
 * fit is actually confirmed (screenshots at 1920/1100/680 in the PR).
 *
 * Two full, independent layouts -- `computeWideLayout` (parents/children
 * spread horizontally either side of the centre) and `computeNarrowLayout`
 * (everything in one vertical column, edges routed through a side lane
 * gutter). `CalculationDependencyGraph.tsx` picks between them at RENDER
 * TIME, from a real measured container width (`ResizeObserver`) compared
 * against `computeWideLayout(...).width` -- never a fixed breakpoint and
 * never two SVGs left in the DOM for CSS to choose between. A fixed
 * `680px` media query (an earlier version of this module) let the wide
 * layout scale itself down via `width: 100%` on any container narrower
 * than its own natural width but wider than 680px -- MEASURED: at a
 * 1100px container with 3 children the SVG scaled to 0.913x, and its
 * smallest text (`--type-label-font`, 11.52px) rendered at 10.52px, below
 * this app's own accessible floor. Comparing the real container width to
 * the wide layout's own computed width switches to the narrow layout
 * BEFORE any such shrink happens, so the wide layout only ever renders at
 * exactly 1.000 scale (`CalculationDependencyGraph.tsx`'s inline
 * `maxWidth` still caps it from scaling UP past that).
 *
 * ---------------------------------------------------------------------
 * Band ordering (wide layout): an edge's band depth comes from how far
 * its sibling sits from the centre column, not array order, so the shared
 * trunk at `centreX` never crosses a label. Each label sits beside its
 * sibling-end drop (or above its run) and never on any edge line.
 * ---------------------------------------------------------------------
 */

import type { CalculationDependency } from "../api/calculationApi"
import { typeLabel } from "./calculationTypeFormat"
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
    /** Short label for the centre node's own SUBJECT row -- see
     * `CalculationDependencyGraph.tsx`'s `centreSubject` prop's own
     * docstring for what this answers ("an optimisation OF WHAT",
     * distinct from the pill's "what KIND" and the ref line's "WHICH
     * one"). `undefined` for every caller that doesn't have a subject to
     * show (`CalculationDetailPage.tsx`'s own graph) -- in which case the
     * centre node's content block stays exactly `CENTRE_H` tall, byte-
     * identical to this row's own geometry before the subject existed. */
    centreSubjectLabel?: string
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
    centreSubjectLabel?: string,
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

    return { ownRef, ownType, centreSubjectLabel, parentRefs, childRefs, edges }
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

// Node ref text: `--type-data-font` (.8125rem/13px mono).
const CHAR_W = 7.5
const NODE_MIN_W = 140
const NODE_PAD_X = 24
const NODE_H = 40
// `rx` on every node's own `<rect>` (`CalculationDependencyGraph.tsx`'s
// `DependencyGraphNode`, which imports this constant rather than
// hard-coding its own copy). Exported so `centreHeightFor` below can
// reserve room for it: a point sitting exactly ON the flat half-height
// line lands INSIDE the rounded corner's cut, not on the actual drawn
// border, once it's within `rx` of a corner.
export const NODE_RX = 6

// Edge labels and the centre node's type pill use `--type-label-font` / `--type-label-strong-font`
// (.8125rem sans). 7.6px per character is a conservative upper bound for that face, so the longest label
// still fits; `SMALL_PAD` is 6px on each side, the slack the text-vs-background-box test checks for.
const SMALL_CHAR_W = 7.6
const SMALL_PAD = 6
const LABEL_H = 18
const PILL_H = 18

// Centre node: padding, the pill, a gap, then the ref line.
// Exported alongside `CENTRE_H` so a test can independently compute the
// pill+ref content block's own top/bottom edges (`CalculationDependency
// Graph.tsx`'s `contentTop = top + (node.height - CENTRE_H) / 2`, then
// `pillY = contentTop + CENTRE_PAD_TOP`) without re-deriving the same
// formula it is meant to be checking.
export const CENTRE_PAD_TOP = 10
const CENTRE_PILL_GAP = 8
const CENTRE_REF_LINE_H = 24
const CENTRE_PAD_BOTTOM = 12
// Exported so `CalculationDependencyGraph.tsx` can vertically CENTRE the
// pill+ref content block inside a centre node taller than this base
// height (the narrow layout grows the centre node past `CENTRE_H` to fit
// staggered lane entries -- see `centreHeightFor` -- but the content
// itself never grows, so it has to be re-centred in the extra room
// rather than staying pinned to the top).
export const CENTRE_H = CENTRE_PAD_TOP + PILL_H + CENTRE_PILL_GAP + CENTRE_REF_LINE_H + CENTRE_PAD_BOTTOM

// The centre node's optional SUBJECT row -- "an optimisation OF WHAT"
// (owner: "what the fuck is it? a ts? a random ass molecule?"), inserted
// BETWEEN the type pill and the ref line rather than replacing either --
// the pill still answers WHAT KIND of calculation, the ref line still
// answers WHICH one, and this row alone answers WHOSE. Same small font
// step as the pill/edge labels (`SMALL_CHAR_W`, `PILL_H`-adjacent line
// height), but never a duplicate concept: `CalculationDependencyGraph
// .tsx`'s `centreSubject` prop's own docstring covers the accessibility
// shape (a real link, its accessible name carrying the subject's own
// ref, since the visible text here is deliberately short).
export const CENTRE_SUBJECT_GAP_TOP = 6
export const CENTRE_SUBJECT_LINE_H = 16
export const CENTRE_SUBJECT_GAP_BOTTOM = 6

/** The centre node's own pill(+subject)+ref content block height --
 * `CENTRE_H` (pill, then straight to the ref line) when there is no
 * subject, or `CENTRE_H` widened by the subject row and its own two gaps
 * (replacing the plain `CENTRE_PILL_GAP` the no-subject case uses)
 * otherwise. Exported so `CalculationDependencyGraph.tsx` can re-derive
 * the SAME box height this module used to size the node, rather than
 * re-deriving a second copy of the split -- mirrors how `CENTRE_PAD_TOP`
 * is already shared this way for the pill's own vertical offset. */
export function centreContentHeight(hasSubject: boolean): number {
    const middle = hasSubject
        ? CENTRE_SUBJECT_GAP_TOP + CENTRE_SUBJECT_LINE_H + CENTRE_SUBJECT_GAP_BOTTOM
        : CENTRE_PILL_GAP
    return CENTRE_PAD_TOP + PILL_H + middle + CENTRE_REF_LINE_H + CENTRE_PAD_BOTTOM
}

function nodeWidth(ref: string): number {
    return Math.max(NODE_MIN_W, Math.round(ref.length * CHAR_W) + NODE_PAD_X)
}

/** Shared sizing for an edge label OR the centre node's type pill --
 * both draw at the same small, tracked, uppercase mono step. */
function smallTextBoxWidth(text: string): number {
    return Math.round(text.length * SMALL_CHAR_W) + SMALL_PAD * 2
}

/** The centre node's own width -- `nodeWidth(ownRef)`, widened to fit the
 * subject row's own text when one is present and would otherwise be
 * wider than the ref-based box (never narrower: a short subject label
 * like "Transition state" is well under a real `calc_...` ref's own
 * width in every case measured, but this stays a `Math.max` rather than
 * an assumption). Sized with `smallTextBoxWidth` -- the SAME estimate the
 * subject row's own font step uses (matching `SMALL_PAD`'s slack), not
 * the ref line's wider `CHAR_W`/`NODE_PAD_X` estimate. */
function centreNodeWidth(ownRef: string, subjectLabel: string | undefined): number {
    const base = nodeWidth(ownRef)
    if (!subjectLabel) return base
    return Math.max(base, smallTextBoxWidth(subjectLabel))
}

export interface LayoutNode {
    ref: string
    tier: "parent" | "centre" | "child"
    type: string | null
    x: number
    y: number
    width: number
    height: number
    /** Centre node only: the type-pill box drawn INSIDE the node (a real
     * `<rect rx>`, not bare text -- see `.value-pill`'s own COLOUR and
     * RADIUS treatment, which this matches (`--accent-50` fill, rounded
     * corners); the STROKE (`--accent-300`, not `.value-pill`'s own
     * `--line`) and the FONT (the SVG label step, not `.value-pill`'s
     * own) both differ -- see `calculation-dependency-graph.css`'s own
     * `.dep-graph-node-pill-bg` comment for why the stroke differs.
     * `label` is `typeLabel(type)` -- computed once here so the component
     * never re-derives what text the pill sizing was based on. */
    pill?: { width: number; height: number; label: string }
}

export interface LayoutEdge {
    role: string
    label: string
    fromRef: string
    toRef: string
    /** SVG `<path d>`, always drawn FROM the source (parent) TO the
     * destination (child) -- `marker-end` (always the last point) is
     * therefore always the arrowhead in the parent -> child direction.
     * Every segment is axis-aligned (a plain elbow, never a diagonal). */
    path: string
    labelX: number
    labelY: number
    labelWidth: number
    labelHeight: number
    /** Which end of the label box the text hugs; the end nearest its own line. Default: centred. */
    labelAnchor?: "start" | "end"
    /** Narrow layout only: the lane `x` this edge's stub and label both
     * anchor to (`labelX - labelWidth / 2 === laneX`, checked directly by
     * `dependencyGraphLayout.test.ts`). Undefined for a wide-layout edge,
     * which has no lane. */
    laneX?: number
}

export interface GraphLayout {
    width: number
    height: number
    nodes: LayoutNode[]
    edges: LayoutEdge[]
}

function centrePillFor(ownType: string): { width: number; height: number; label: string } {
    const label = typeLabel(ownType)
    return { width: smallTextBoxWidth(label), height: PILL_H, label }
}

// ---------------------------------------------------------------------------
// Wide layout: parents in a horizontal row above, children in a horizontal
// row below, each edge a 3-segment elbow into the centre. See the module
// docstring's "Band ordering" section for why band DEPTH is assigned by
// each edge's own clearance from `centreX`, not by array order.
// ---------------------------------------------------------------------------

const WIDE_GAP_X_MIN = 24
const WIDE_MARGIN = 24
const WIDE_BAND_STEP = 26
// Extra drop length per edge, so each label gets its own slot beside a drop.
const WIDE_LABEL_SLOT = LABEL_H + 10

function layoutWideRow(
    refs: string[], rowCenterX: number, gapX: number,
): { boxes: Map<string, { x: number; width: number }>; rowWidth: number } {
    const widths = refs.map(nodeWidth)
    const rowWidth = widths.reduce((sum, w) => sum + w, 0) + gapX * Math.max(0, refs.length - 1)
    let cursor = rowCenterX - rowWidth / 2
    const boxes = new Map<string, { x: number; width: number }>()
    refs.forEach((ref, index) => {
        const width = widths[index]
        boxes.set(ref, { x: cursor + width / 2, width })
        cursor += width + gapX
    })
    return { boxes, rowWidth }
}

/**
 * See the module docstring's "Band ordering" section. Returns each edge's
 * 1-indexed band depth.
 *
 * A PARENT-tier path's shared run (at `centreX`) is its LAST segment,
 * from its own band down to `centreTopY` -- so edge i's shared run spans
 * every band DEEPER than its own (`j >= i`), and band 1 (nearest the
 * sibling row) is the only band nothing ever shares that column through.
 *
 * A CHILD-tier path's shared run is its FIRST segment instead, from
 * `centreBottomY` down to its own band -- the mirror image -- so edge i's
 * shared run spans every band SHALLOWER than its own (`j <= i`), and
 * band N (nearest the sibling row, THE LAST band) is the one nothing
 * else's shared run reaches.
 *
 * Either way, exactly one band position is never shared through by any
 * OTHER edge's trunk; `uncrossedBand` tells this function which end that
 * is for the tier being laid out, and the edge with the WORST (most
 * negative) clearance -- the one most likely to have a label reaching
 * `centreX` -- is the one assigned to it. */
function assignBandOrder(
    tierEdges: DependencyGraphEdgeSpec[],
    boxes: Map<string, { x: number }>,
    centreX: number,
    uncrossedBand: "shallowest" | "deepest",
): Map<DependencyGraphEdgeSpec, number> {
    const withClearance = tierEdges.map((edge, arrayIndex) => {
        const boxX = boxes.get(edge.otherRef)!.x
        const clearance = Math.abs(boxX - centreX) - smallTextBoxWidth(edge.label) / 2
        return { edge, arrayIndex, clearance }
    })
    // Worst (most negative) clearance goes to whichever end is the
    // uncrossed band for this tier; best (most positive) clearance goes
    // to the other end, since it is crossed regardless but safely --
    // its own label falls well short of centreX. Stable on the original
    // array order for a genuine tie.
    const direction = uncrossedBand === "shallowest" ? 1 : -1
    withClearance.sort((a, b) => direction * (a.clearance - b.clearance) || a.arrayIndex - b.arrayIndex)
    const order = new Map<DependencyGraphEdgeSpec, number>()
    withClearance.forEach((item, index) => order.set(item.edge, index + 1))
    return order
}

/** How far the row's OUTERMOST (first/last) sibling's own widest label
 * extends past that sibling's own node box, beyond the box's plain half-
 * width -- see `computeWideLayout`'s own comment for why this is the one
 * gap the inter-sibling `parentGapX`/`childGapX` spacing does not cover.
 * A ref can carry more than one edge (two roles pointing at the same
 * node), so this takes the WIDEST label among any edge whose `otherRef`
 * is that outer box, not just the first edge encountered. Zero for an
 * empty tier or whenever every label already fits inside its own box. */
function outerLabelOverhang(tierEdges: DependencyGraphEdgeSpec[], refs: string[], widths: number[]): number {
    if (refs.length === 0) return 0
    const labelWidthFor = (ref: string) =>
        Math.max(0, ...tierEdges.filter((e) => e.otherRef === ref).map((e) => smallTextBoxWidth(e.label)))
    const leftOverhang = labelWidthFor(refs[0]) / 2 - widths[0] / 2
    const rightOverhang = labelWidthFor(refs[refs.length - 1]) / 2 - widths[widths.length - 1] / 2
    return Math.max(0, leftOverhang, rightOverhang)
}

const LABEL_GAP = 10
const LABEL_CLEAR = 3

interface Pt { x: number; y: number }
interface Box { x: number; y: number; w: number; h: number }

function pathPoints(d: string): Pt[] {
    const t = d.split(/\s+/)
    const pts: Pt[] = []
    for (let i = 0; i < t.length; i++) {
        if (t[i] === "M" || t[i] === "L") { pts.push({ x: Number(t[i + 1]), y: Number(t[i + 2]) }); i += 2 }
    }
    return pts
}

function boxesOverlap(a: Box, b: Box): boolean {
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

/** Puts each label beside its sibling-end vertical drop (right, then left) or above its horizontal run, at
 * the first spot `LABEL_CLEAR` px clear of every edge segment, node and earlier label. */
function placeWideLabels(edges: LayoutEdge[], nodes: LayoutNode[], centreRef: string, width: number): boolean {
    let allFit = true
    const segs: Box[] = []
    for (const e of edges) {
        const p = pathPoints(e.path)
        for (let i = 0; i < p.length - 1; i++) {
            segs.push({
                x: Math.min(p[i].x, p[i + 1].x), y: Math.min(p[i].y, p[i + 1].y),
                w: Math.max(0.02, Math.abs(p[i].x - p[i + 1].x)), h: Math.max(0.02, Math.abs(p[i].y - p[i + 1].y)),
            })
        }
    }
    const obstacles: Box[] = [
        ...segs,
        ...nodes.map((n) => ({ x: n.x - n.width / 2, y: n.y - n.height / 2, w: n.width, h: n.height })),
    ]
    const placed: Box[] = []
    for (const e of edges) {
        const p = pathPoints(e.path)
        const parentTier = e.toRef === centreRef
        // Sibling-end drop and the run beside it.
        const [v1, v2] = parentTier ? [p[0], p[1]] : [p[2], p[3]]
        const runEnd = parentTier ? p[2] : p[1]
        const w = e.labelWidth
        const h = e.labelHeight
        const top = Math.min(v1.y, v2.y)
        const bottom = Math.max(v1.y, v2.y)
        const mid = (top + bottom) / 2
        const cands: Box[] = []
        for (const x0 of [v1.x + LABEL_GAP, v1.x - LABEL_GAP - w]) {
            for (let d = 0; d <= bottom - top; d += 2) {
                for (const sign of d === 0 ? [1] : [1, -1]) {
                    const cy = mid + sign * d
                    if (cy - h / 2 >= top && cy + h / 2 <= bottom) cands.push({ x: x0, y: cy - h / 2, w, h })
                }
            }
        }
        const dir = runEnd.x > v1.x ? 1 : -1
        for (let off = 0; off <= 160; off += 8) {
            const x0 = dir > 0 ? v1.x + LABEL_GAP + off : v1.x - LABEL_GAP - off - w
            cands.push({ x: x0, y: runEnd.y - 5 - h, w, h })
        }
        const fits = (c: Box) => {
            const inflated = { x: c.x - LABEL_CLEAR, y: c.y - LABEL_CLEAR, w: c.w + 2 * LABEL_CLEAR, h: c.h + 2 * LABEL_CLEAR }
            return c.x >= 0 && c.x + c.w <= width
                && !obstacles.some((o) => boxesOverlap(inflated, o))
                && !placed.some((o) => boxesOverlap(inflated, o))
        }
        const found = cands.find(fits)
        if (!found) allFit = false
        const chosen = found ?? cands[0]
        placed.push(chosen)
        e.labelX = chosen.x + w / 2
        e.labelY = chosen.y + h / 2
        e.labelAnchor = chosen.x + w / 2 < v1.x ? "end" : "start"
    }
    return allFit
}

export function computeWideLayout(model: DependencyGraphModel, extraMargin = 0): GraphLayout {
    const { ownRef, ownType, centreSubjectLabel, parentRefs, childRefs, edges } = model
    const centreWidth = centreNodeWidth(ownRef, centreSubjectLabel)
    const centreH = centreContentHeight(Boolean(centreSubjectLabel))
    const pill = centrePillFor(ownType)

    const parentEdges = edges.filter((e) => e.tier === "parent")
    const childEdges = edges.filter((e) => e.tier === "child")
    // The gap between sibling boxes must be wide enough that a label
    // CENTRED ON ITS OWN BOX (see below) cannot reach a neighbour's box
    // OR a neighbour's label -- sized off the widest label in the tier,
    // not a flat constant, since a long label
    // ("continued by the IRC follow-up", 240px) is wider than this
    // app's own minimum node box (140px).
    const maxParentLabelW = Math.max(0, ...parentEdges.map((e) => smallTextBoxWidth(e.label)))
    const maxChildLabelW = Math.max(0, ...childEdges.map((e) => smallTextBoxWidth(e.label)))
    const parentGapX = Math.max(WIDE_GAP_X_MIN, maxParentLabelW / 2 + 16)
    const childGapX = Math.max(WIDE_GAP_X_MIN, maxChildLabelW / 2 + 16)

    const parentWidths = parentRefs.map(nodeWidth)
    const childWidths = childRefs.map(nodeWidth)
    const parentRowWidth = parentWidths.reduce((s, w) => s + w, 0) + parentGapX * Math.max(0, parentRefs.length - 1)
    const childRowWidth = childWidths.reduce((s, w) => s + w, 0) + childGapX * Math.max(0, childRefs.length - 1)
    const contentWidth = Math.max(centreWidth, parentRowWidth, childRowWidth)
    // The inter-sibling gaps above (`parentGapX`/`childGapX`) only keep a
    // label clear of its NEIGHBOUR -- the row's own OUTERMOST sibling
    // (first/last in `parentRefs`/`childRefs`) has no neighbour on its
    // outward side, only the plain `WIDE_MARGIN`. Once a label is wider
    // than its own node box, that flat margin alone is not enough and
    // the label's own background rect can stick out past `[0, svgWidth]`
    // -- MEASURED (post-review, longer edge-label wording, using this
    // archive's real `calc_<26 lowercase-alnum>` refs, 31 characters ->
    // a 257px node box): a lone child box with `optimized_from`'s
    // original 43-character label ("starting geometry for the fine
    // optimisation", 339px) landed 17px past x=0. That specific label
    // was shortened in review (see `dependencyWording.ts`) and no longer
    // clips at this exact box width, but the fix stays: it is a general
    // guard against any tier whose one label genuinely outgrows its own
    // box, not tied to today's specific strings or ref length.
    // `outerLabelOverhang` is how much EXTRA half-width the margin needs
    // on top of `WIDE_MARGIN` to cover the worst case across both tiers;
    // adding it to BOTH sides keeps the row (still centred on `centreX`)
    // entirely inside the SVG regardless of which end the long label
    // sits on.
    const outerMargin = WIDE_MARGIN + extraMargin + Math.max(
        outerLabelOverhang(parentEdges, parentRefs, parentWidths),
        outerLabelOverhang(childEdges, childRefs, childWidths),
    )
    const svgWidth = contentWidth + outerMargin * 2
    const centreX = svgWidth / 2

    const parentEdgeCount = parentEdges.length
    const childEdgeCount = childEdges.length
    const parentTierGap = parentRefs.length > 0 ? (WIDE_BAND_STEP + WIDE_LABEL_SLOT) * parentEdgeCount + WIDE_BAND_STEP : 0
    const childTierGap = childRefs.length > 0 ? (WIDE_BAND_STEP + WIDE_LABEL_SLOT) * childEdgeCount + WIDE_BAND_STEP : 0

    let cursorY = WIDE_MARGIN
    const nodes: LayoutNode[] = []
    let parentRow: { boxes: Map<string, { x: number; width: number }> } | null = null
    let parentTierBottomY = cursorY

    if (parentRefs.length > 0) {
        const row = layoutWideRow(parentRefs, centreX, parentGapX)
        parentRow = row
        parentRefs.forEach((ref) => {
            const box = row.boxes.get(ref)!
            nodes.push({ ref, tier: "parent", type: null, x: box.x, y: cursorY + NODE_H / 2, width: box.width, height: NODE_H })
        })
        parentTierBottomY = cursorY + NODE_H
        cursorY = parentTierBottomY + parentTierGap
    }

    const centreTopY = cursorY
    nodes.push({
        ref: ownRef, tier: "centre", type: ownType, x: centreX, y: centreTopY + centreH / 2,
        width: centreWidth, height: centreH, pill,
    })
    const centreBottomY = centreTopY + centreH
    cursorY = centreBottomY + childTierGap

    let childRow: { boxes: Map<string, { x: number; width: number }> } | null = null
    const childTierTopY = cursorY
    if (childRefs.length > 0) {
        const row = layoutWideRow(childRefs, centreX, childGapX)
        childRow = row
        childRefs.forEach((ref) => {
            const box = row.boxes.get(ref)!
            nodes.push({ ref, tier: "child", type: null, x: box.x, y: cursorY + NODE_H / 2, width: box.width, height: NODE_H })
        })
        cursorY = cursorY + NODE_H
    }

    const svgHeight = cursorY + WIDE_MARGIN

    const parentBandOrder = parentRow ? assignBandOrder(parentEdges, parentRow.boxes, centreX, "shallowest") : new Map()
    const childBandOrder = childRow ? assignBandOrder(childEdges, childRow.boxes, centreX, "deepest") : new Map()

    const edgesOut: LayoutEdge[] = []
    for (const edge of edges) {
        if (edge.tier === "parent" && parentRow) {
            const box = parentRow.boxes.get(edge.otherRef)!
            const bandIndex = parentBandOrder.get(edge)!
            const bandY = parentTierBottomY + WIDE_LABEL_SLOT * parentEdgeCount + WIDE_BAND_STEP * bandIndex
            const path = `M ${box.x} ${parentTierBottomY} L ${box.x} ${bandY} L ${centreX} ${bandY} L ${centreX} ${centreTopY}`
            const width = smallTextBoxWidth(edge.label)
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: edge.otherRef, toRef: ownRef, path,
                // Label anchored at the SIBLING end of the band (its own
                // box's x), not the midpoint toward centreX -- see the
                // module docstring's "Band ordering" section for why.
                labelX: box.x, labelY: bandY, labelWidth: width, labelHeight: LABEL_H,
            })
        } else if (edge.tier === "child" && childRow) {
            const box = childRow.boxes.get(edge.otherRef)!
            const bandIndex = childBandOrder.get(edge)!
            const bandY = centreBottomY + WIDE_BAND_STEP * bandIndex
            const path = `M ${centreX} ${centreBottomY} L ${centreX} ${bandY} L ${box.x} ${bandY} L ${box.x} ${childTierTopY}`
            const width = smallTextBoxWidth(edge.label)
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: ownRef, toRef: edge.otherRef, path,
                labelX: box.x, labelY: bandY, labelWidth: width, labelHeight: LABEL_H,
            })
        }
    }

    // A lone sibling under the centre leaves no room beside its drop; widen only as far as needed.
    if (!placeWideLabels(edgesOut, nodes, ownRef, svgWidth) && extraMargin < 400) {
        return computeWideLayout(model, extraMargin + 12)
    }
    return { width: svgWidth, height: svgHeight, nodes, edges: edgesOut }
}

// ---------------------------------------------------------------------------
// Narrow layout: one column, parents then centre then children. A spine
// runs down the left of the column; each satellite hangs off it by a short
// branch with its label on the branch, so the whole graph fits a phone.
// ---------------------------------------------------------------------------

const NARROW_MARGIN = 20
const NARROW_INDENT = 28
const NARROW_SPINE_X = 12
const NARROW_DROP = 14
const NARROW_GAP_Y = NARROW_DROP + 3 + LABEL_H + 6

export function computeNarrowLayout(model: DependencyGraphModel): GraphLayout {
    const { ownRef, ownType, centreSubjectLabel, parentRefs, childRefs, edges } = model
    const pill = centrePillFor(ownType)
    const centreW0 = centreNodeWidth(ownRef, centreSubjectLabel)
    const satW = Math.max(...[...parentRefs, ...childRefs].map(nodeWidth), centreW0 - NARROW_INDENT, 0)
    const colWidth = satW + NARROW_INDENT
    const maxLabelWidth = Math.max(0, ...edges.map((e) => smallTextBoxWidth(e.label)))
    const left = NARROW_MARGIN
    const spineX = left + NARROW_SPINE_X
    const svgWidth = Math.max(colWidth, NARROW_SPINE_X + LABEL_GAP + maxLabelWidth) + 2 * NARROW_MARGIN
    const centreH = centreContentHeight(Boolean(centreSubjectLabel))

    const nodes: LayoutNode[] = []
    const tops = new Map<string, number>()
    let y = NARROW_MARGIN
    const addSat = (ref: string, tier: "parent" | "child") => {
        nodes.push({ ref, tier, type: null, x: left + NARROW_INDENT + satW / 2, y: y + NODE_H / 2, width: satW, height: NODE_H })
        tops.set(ref, y)
        y += NODE_H + NARROW_GAP_Y
    }
    parentRefs.forEach((ref) => addSat(ref, "parent"))
    const centreTop = y
    nodes.push({
        ref: ownRef, tier: "centre", type: ownType, x: left + colWidth / 2, y: centreTop + centreH / 2,
        width: colWidth, height: centreH, pill,
    })
    y += centreH + NARROW_GAP_Y
    childRefs.forEach((ref) => addSat(ref, "child"))
    const svgHeight = (childRefs.length > 0 ? y - NARROW_GAP_Y : y - NARROW_GAP_Y) + NARROW_MARGIN
    const centreBottom = centreTop + centreH

    const edgesOut: LayoutEdge[] = []
    for (const edge of edges) {
        const top = tops.get(edge.otherRef)!
        const dropX = left + NARROW_INDENT + 20
        const width = smallTextBoxWidth(edge.label)
        const labelX = spineX + LABEL_GAP + width / 2
        if (edge.tier === "parent") {
            const branchY = top + NODE_H + NARROW_DROP
            const path = `M ${dropX} ${top + NODE_H} L ${dropX} ${branchY} L ${spineX} ${branchY} L ${spineX} ${centreTop}`
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: edge.otherRef, toRef: ownRef, path,
                labelX, labelY: branchY + 3 + LABEL_H / 2, labelWidth: width, labelHeight: LABEL_H, laneX: spineX, labelAnchor: "start",
            })
        } else {
            const branchY = top - NARROW_DROP
            const path = `M ${spineX} ${centreBottom} L ${spineX} ${branchY} L ${dropX} ${branchY} L ${dropX} ${top}`
            edgesOut.push({
                role: edge.role, label: edge.label, fromRef: ownRef, toRef: edge.otherRef, path,
                labelX, labelY: branchY - 3 - LABEL_H / 2, labelWidth: width, labelHeight: LABEL_H, laneX: spineX, labelAnchor: "start",
            })
        }
    }

    return { width: svgWidth, height: svgHeight, nodes, edges: edgesOut }
}
