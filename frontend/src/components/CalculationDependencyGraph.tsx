import { useEffect, useId, useRef, useState } from "react"
import { Link } from "react-router-dom"
import "../calculation-dependency-graph.css"
import type { CalculationDependency } from "../api/calculationApi"
import {
    buildDependencyGraphModel,
    centreContentHeight,
    computeNarrowLayout,
    computeWideLayout,
    dependencyGraphAriaLabel,
    NODE_RX,
    type GraphLayout,
    type LayoutNode,
} from "../domain/dependencyGraphLayout"
import { dependencyChildSentenceTemplate, dependencyParentSentenceTemplate, splitLinkTemplate } from "../domain/dependencyWording"

/**
 * The Related-calculations section's graph -- the owner's ask ("I think
 * this can be presented as a graph instead") for what used to be plain
 * sentences with links. This calculation is the centre node; every
 * PARENT edge (`dependency.direction === "child"`, this calc is the
 * child -- see `buildDependencyGraphModel`'s own docstring for why that
 * reads backwards) draws above it, every CHILD edge below, arrows always
 * pointing parent -> child (the data-flow direction), labelled with the
 * SAME plain-words role wording `domain/dependencyWording.ts` gives the
 * sentence list -- one shared table, so the vocabulary can't fork.
 *
 * Parent/child nodes never carry a `type` pill: `include=dependencies`
 * gives this page only `{ role, direction, parent_calculation_ref,
 * child_calculation_ref }` (`api/calculationApi.ts`'s `dependencySchema`)
 * -- no type for the OTHER calculation in the edge. Only the centre node's
 * own type is known (`calculation.type`, already on the record), so only
 * the centre node gets a type pill (a real `<rect rx>` behind the text,
 * matching `.value-pill`'s own COLOUR and RADIUS -- the stroke and font
 * both deliberately differ, see `calculation-dependency-graph.css`'s own
 * `.dep-graph-node-pill-bg` comment -- `domain/dependencyGraphLayout.ts`
 * sizes it); every other node shows its ref alone rather than guess.
 *
 * `centreLinked` (default `false`, preserving every existing caller's
 * behaviour) opts the CENTRE node into being a real link too --
 * `CalculationDetailPage.tsx` never passes it (that page IS the centre
 * calculation, so a link back to itself would be pointless) but
 * `ReactionTransitionStatesSection.tsx` does: on the reaction entry page
 * the centre node is the transition state's own `ts_opt`, a calculation
 * the reader is NOT already viewing, so leaving it unlinked there was the
 * owner's second complaint ("not clickable link to the calc"). When linked,
 * the pill+ref content wraps in the SAME `.dep-graph-node-link` a
 * parent/child node uses (hover/focus/visible-affordance CSS all apply
 * unchanged) and gets an accessible name of `${pill.label} ${node.ref}`
 * (e.g. "Optimisation calc_xxx") -- the two visible text runs (the pill's
 * own text, then the ref line) concatenated in their own on-screen reading
 * order, nothing inserted between them. Review finding (SC 2.5.3, Label in
 * Name): an earlier version inserted the word "calculation" between the
 * two -- `"Optimisation calculation calc_xxx"` -- which is a real word in
 * the accessible name that never appears on screen, breaking the visible
 * label into two non-contiguous pieces of the name. A parent/child node
 * has only ONE visible text run (its ref alone, no pill), so `${prefix}
 * ${ref}` was already safe there -- the prefix is never visible, and the
 * ref is a contiguous, unbroken tail of the name.
 *
 * Two full layouts (`computeWideLayout`/`computeNarrowLayout`,
 * `domain/dependencyGraphLayout.ts` -- see that module's own docstring
 * for the geometry). Which one is ACTIVE is decided from a REAL measured
 * container width (`ResizeObserver` on this component's own wrapper),
 * compared against `computeWideLayout(...).width` -- narrow renders as
 * soon as the container is narrower than the wide layout's own natural
 * width, never a fixed breakpoint. MEASURED (post-review): a fixed
 * `680px` media query let the wide layout scale ITSELF down (`width:
 * 100%`, no floor) on any container between 680px and its own natural
 * width -- at a 1100px container with 3 children the SVG scaled to
 * 0.913x, taking its smallest text (`--type-label-font`, 11.52px) below
 * this app's own accessible floor. Comparing against the real container
 * width switches to the narrow layout before any such shrink can happen.
 * `containerWidth` starts `null` (not yet measured, or `ResizeObserver`
 * unavailable -- jsdom, this app's own test-environment default) and the
 * component renders the WIDE layout in that case, same as this app's
 * `resolveIsDarkTheme` falls back to light when `matchMedia` is missing.
 *
 * The demoted `<ul>` below the SVG is the pre-existing sentence list, not
 * a duplicate: it is the text equivalent a screen reader (or Ctrl+F) uses
 * instead of the SVG, which carries `role="img"` and therefore hides its
 * own internals from assistive tech. Each node in the SVG is still a real
 * link for a sighted mouse/keyboard user (`aria-label` deliberately
 * differs from the bare ref text the list link uses, so the two never
 * collide as the same accessible name within this section).
 *
 * `centreSubject` (default `null`) answers the OTHER question a linked
 * centre node still didn't answer -- owner report on the reaction-entry
 * page's graph: "what the fuck is it? a ts? a random ass molecule?...
 * there is no real indication except before it some tiny ass text that no
 * one is going to read". The type pill says WHAT KIND ("Optimisation");
 * the ref line says WHICH ONE (`calc_xxx`); neither says WHOSE --
 * `ReactionTransitionStatesSection.tsx` is the one caller that has an
 * answer (the TS entry this `ts_opt` belongs to) and passes it as a THIRD
 * row inside the node itself, a real `<Link>` to the TS entry (so the
 * `tse_...` ref stays reachable from the node area, not only from prose
 * above the graph) with the visible text kept short (e.g. "Transition
 * state") and the ref folded into the link's own accessible name instead
 * (`${label} ${ref}`) rather than printed twice. `CalculationDetailPage
 * .tsx` never passes this -- it has no such "whose" to report about
 * itself -- so its own centre node stays exactly two rows, unchanged.
 * `domain/dependencyGraphLayout.ts`'s `centreContentHeight`/
 * `centreNodeWidth` grow the box for the subject row at LAYOUT time (fed
 * `centreSubject?.label` into `buildDependencyGraphModel`); this
 * component re-derives the SAME split (`centreContentHeight`, imported
 * rather than re-computed) to place the subject row between the pill and
 * the ref line without a second, drifting copy of that arithmetic.
 *
 * Paint order inside the `<svg>` is ALL edge paths, then ALL edge labels,
 * then ALL nodes -- never one edge's path/label/rect interleaved with
 * the next's. MEASURED (post-review): with each edge rendered as its own
 * self-contained group (path, then its label, in DOM/array order), a
 * LATER edge's path painted on top of an visually EARLIER edge's label
 * wherever the two geometrically crossed (the `<path>` has no fill, but
 * is still opaque along its stroked line) -- e.g. a 3-child graph's
 * middle child's own connecting line crossing dead through another
 * child's label text. `domain/dependencyGraphLayout.ts`'s band/lane
 * placement keeps every DIFFERENT edge's path geometrically clear of
 * every OTHER edge's label rect (see that module's own tests); this
 * paint order is what ALSO keeps an edge's path from ever visually
 * covering its OWN label where the two deliberately meet (the label's
 * opaque background "punches a hole" in its own line at the bend).
 */
