import { useEffect, useId, useState } from "react"
import { Link } from "react-router-dom"
import "../calculation-dependency-graph.css"
import type { CalculationDependency } from "../api/calculationApi"
import { typeLabel } from "../domain/calculationTypeFormat"
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
 * the centre node gets a type pill; every other node shows its ref alone
 * rather than guess.
 *
 * Two full layouts (`computeWideLayout`/`computeNarrowLayout`,
 * `domain/dependencyGraphLayout.ts`) rather than one CSS-scaled SVG: a
 * horizontal row of several siblings shrunk to fit a 680px viewport would
 * cross the accessible text-size floor, so the narrow layout genuinely
 * re-lays the graph out as one vertical column instead of just shrinking
 * the wide one. Which layout is ACTIVE is real `matchMedia` state (the
 * same `resolveIsDarkTheme`/`isDarkTheme` pattern `GeometryViewer.tsx`
 * already uses for `prefers-color-scheme`), not two SVGs toggled by a CSS
 * media query -- keeping only one `role="img"` element in the DOM at a
 * time avoids ever having two elements answer `getByRole("img")`, and
 * matches this app's existing "no `window.matchMedia`" test-environment
 * default (jsdom does not implement it) by falling back to the wide
 * layout, same as `resolveIsDarkTheme` falls back to light.
 *
 * The demoted `<ul>` below the SVG is the pre-existing sentence list, not
 * a duplicate: it is the text equivalent a screen reader (or Ctrl+F) uses
 * instead of the SVG, which carries `role="img"` and therefore hides its
 * own internals from assistive tech. Each node in the SVG is still a real
 * link for a sighted mouse/keyboard user (`aria-label` deliberately
 * differs from the bare ref text the list link uses, so the two never
 * collide as the same accessible name within this section).
 */
export function CalculationDependencyGraph({ dependencies, ownRef, ownType }: {
    dependencies: CalculationDependency[]
    ownRef: string
    ownType: string
}) {
    const markerId = useId()
    const [isNarrow, setIsNarrow] = useState(() => resolveIsNarrow())

    useEffect(() => {
        const mediaQuery = typeof window !== "undefined" && typeof window.matchMedia === "function"
            ? window.matchMedia(NARROW_MEDIA_QUERY)
            : null
        if (!mediaQuery) return
        const sync = () => setIsNarrow(mediaQuery.matches)
        sync()
        mediaQuery.addEventListener?.("change", sync)
        return () => mediaQuery.removeEventListener?.("change", sync)
    }, [])

    if (dependencies.length === 0) return null

    const model = buildDependencyGraphModel(ownRef, ownType, dependencies)
    const layout: GraphLayout = isNarrow ? computeNarrowLayout(model) : computeWideLayout(model)
    const ariaLabel = dependencyGraphAriaLabel(model.parentRefs.length, model.childRefs.length)
    const arrowMarkerId = `dep-graph-arrow-${markerId}`

    return (
        <div className="dep-graph">
            <svg
                className="dep-graph-svg"
                viewBox={`0 0 ${layout.width} ${layout.height}`}
                role="img"
                aria-label={`Dependency graph for ${ownRef}: ${ariaLabel}`}
                // `width: 100%` (calculation-dependency-graph.css) lets the
                // graph SHRINK to fit a narrow container; without a cap it
                // would also GROW to fill a wide one, inflating every box
                // and font past the size the layout math actually chose --
                // MEASURED (screenshot review): a one-parent graph on this
                // page's ~1085px-wide content column rendered at 3x its
                // intended size, letters taller than the node boxes. One
                // viewBox unit is intended to be one CSS px (the box-sizing
                // estimate in `domain/dependencyGraphLayout.ts` is written
                // in px), so the inline cap is the layout's own computed
                // width -- this graph never scales past 1:1, only down.
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
                    <g key={`${edge.role}-${edge.fromRef}-${edge.toRef}`} data-testid={`dep-edge-${edge.fromRef}-${edge.toRef}-${edge.role}`}>
                        <path d={edge.path} className="dep-graph-edge-path" markerEnd={`url(#${arrowMarkerId})`} data-from={edge.fromRef} data-to={edge.toRef} />
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

const NARROW_MEDIA_QUERY = "(max-width: 680px)"

function resolveIsNarrow(): boolean {
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
        return window.matchMedia(NARROW_MEDIA_QUERY).matches
    }
    return false
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

    if (node.tier === "centre") {
        const pillLabel = typeLabel(node.type ?? "")
        return (
            <g className="dep-graph-node dep-graph-node--centre" data-testid={`dep-node-centre-${node.ref}`}>
                {rect}
                <text x={node.x} y={top + 20} className="dep-graph-node-pill-text" textAnchor="middle" dominantBaseline="middle">
                    {pillLabel}
                </text>
                <text x={node.x} y={top + 40} className="dep-graph-node-ref" textAnchor="middle" dominantBaseline="middle">
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
