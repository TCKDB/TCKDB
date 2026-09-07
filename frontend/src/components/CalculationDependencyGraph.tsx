import { useEffect, useId, useRef, useState } from "react"
import { Link } from "react-router-dom"
import "../calculation-dependency-graph.css"
import type { CalculationDependency } from "../api/calculationApi"
import {
    buildDependencyGraphModel,
    computeNarrowLayout,
    computeWideLayout,
    dependencyGraphAriaLabel,
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
 * `.value-pill`'s own treatment -- `domain/dependencyGraphLayout.ts`
 * sizes it); every other node shows its ref alone rather than guess.
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
export function CalculationDependencyGraph({ dependencies, ownRef, ownType }: {
    dependencies: CalculationDependency[]
    ownRef: string
    ownType: string
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

    const model = buildDependencyGraphModel(ownRef, ownType, dependencies)
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
                // graph SHRINK to fit a narrow container; without a cap it
                // would also GROW to fill a wide one, inflating every box
                // and font past the size the layout math actually chose.
                // One viewBox unit is intended to be one CSS px (the
                // box-sizing estimate in `domain/dependencyGraphLayout.ts`
                // is written in px), so the inline cap is the layout's own
                // computed width -- this graph never scales past 1:1, only
                // down (and even that only when narrower than its own
                // computed width AND still wider than the narrow layout's
                // own width, since the `isNarrow` switch above already
                // fires before the wide layout would otherwise need to).
                style={{ maxWidth: `${layout.width}px` }}
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
                {layout.nodes.map((node) => <DependencyGraphNode key={`${node.tier}-${node.ref}`} node={node} />)}
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

function DependencyGraphNode({ node }: { node: LayoutNode }) {
    const left = node.x - node.width / 2
    const top = node.y - node.height / 2
    const rect = <rect x={left} y={top} width={node.width} height={node.height} rx={8} className="dep-graph-node-rect" />

    if (node.tier === "centre" && node.pill) {
        const { pill } = node
        // Matches `domain/dependencyGraphLayout.ts`'s own `CENTRE_PAD_TOP`
        // (10) / `CENTRE_PILL_GAP` (8) / `CENTRE_REF_LINE_H` (24) --
        // duplicated here as plain numbers rather than exported constants
        // since this is the only place outside that module that needs the
        // internal split of `CENTRE_H`, not a value worth widening that
        // module's public surface for.
        const pillY = top + 10
        const refY = pillY + pill.height + 8 + 12
        return (
            <g className="dep-graph-node dep-graph-node--centre" data-testid={`dep-node-centre-${node.ref}`}>
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