/** See `CalculationDependencyGraph`'s own docstring for what this answers
 * and why it lives inside the node rather than a caption above it. */
export interface CentreSubject {
    /** Short, visible answer to "an optimisation (or whatever `ownType`
     * is) OF WHAT?" -- e.g. "Transition state". Deliberately NOT the raw
     * ref (that stays reachable via the link's own accessible name and
     * `href`, not printed a second time next to the calc ref line). */
    label: string
    /** The subject's own ref (e.g. a `tse_...` transition-state-entry
     * ref) -- folded into the link's accessible name (`${label} ${ref}`)
     * so the exact record stays identifiable even though the visible
     * text is the short label alone. */
    ref: string
    href: string
}

export function CalculationDependencyGraph({ dependencies, ownRef, ownType, centreLinked = false, centreSubject = null }: {
    dependencies: CalculationDependency[]
    ownRef: string
    ownType: string
    /** Opt-in: see this component's own docstring. Default `false` keeps
     * `CalculationDetailPage.tsx`'s existing centre-is-unlinked behaviour. */
    centreLinked?: boolean
    /** Opt-in: see this component's own docstring. Default `null` keeps
     * `CalculationDetailPage.tsx`'s centre node at its existing two rows
     * (pill + ref), since that page has no subject of its own to report. */
    centreSubject?: CentreSubject | null
}) {
    const markerId = useId()
    const containerRef = useRef<HTMLDivElement>(null)
    const [containerWidth, setContainerWidth] = useState<number | null>(null)

    useEffect(() => {
        const el = containerRef.current
        if (!el || typeof ResizeObserver === "undefined") return
        const observer = new ResizeObserver((entries) => {
            const entry = entries[0]
            if (entry) setContainerWidth(entry.contentRect.width)
        })
        observer.observe(el)
        return () => observer.disconnect()
    }, [])

    if (dependencies.length === 0) return null

    const model = buildDependencyGraphModel(ownRef, ownType, dependencies, centreSubject?.label)
    const wideLayout = computeWideLayout(model)
    const isNarrow = containerWidth !== null && containerWidth < wideLayout.width
    const layout: GraphLayout = isNarrow ? computeNarrowLayout(model) : wideLayout
    const ariaLabel = dependencyGraphAriaLabel(model.parentRefs.length, model.childRefs.length)
    const arrowMarkerId = `dep-graph-arrow-${markerId}`

    return (
        <div className="dep-graph" ref={containerRef}>
            <svg
                className="dep-graph-svg"
                viewBox={`0 0 ${layout.width} ${layout.height}`}
                role="img"
                aria-label={`Dependency graph for ${ownRef}: ${ariaLabel}`}
                // `width: 100%` (calculation-dependency-graph.css) lets the
                // graph fill its container UP TO this cap -- but `minWidth`
                // pins the SAME value as a FLOOR too, so the SVG renders at
                // EXACTLY `layout.width` CSS px regardless of the container
                // (never scaled up OR down). One viewBox unit is intended
                // to be one CSS px (the box-sizing estimate in
                // `domain/dependencyGraphLayout.ts` is written in px), so
                // this is always the layout math's own computed width.
                // MEASURED (post-review): with only a `maxWidth` cap, the
                // ACTIVE layout (even after the `isNarrow` switch already
                // picked the narrower one) still shrank below its own
                // natural size on a container narrower than THAT layout's
                // own width -- a 600px viewport rendered the narrow layout
                // at 0.89x (labels 10.26px), 400px at 0.57x (6.6px), both
                // under the accessible floor. `.dep-graph`'s own
                // `overflow-x: auto` (calculation-dependency-graph.css) is
                // what a container narrower than this fixed width does
                // instead: the SECTION scrolls horizontally, the graph's
                // own text never shrinks.
                style={{ maxWidth: `${layout.width}px`, minWidth: `${layout.width}px` }}
            >
                <defs>
                    <marker
                        id={arrowMarkerId}
                        viewBox="0 0 10 10"
                        refX="8"
                        refY="5"
                        markerWidth="7"
                        markerHeight="7"
                        orient="auto"
                    >
                        <path d="M0,0 L10,5 L0,10 z" className="dep-graph-arrowhead" />
                    </marker>
                </defs>
                {layout.edges.map((edge) => (
                    <path
                        key={`path-${edge.role}-${edge.fromRef}-${edge.toRef}`}
                        data-testid={`dep-edge-path-${edge.fromRef}-${edge.toRef}-${edge.role}`}
                        d={edge.path}
                        className="dep-graph-edge-path"
                        markerEnd={`url(#${arrowMarkerId})`}
                        data-from={edge.fromRef}
                        data-to={edge.toRef}
                    />
                ))}
                {layout.edges.map((edge) => (
                    <g key={`label-${edge.role}-${edge.fromRef}-${edge.toRef}`} data-testid={`dep-edge-label-${edge.fromRef}-${edge.toRef}-${edge.role}`}>
                        <rect
                            x={edge.labelX - edge.labelWidth / 2}
                            y={edge.labelY - edge.labelHeight / 2}
                            width={edge.labelWidth}
                            height={edge.labelHeight}
                            className="dep-graph-edge-label-bg"
                        />
                        <text x={edge.labelX} y={edge.labelY} className="dep-graph-edge-label-text" textAnchor="middle" dominantBaseline="middle">
                            {edge.label}
                        </text>
                    </g>
                ))}
                {layout.nodes.map((node) => (
                    <DependencyGraphNode key={`${node.tier}-${node.ref}`} node={node} centreLinked={centreLinked} centreSubject={centreSubject} />
                ))}
            </svg>
            <DependencySentenceList dependencies={dependencies} />
        </div>
    )
}

