import type { ReactNode } from "react"

/**
 * Defers a `children(record, data)` render-prop call into a real React
 * component's own render, rather than evaluating it inline as a plain JS
 * function call. This is NOT cosmetic: `<SectionErrorBoundary>{children(record,
 * data)}</SectionErrorBoundary>` calls `children(...)` synchronously while
 * the CALLER is still constructing its own JSX tree — a throw there
 * propagates out of the caller's own render function before
 * `SectionErrorBoundary` is ever mounted, so it is not caught by a boundary
 * that only wraps the *result* of the call.
 *
 * Confirmed directly (not just reasoned about) via instrumented probes
 * before this was accepted:
 *
 * ```
 * PROBE A (inline call in parent's render):  innerFallback=false  outerFallback=TRUE  sibling=false
 * PROBE B (deferred via LazyRowBody):        innerFallback=TRUE   outerFallback=false sibling=true
 * ```
 *
 * `<Boundary>{children(record, data)}</Boundary>` compiles to
 * `jsx(Boundary, {children: children(record, data)})` — the call runs while
 * the parent builds its element tree, so the throw escapes before the inner
 * boundary fiber exists and is instead caught by whatever boundary wraps
 * the PARENT (the `outerFallback=TRUE` in probe A). `LazyRowBody` is a
 * genuine descendant component, so `render`'s call happens during React's
 * render phase, inside the boundary's own subtree, where the boundary can
 * actually see it throw and the SIBLING row is left standing (probe B).
 *
 * Shared by `EntryStatmechSection.tsx` and `EntryTransportSection.tsx` —
 * both entry-scoped LIST surfaces with per-token lazy sections whose rows
 * need this same per-row isolation. See
 * `EntryStatmechSection.errorBoundary.test.tsx` / the transport equivalent
 * prove that the real sections isolate a failing row from its siblings, the
 * record cards and the review summary. Read precisely: they prove the real
 * sections use *a boundary*, NOT that they route through this component.
 * Reverting a call site to `{children(record, data)}` still passes them,
 * and that is correct rather than a coverage hole — those two render-props
 * return an ELEMENT, which React invokes at its own tree position under the
 * boundary either way, so inline and deferred are genuinely equivalent there.
 *
 * This component earns its keep on the render-props that build native JSX in
 * their own body — statmech electronic_levels / frequencies / conformers /
 * review_history, and transport review_history. For those, inline throws
 * escape to the OUTER boundary and take the parent's siblings with them
 * (probe 2a vs 2b). Do not inline those call sites.
 *
 * That path is not pinned by a test. It is reachable only from a payload no
 * zod-validated archive response can currently produce. The way to pin it,
 * if it ever becomes reachable: export the lazy-section component and inject
 * a throwing render-prop into it — do NOT extract the body into a component,
 * because any extraction sufficient to mock makes it React-deferred and
 * neutralises the property under test.
 */
export function LazyRowBody<TRecord, TField>({ record, data, render }: {
    record: TRecord
    data: TField
    render: (record: TRecord, data: TField) => ReactNode
}) {
    return <>{render(record, data)}</>
}