const TIER_ARIA_PREFIX: Record<LayoutNode["tier"], string> = {
    parent: "Parent calculation",
    centre: "This calculation",
    child: "Child calculation",
}

function DependencyGraphNode({ node, centreLinked, centreSubject }: {
    node: LayoutNode
    centreLinked: boolean
    centreSubject?: CentreSubject | null
}) {
    const left = node.x - node.width / 2
    const top = node.y - node.height / 2
    const rect = <rect x={left} y={top} width={node.width} height={node.height} rx={NODE_RX} className="dep-graph-node-rect" />

    if (node.tier === "centre" && node.pill) {
        const { pill } = node
        const hasSubject = Boolean(centreSubject)
        // The pill(+subject)+ref content block is `centreContentHeight
        // (hasSubject)` tall regardless of the node's OWN height -- the
        // narrow layout grows a centre node past that to fit staggered
        // lane entries (`centreHeightFor`), but the content itself never
        // grows to match. MEASURED (post-review, pre-subject-row): pinning
        // the block to the box's TOP (`top + 10`) left a 64.6px gap below
        // the ref line and only 10px above it on a 3-child, 680px narrow
        // graph. `contentTop` re-centres the whole block in the node's
        // actual height, so the gap splits evenly top and bottom instead.
        const contentTop = top + (node.height - centreContentHeight(hasSubject)) / 2
        // Matches `domain/dependencyGraphLayout.ts`'s own `CENTRE_PAD_TOP`
        // (10) / `CENTRE_PILL_GAP` (8) / `CENTRE_REF_LINE_H` (24) /
        // `CENTRE_SUBJECT_GAP_TOP` (6) / `CENTRE_SUBJECT_LINE_H` (16) /
        // `CENTRE_SUBJECT_GAP_BOTTOM` (6) -- duplicated here as plain
        // numbers rather than exported constants (besides
        // `centreContentHeight` itself, already imported) since this is
        // the only place outside that module that needs the internal
        // split, not a value worth widening that module's public surface
        // for.
        const pillY = contentTop + 10
        const pillBottom = pillY + pill.height
        // Row 2 (subject, only when `hasSubject`) sits between the pill
        // and the ref line; row 3 (ref) starts right after whichever gap
        // applies -- the plain `CENTRE_PILL_GAP` (8) with no subject, or
        // the subject row plus its own two gaps (6 + 16 + 6 = 28)
        // otherwise. Both branches land on the SAME ref-row top when
        // `hasSubject` is false as the pre-subject-row formula did
        // (`pillBottom + 8`), so `CalculationDetailPage.tsx`'s own centre
        // node (never passes `centreSubject`) renders byte-identical
        // geometry to before this row existed.
        const subjectTop = pillBottom + 6
        const subjectTextY = subjectTop + 16 / 2
        const refRowTop = hasSubject ? subjectTop + 16 + 6 : pillBottom + 8
        const refY = refRowTop + 12
        const content = (
            <>
                {rect}
                <rect
                    x={node.x - pill.width / 2}
                    y={pillY}
                    width={pill.width}
                    height={pill.height}
                    rx={999}
                    className="dep-graph-node-pill-bg"
                />
                <text x={node.x} y={pillY + pill.height / 2} className="dep-graph-node-pill-text" textAnchor="middle" dominantBaseline="middle">
                    {pill.label}
                </text>
                <text x={node.x} y={refY} className="dep-graph-node-ref" textAnchor="middle" dominantBaseline="middle">
                    {node.ref}
                </text>
            </>
        )
        return (
            <g className="dep-graph-node dep-graph-node--centre" data-testid={`dep-node-centre-${node.ref}`}>
                {centreLinked
                    ? (
                        <Link
                            to={`/calculations/${node.ref}`}
                            aria-label={`${pill.label} ${node.ref}`}
                            className="dep-graph-node-link"
                        >
                            {content}
                        </Link>
                    )
                    : content}
                {/* A SEPARATE link from the calc link above -- an SVG `<a>`
                    cannot nest inside another, and this answers a
                    different question (WHOSE, not WHICH) with a
                    different destination. Sits strictly within its own
                    row (`subjectTop`..`subjectTop + 16`), never
                    overlapping the pill or ref rows above/below it, so
                    the two links' hit areas stay disjoint even though
                    both live inside the same node box. A transparent
                    rect gives the link a real click target sized to the
                    row (an SVG `<text>` alone hit-tests only its own
                    glyph shapes, per `CalculationDependencyGraph.tsx`'s
                    own `.dep-graph-node-subject-link` CSS comment). */}
                {centreSubject && (
                    <Link
                        to={centreSubject.href}
                        aria-label={`${centreSubject.label} ${centreSubject.ref}`}
                        className="dep-graph-node-subject-link"
                        data-testid={`dep-node-subject-${node.ref}`}
                    >
                        <rect
                            x={left + 8}
                            y={subjectTop}
                            width={node.width - 16}
                            height={16}
                            fill="transparent"
                        />
                        <text x={node.x} y={subjectTextY} className="dep-graph-node-subject-text" textAnchor="middle" dominantBaseline="middle">
                            {centreSubject.label}
                        </text>
                    </Link>
                )}
            </g>
        )
    }

    return (
        <g className={`dep-graph-node dep-graph-node--${node.tier}`} data-testid={`dep-node-${node.tier}-${node.ref}`}>
            <Link
                to={`/calculations/${node.ref}`}
                aria-label={`${TIER_ARIA_PREFIX[node.tier]} ${node.ref}`}
                className="dep-graph-node-link"
            >
                {rect}
                <text x={node.x} y={node.y} className="dep-graph-node-ref" textAnchor="middle" dominantBaseline="middle">
                    {node.ref}
                </text>
            </Link>
        </g>
    )
}

/** The text equivalent for the SVG above -- byte-identical wording to the
 * one this section rendered before the graph existed (`dependencySentence`
 * in `CalculationDetailPage.tsx`, now sourced from the same
 * `domain/dependencyWording.ts` table the graph's edge labels use), just
 * demoted to `.dep-graph-sentences` (`.note`-sized: `--type-note-font` /
 * `--muted`) rather than the section's primary content. */
function DependencySentenceList({ dependencies }: { dependencies: CalculationDependency[] }) {
    return (
        <ul className="dep-graph-sentences" aria-label="Dependency edges, as text">
            {dependencies.map((dep, index) => {
                const isChildSide = dep.direction === "child"
                const linkRef = isChildSide ? dep.parent_calculation_ref : dep.child_calculation_ref
                const template = isChildSide ? dependencyChildSentenceTemplate(dep.role) : dependencyParentSentenceTemplate(dep.role)
                const { before, after } = splitLinkTemplate(template)
                return (
                    <li key={`${dep.role}-${dep.direction}-${linkRef}-${index}`}>
                        {before}
                        <Link to={`/calculations/${linkRef}`}>{linkRef}</Link>
                        {after}
                    </li>
                )
            })}
        </ul>
    )
}
