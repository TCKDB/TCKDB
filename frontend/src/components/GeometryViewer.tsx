import { useEffect, useId, useMemo, useRef, useState } from "react"
import type { GeometryAtom } from "../api/geometryApi"
import { buildXyzBlock } from "../domain/geometryXyz"

/**
 * A real WebGL 3D viewer, built on 3Dmol.js — replaces a hand-rolled SVG
 * orthographic projection that existed only because of an invented
 * constraint ("no npm dependency") with no basis in this repo's actual
 * rules. 3Dmol is bundled from npm and imported dynamically inside this
 * component's own effect (not statically at module scope), so it never
 * enters the shared app bundle: it loads only when a reader lands on a
 * geometry page that actually mounts this component, as its own chunk.
 *
 * WebGL is a hard requirement 3Dmol did not have to satisfy before (the
 * SVG projection needed none), so this component treats a missing/broken
 * WebGL context as an ordinary, expected outcome rather than a crash:
 *
 * - `$3Dmol.createViewer()` calls into `new GLViewer(...)`, whose
 *   constructor calls `Renderer#initGL()` (which itself only
 *   `console.error`s and swallows a failed `canvas.getContext('webgl'/
 *   'webgl2'/'experimental-webgl')`) followed immediately by
 *   `setDefaultGLState()`, which dereferences the now-undefined GL
 *   context and throws. `createViewer` re-throws that as a plain string.
 *   That throw happens inside this component's own `useEffect` — not
 *   during render — so `SectionErrorBoundary` (which only catches
 *   descendant *render* errors, per React's contract) would not reliably
 *   catch it even if left unguarded. This component does not rely on the
 *   boundary for that case: the dynamic import and the `createViewer`/
 *   `addModel` calls are wrapped in their own try/catch below, and a
 *   failure flips local state to an honest "could not be initialised"
 *   message instead of leaving the section stuck or throwing further.
 *   Verified against real WebGL failure (a mocked `createViewer` that
 *   throws — see `GeometryViewer.test.tsx`), not only against the
 *   zero-size path below, which is a different failure mode entirely.
 *   (`SectionErrorBoundary` in `GeometryDetailPage` still exists as a
 *   second, independent line of defence against an actual render throw
 *   in this component's own JSX — the two failure modes are handled by
 *   two different mechanisms on purpose, and `GeometryDetailPage` wires
 *   it around `<GeometryViewer …/>` as a JSX *element*, never a called
 *   function — a boundary cannot catch a throw from a function called
 *   while building the parent's own JSX, only a throw from a descendant
 *   component's own render.)
 *
 * - A container measured at zero size is NOT the same failure as no
 *   WebGL. 3Dmol's `GLViewer` constructor runs to completion either way
 *   (a zero-size `Renderer` just "starts lost" — see
 *   `node_modules/3dmol/src/WebGL/Renderer.ts`, `parameters.containerWidth
 *   == 0 || parameters.containerHeight == 0` — silently, no throw) and
 *   registers its own `ResizeObserver`/`IntersectionObserver` on the
 *   container (`GLViewer.ts:731-748`), so a viewer that starts at zero
 *   size and later gets a real size self-heals via `resize()`
 *   (`Renderer.ts:1453-1479`) to a result indistinguishable from a
 *   viewer that had a size from the start (measured: identical rendered
 *   output). This component does not lean on that internal self-healing
 *   directly — it is 3Dmol-version-internal behaviour, not a public
 *   contract — but mirrors the same idea explicitly and observably: if
 *   the container measures zero on mount, this effect does not call
 *   `createViewer` at all yet, and instead watches the container with
 *   its own `ResizeObserver` and defers initialisation until the
 *   container actually has a size. Only when no `ResizeObserver` exists
 *   at all (so a later resize could never be noticed) does this report
 *   "unavailable" for a zero-size container, rather than reporting a
 *   false "ready" the instant `createViewer` merely fails to throw, or
 *   hanging on "loading" forever with no way to ever detect success.
 *
 * This component never calls `viewer.spin()` or `viewer.animate()`.
 * `zoomTo()` on load, `zoom()` from the zoom buttons, and
 * `rotate()`/`setView()` from the other buttons are all called with no
 * animation duration (0, the default for each) — so there is nothing
 * here for `prefers-reduced-motion` to need to turn off; every view
 * change is an instant snap by construction, not a media-query branch
 * that could be missed.
 *
 * ## Mouse/touch: re-enabled, with the scroll trap fixed at the source
 *
 * An earlier revision passed `nomouse: true` to `createViewer`. That was
 * the right diagnosis (3Dmol's default handling was trapping page
 * scroll — see below) with too broad a remedy: it disabled *all* of
 * 3Dmol's own input handling, including drag-to-rotate, which was never
 * the problem.
 *
 * The actual, narrower problem: 3Dmol installs non-passive `wheel` and
 * `touchstart`/`touchmove` listeners directly on its own canvas
 * (`GLViewer.ts:291-298`), and `_handleMouseScroll` (the `wheel`
 * handler) calls `ev.preventDefault()` unconditionally, with no check
 * for a modifier key or any other "this gesture means zoom, not scroll"
 * signal (`GLViewer.ts:1054-1055`). On a viewer that is close to full
 * width on a phone, a reader's swipe-to-scroll starts a touch sequence
 * on the canvas and 3Dmol treats it as drag-to-rotate instead of
 * forwarding it to the page; on desktop, resting the cursor over the
 * canvas while scrolling with a wheel zooms the model instead of
 * scrolling the page. Both are regressions the previous SVG projection
 * never had, since it was never large enough to sit under a reader's
 * thumb, and never captured input at all.
 *
 * The fix here re-enables 3Dmol's own mouse/touch handling in full
 * (`nomouse` is no longer passed at all — 3Dmol's own default is
 * `false`), so mouse drag and multi-finger touch gestures reach 3Dmol
 * and work exactly as 3Dmol implements them — but "3Dmol implements
 * them" is not the same claim as "every gesture rotates the model".
 * Measured live via `getView()` before/after each gesture: two-finger
 * touch is zoom-only (the rotation quaternion is unchanged, only camera
 * distance moves) and three-finger touch translates the model; neither
 * rotates it. One-finger touch is the gesture that *would* rotate, and
 * it is exactly the gesture this component intercepts for page scroll
 * (below) — so on a touchscreen, no gesture rotates the model at all.
 * That is the right trade (a reader who cannot scroll past the viewer
 * has lost more than one who cannot rotate it with a finger), but it
 * means the Rotate buttons below are not merely a keyboard-accessible
 * addition on touch — they are the *only* way to rotate there. On
 * desktop, mouse drag is untouched by any of this and rotates normally.
 * On top of that, this component adds
 * its own **capture-phase** `wheel`/`touchmove` listeners on the
 * *container* — an ancestor of the `<canvas>` 3Dmol appends into it —
 * that call `event.stopPropagation()` (never `preventDefault()`) for:
 *
 *   - every `wheel` event (zoom-by-scroll is not offered at all; the
 *     "Zoom in"/"Zoom out" buttons below are the explicit affordance
 *     instead, so there is no gesture to disambiguate and no modifier
 *     key to remember);
 *   - a `touchmove` event with exactly one active touch point (a plain
 *     one-finger swipe — the page-scroll gesture). A `touchmove` with
 *     two or more touch points (pinch, two-finger drag) is deliberately
 *     left alone and reaches 3Dmol normally;
 *   - a `touchstart` event under the same one-finger/multi-finger rule.
 *     This one is easy to think unnecessary (the reviewer's original
 *     proposal named only wheel/touchmove) — it is not. Measured live:
 *     3Dmol's `touchstart` handler (`_handleMouseDown`) also calls
 *     `preventDefault()` unconditionally, and Chromium suppresses
 *     scrolling for the *entire* touch sequence once its `touchstart`
 *     has been prevented, even when every subsequent `touchmove` in that
 *     same sequence is left completely alone. Intercepting only
 *     `touchmove` reproduces the swipe-traps-the-page regression in
 *     full; the fix needs both.
 *
 * Calling `stopPropagation()` on an *ancestor* during the *capture*
 * phase halts the event's propagation before it ever reaches the
 * descendant `<canvas>` — per the DOM event dispatch algorithm, the
 * event is never delivered to the target's own listeners once
 * propagation has been stopped upstream of it, whether those listeners
 * are registered for the capture, target, or bubble phase. Concretely:
 * 3Dmol's `_handleMouseDown`/`_handleMouseScroll`/`_handleMouseMove`
 * never run for these gestures, so they never call `preventDefault()`,
 * so the browser's native scroll proceeds exactly as if 3Dmol's canvas
 * listeners were never installed at all. This component's own listeners
 * never call `preventDefault()` themselves — only `stopPropagation()` —
 * which is why they are registered `{ passive: true }`.
 *
 * Verified in a real Chromium browser via Playwright (jsdom cannot
 * dispatch trusted input events, perform layout, or scroll, so it cannot
 * show any of this either way — that gap is exactly how the original
 * scroll trap shipped unnoticed). Measured against the live rendered
 * page (real 3Dmol, real WebGL via swiftshader), listening on 3Dmol's
 * own `<canvas>` directly (the exact node its handlers are registered
 * on) as well as on `window`:
 *
 *   - Plain wheel over the canvas: page scrolled (`window.scrollY`
 *     400→400 with the fix reverted to "intercept nothing" vs.
 *     1507→1907 with the fix in place, for an identical 400-unit wheel
 *     input each time); 3Dmol's own canvas listener never ran at all
 *     with the fix in place (0 events observed there — with the fix
 *     reverted, that same listener ran and read `defaultPrevented:
 *     true`, confirming the interception is what changes the outcome).
 *   - Plain one-finger touch swipe over the canvas: `window.scrollY`
 *     1507→1998 with the fix in place vs. 1507→1507 (no movement at
 *     all) with the fix reverted; again, 0 events reached 3Dmol's own
 *     canvas listener with the fix in place, vs. 8/8 reaching it (each
 *     `defaultPrevented: true`) with the fix reverted.
 *   - Two-finger touchmove/touchstart (pinch/two-finger drag): reaches
 *     3Dmol's own listener either way, as intended (`defaultPrevented:
 *     true` there is 3Dmol doing its own thing with the gesture, not a
 *     bug) — confirming multi-touch was never collaterally disabled.
 *   - A mouse drag over the canvas still rotates the model (visually
 *     confirmed via before/after screenshot).
 *
 * The canvas 3Dmol renders into is `aria-hidden` — a supplementary
 * picture, not the accessible representation of this geometry. The
 * coordinate table and raw XYZ block rendered alongside it in
 * `GeometryDetailPage` remain the actual accessible/copyable fallback,
 * unchanged by this component, and are what a reader gets in full even
 * when this component's viewer never initialises at all (no WebGL) or
 * throws outright (an error state this component cannot anticipate).
 *
 * Bonds 3Dmol draws are inferred client-side from interatomic distance
 * for legibility only, exactly as the previous SVG projection's bonds
 * were — the payload carries no bond list, and that is disclosed in the
 * caption rather than presented as deposited data. The spacefill style
 * (see `STYLE_SPECS` below) does not draw an explicit bond primitive at
 * all, so the caption swaps to a style-accurate sentence instead of
 * asserting a bond disclosure that would not be true for it.
 *
 * ## Labels follow the coordinate table, not 3Dmol's own atom numbering
 *
 * 3Dmol assigns its own `serial` to each parsed atom, which is not
 * guaranteed to agree with this archive's `atom_index` (the coordinate
 * table's own 1-based numbering — see `api/geometryApi.ts`). Rather than
 * read atom identity back out of 3Dmol's model (via `selectedAtoms()`
 * and its `serial`), the label effect below iterates this component's
 * own `atoms` prop directly — the exact same rows the coordinate table
 * renders — and places each label at that row's own `x`/`y`/`z`, text
 * built from that row's own `element`/`atom_index`. A viewer that
 * numbers atoms differently from the table beside it would be worse
 * than no numbering at all, so this sidesteps the possibility entirely
 * rather than relying on the two numberings happening to agree.
 *
 * Teardown calls `viewer.clear()`, but `clear()` only removes models/
 * surfaces/labels/shapes (`GLViewer.ts:4859`) — it leaves 3Dmol's own
 * `<canvas>` appended to this container, and 3Dmol's own `document`/
 * `window`/`ResizeObserver`/`IntersectionObserver` registrations in
 * place (`GLViewer.ts:285-298`, `:723-749`). Left alone, a second
 * `createViewer()` on the same container — which React's
 * `<StrictMode>` dev double-invoke of effects causes on every mount —
 * stacks a second absolutely-positioned canvas on top of the first.
 * `container.replaceChildren()` after `clear()` removes 3Dmol's own
 * `<canvas>` — and, with it, the DOM-node-scoped listeners 3Dmol
 * registered directly ON THAT CANVAS — outright.
 *
 * That does NOT include this component's own capture-phase wheel/
 * touchstart/touchmove listeners from the "Mouse/touch" section above:
 * those are registered on the *container* itself, a node
 * `replaceChildren()` clears the CONTENTS of but does not itself
 * remove. (An earlier draft of this comment claimed
 * `replaceChildren()` removed those too — it does not; a docstring
 * claiming a property the code doesn't have is worse than no comment,
 * since a reader has no way to tell it's wrong without independently
 * re-deriving the DOM semantics.) They are removed explicitly, by name,
 * in this same effect's cleanup function below (`removeEventListener`
 * calls) — that explicit removal, not `replaceChildren()`, is what
 * stops React `<StrictMode>`'s double-invoke from stacking a second set
 * of these listeners on the container alongside the first.
 */

type ViewerStatus = "loading" | "ready" | "unavailable"

const VIEWER_BACKGROUND = "white"
const ROTATE_STEP_DEG = 20
const ZOOM_FACTOR = 1.2

type StyleMode = "ballstick" | "spacefill" | "wireframe"
type LabelMode = "none" | "symbols" | "numbers" | "both"

/**
 * The one API call, `viewer.setStyle(...)`, that decides what 3Dmol draws
 * for each representation style GaussView/ChemCraft-style tools offer.
 * Kept as a named constant (not inlined) so a future change to a style's
 * visual spec is a one-line diff. `ballstick` is the default and its
 * values are pinned exactly by `GeometryViewer.test.tsx`.
 */
const STYLE_SPECS: Record<StyleMode, Record<string, unknown>> = {
    ballstick: { stick: { radius: 0.14 }, sphere: { scale: 0.28 } },
    // No `scale` — 3Dmol's sphere style defaults to each atom's full Van
    // der Waals radius when scale is omitted, which is exactly a
    // spacefill/CPK representation, not an arbitrary shrunken sphere.
    spacefill: { sphere: {} },
    // NOT `{ line: { linewidth: 2 } }` — that was the first version of
    // this spec, and it rendered as an essentially empty box. WebGL
    // clamps `gl.lineWidth()` to 1.0 in Chromium/ANGLE regardless of the
    // requested value (a longstanding, spec-permitted WebGL1 limitation,
    // not a 3Dmol bug), so `linewidth: 2` was a silent no-op — 3Dmol drew
    // 1px element-coloured lines (grey carbon, white hydrogen) on a white
    // background. Measured on a real 704×704 render: no pixel darker
    // than 200/255, and only 0.021% of pixels darker than 240/255 at
    // all — effectively invisible, vs. ball & stick's minimum pixel
    // value of 2 with 1.5% of the image below 200. A `stick` spec draws
    // actual cylinder geometry (rasterised, not a 1px GL line), so it
    // renders with real contrast regardless of the lineWidth clamp, at a
    // thin enough radius to still read as "wireframe" rather than
    // "ball & stick". This also keeps `STYLE_BOND_NOTE.wireframe` (below)
    // truthful — it still draws an explicit bond primitive.
    wireframe: { stick: { radius: 0.03 } },
}

const STYLE_LABELS: Record<StyleMode, string> = {
    ballstick: "Ball & stick",
    spacefill: "Spacefill",
    wireframe: "Wireframe",
}

/**
 * The bond-disclosure sentence shown under the picture, one full string
 * per style — deliberately NOT a shared boolean (`drawsBonds ? A : B`)
 * selecting between two sentences. A boolean-keyed lookup lets exactly
 * one style's flip corrupt every OTHER style sharing that branch without
 * any test noticing (this happened during review here: flipping
 * `wireframe`'s bond-drawing boolean to `false` made the page render
 * spacefill's "does not draw an explicit bond" sentence while the
 * Wireframe button read `aria-pressed="true"` — a self-contradicting
 * disclosure about inferred-vs-deposited data, the one thing this
 * sentence exists to get right — and every existing test still passed,
 * since only ballstick/spacefill were asserted). Each style owning its
 * own literal sentence here makes that class of bug structurally
 * impossible: there is no shared branch left to corrupt.
 */
const STYLE_BOND_NOTE: Record<StyleMode, string> = {
    ballstick: "Bonds shown are inferred from interatomic distance for legibility only; they are not part of the deposited record.",
    wireframe: "Bonds shown are inferred from interatomic distance for legibility only; they are not part of the deposited record.",
    spacefill: "This spacefill style does not draw an explicit bond between atoms; each sphere marks exactly one deposited atomic position.",
}

const LABEL_MODE_OPTIONS: { value: LabelMode; text: string }[] = [
    { value: "none", text: "None" },
    { value: "symbols", text: "Symbols" },
    { value: "numbers", text: "Numbers" },
    { value: "both", text: "Symbols + numbers" },
]

function labelTextFor(mode: LabelMode, atom: GeometryAtom): string {
    if (mode === "symbols") return atom.element
    if (mode === "numbers") return String(atom.atom_index)
    return `${atom.element}${atom.atom_index}`
}

/** The subset of GLViewer's instance API this component actually calls. */
type Viewer3DHandle = {
    clear?: () => void
    render?: () => void
    rotate?: (angle: number, axis?: string) => unknown
    zoom?: (factor?: number, animationDuration?: number) => unknown
    getView?: () => number[]
    setView?: (view: number[]) => unknown
    setStyle?: (sel: Record<string, unknown>, style: Record<string, unknown>) => unknown
    addLabel?: (text: string, options: Record<string, unknown>) => unknown
    removeAllLabels?: () => unknown
}

function hasMeasuredSize(el: HTMLDivElement) {
    return el.offsetWidth > 0 && el.offsetHeight > 0
}

export function GeometryViewer({
    atoms,
    formula,
    xyzText,
}: {
    atoms: GeometryAtom[]
    formula: string
    xyzText: string | null
}) {
    const containerRef = useRef<HTMLDivElement | null>(null)
    const viewerRef = useRef<Viewer3DHandle | null>(null)
    const initialViewRef = useRef<number[] | null>(null)
    // See the label effect below — tracks whether labels are currently
    // drawn so its ready-transition run (default "none", nothing to
    // clear) can skip a redundant render() call.
    const labelsPresentRef = useRef(false)
    const [status, setStatus] = useState<ViewerStatus>("loading")
    const [style, setStyleMode] = useState<StyleMode>("ballstick")
    const [labelMode, setLabelMode] = useState<LabelMode>("none")
    // 3Dmol unconditionally sets `this._canvas.id = parameters.id`
    // (Renderer.ts's constructor) whether or not an id was passed —
    // leaving it unset stamps the canvas with the literal string
    // `id="undefined"`. A real, stable id also keeps two instances of
    // this component on the same page (unlikely today, but not
    // impossible) from colliding.
    const viewerDomId = useId()

    const xyzForViewer = useMemo(() => {
        if (xyzText) return xyzText
        if (atoms.length === 0) return null
        return buildXyzBlock(atoms)
    }, [xyzText, atoms])

    useEffect(() => {
        const container = containerRef.current
        viewerRef.current = null
        initialViewRef.current = null

        if (!container || !xyzForViewer) {
            setStatus("unavailable")
            return
        }

        let cancelled = false
        let resizeObserver: ResizeObserver | null = null

        // See the module docstring's "Mouse/touch" section: these run in
        // the capture phase on the container (an ancestor of 3Dmol's own
        // canvas) and only ever call stopPropagation(), never
        // preventDefault() — that is what keeps native page scroll intact
        // while still letting 3Dmol's own drag-to-rotate and multi-touch
        // handling through untouched.
        //
        // `touchstart` is intercepted too, on the same one-finger
        // condition as `touchmove` below — measured in a real browser
        // (Chromium via Playwright, CDP-level touch input) that this is
        // load-bearing, not belt-and-suspenders: 3Dmol's `touchstart`
        // handler (`_handleMouseDown`, GLViewer.ts:904) calls
        // `preventDefault()` unconditionally too, and Chromium suppresses
        // scrolling for an *entire* touch sequence once its `touchstart`
        // has been prevented — even if every subsequent `touchmove` in
        // that same sequence is never prevented at all. Intercepting only
        // `touchmove` (as first written, following the reviewer's
        // proposal literally) left the swipe-traps-the-page-instead-of-
        // scrolling regression fully in place; leaving `touchstart`
        // itself un-intercepted was the gap. See this component's PR
        // description for the measured before/after.
        function handleWheelCapture(event: WheelEvent) {
            event.stopPropagation()
        }
        function handleSingleTouchCapture(event: TouchEvent) {
            if (event.touches.length <= 1) {
                event.stopPropagation()
            }
        }
        container.addEventListener("wheel", handleWheelCapture, { capture: true, passive: true })
        container.addEventListener("touchstart", handleSingleTouchCapture, { capture: true, passive: true })
        container.addEventListener("touchmove", handleSingleTouchCapture, { capture: true, passive: true })

        function teardownViewer() {
            const viewer = viewerRef.current
            viewerRef.current = null
            initialViewRef.current = null
            if (!viewer) return
            viewer.clear?.()
            // See the module docstring: clear() alone leaves 3Dmol's own
            // canvas (and its listeners) attached to this container.
            container?.replaceChildren()
        }

        function attemptInit() {
            setStatus("loading")
            import("3dmol")
                .then(($3Dmol) => {
                    if (cancelled) return
                    try {
                        const created = $3Dmol.createViewer(container, {
                            backgroundColor: VIEWER_BACKGROUND,
                            id: `3dmol-canvas-${viewerDomId}`,
                        })
                        if (!created) throw new Error("3Dmol did not return a viewer")
                        created.addModel(xyzForViewer, "xyz")
                        // animationDuration defaults to 0 below — no
                        // camera animation, so there is nothing for
                        // prefers-reduced-motion to gate. No `setStyle`/
                        // `render` call here: an unstyled model draws
                        // nothing visible, so there is no meaningful
                        // frame to render before the style-effect below
                        // (which depends on `status`, so it fires as
                        // soon as `viewerRef.current` is set and
                        // `status` flips to "ready") applies the current
                        // style and renders — once, not twice. See that
                        // effect's own comment for why this split
                        // doesn't duplicate a render call.
                        created.zoomTo()
                        viewerRef.current = created
                        initialViewRef.current = created.getView?.() ?? null
                        setStatus("ready")
                    } catch {
                        // Covers both a thrown `createViewer` (no WebGL
                        // context available — see the module docstring) and
                        // any error from the subsequent addModel/zoomTo
                        // calls.
                        setStatus("unavailable")
                    }
                })
                .catch(() => {
                    if (!cancelled) setStatus("unavailable")
                })
        }

        if (hasMeasuredSize(container)) {
            attemptInit()
        } else if (typeof ResizeObserver !== "undefined") {
            setStatus("loading")
            resizeObserver = new ResizeObserver(() => {
                if (cancelled) return
                const current = containerRef.current
                if (current && hasMeasuredSize(current)) {
                    resizeObserver?.disconnect()
                    resizeObserver = null
                    attemptInit()
                }
            })
            resizeObserver.observe(container)
        } else {
            // No ResizeObserver support at all to ever notice a later
            // resize — report the honest state now rather than hang on
            // "loading" forever with no way to detect success.
            setStatus("unavailable")
        }

        return () => {
            cancelled = true
            resizeObserver?.disconnect()
            container.removeEventListener("wheel", handleWheelCapture, true)
            container.removeEventListener("touchstart", handleSingleTouchCapture, true)
            container.removeEventListener("touchmove", handleSingleTouchCapture, true)
            teardownViewer()
        }
    }, [xyzForViewer, viewerDomId])

    // Applies the selected representation style to the viewer — this is
    // now the ONLY place that ever calls `setStyle`, for both the very
    // first frame and every later style click, which is what removes the
    // latent coupling `attemptInit` used to have: that function no
    // longer hardcodes `STYLE_SPECS.ballstick` (or any style) at all, so
    // there is only one source of truth for "what style is currently
    // applied" instead of two that happen to agree only because the
    // style/label controls are gated on `status === "ready"` (see
    // below). `status` is a deliberate dependency, not an oversight: it
    // is what makes this effect actually fire the moment
    // `viewerRef.current` becomes non-null (a ref by itself is not
    // trackable as a dependency; `status` flipping to "ready" is this
    // component's proxy signal that it did). This does NOT double up
    // with a render `attemptInit` used to do itself — `attemptInit` no
    // longer calls `setStyle`/`render` at all (an unstyled model draws
    // nothing, so there was never a meaningful frame to render before
    // this effect's own render call), so exactly one `setStyle`+`render`
    // pair happens per style change, including the very first one.
    useEffect(() => {
        const viewer = viewerRef.current
        if (!viewer) return
        viewer.setStyle?.({}, STYLE_SPECS[style])
        viewer.render?.()
    }, [style, status])

    // See the module docstring's "Labels follow the coordinate table"
    // section — positions/text come from this component's own `atoms`
    // prop, never from querying 3Dmol's model back out.
    //
    // `status` is a dependency for the same reason as the style effect
    // above (fires once `viewerRef.current` becomes non-null, decoupling
    // this from any hardcoded default in `attemptInit`). Unlike style,
    // "nothing to draw yet" genuinely IS a no-op for labels — the
    // default label mode is "none", so the common case at the
    // ready-transition really does have nothing to clear and nothing to
    // add. `labelsPresentRef` tracks whether labels currently exist so
    // that exact no-op case can skip its `render()` call (keeping the
    // ready-transition's total render count at exactly one — the style
    // effect's — not two), while every run that actually changes
    // anything (including switching back to "none" FROM a labelled
    // state, which must still repaint to make the labels disappear)
    // still renders.
    useEffect(() => {
        const viewer = viewerRef.current
        if (!viewer) return
        const hadLabels = labelsPresentRef.current
        const willHaveLabels = labelMode !== "none"
        if (!hadLabels && !willHaveLabels) return
        viewer.removeAllLabels?.()
        if (willHaveLabels) {
            for (const atom of atoms) {
                viewer.addLabel?.(labelTextFor(labelMode, atom), {
                    position: { x: atom.x, y: atom.y, z: atom.z },
                    fontSize: 12,
                    fontColor: "black",
                    backgroundColor: "white",
                    backgroundOpacity: 0.75,
                    inFront: true,
                    alignment: "center",
                    showBackground: true,
                })
            }
        }
        viewer.render?.()
        labelsPresentRef.current = willHaveLabels
    }, [labelMode, atoms, status])

    function rotateBy(angle: number, axis: "x" | "y") {
        const viewer = viewerRef.current
        if (!viewer) return
        viewer.rotate?.(angle, axis)
        viewer.render?.()
    }

    function zoomBy(factor: number) {
        const viewer = viewerRef.current
        if (!viewer) return
        viewer.zoom?.(factor)
        viewer.render?.()
    }

    function resetView() {
        const viewer = viewerRef.current
        const view = initialViewRef.current
        if (!viewer || !view) return
        viewer.setView?.(view)
        viewer.render?.()
    }

    const bondsSentence = STYLE_BOND_NOTE[style]

    return (
        <div className="geometry-viewer">
            <p className="section-note">
                {`An interactive 3D view of the deposited Cartesian coordinates, rendered client-side with WebGL. ${bondsSentence} The coordinate table and raw XYZ block further down are the authoritative, accessible representation of this geometry, and render whether or not this picture does.`}
            </p>
            {status === "ready" && (
                <>
                    <div
                        className="viewer-controls"
                        role="group"
                        aria-label={`Rotate the 3D view of ${formula || "this geometry"} (does not change the coordinate table)`}
                    >
                        <button type="button" onClick={() => rotateBy(-ROTATE_STEP_DEG, "y")}>Rotate left</button>
                        <button type="button" onClick={() => rotateBy(ROTATE_STEP_DEG, "y")}>Rotate right</button>
                        <button type="button" onClick={() => rotateBy(-ROTATE_STEP_DEG, "x")}>Rotate up</button>
                        <button type="button" onClick={() => rotateBy(ROTATE_STEP_DEG, "x")}>Rotate down</button>
                        <button type="button" onClick={() => zoomBy(ZOOM_FACTOR)}>Zoom in</button>
                        <button type="button" onClick={() => zoomBy(1 / ZOOM_FACTOR)}>Zoom out</button>
                        <button type="button" onClick={resetView}>Reset view</button>
                    </div>
                    <div
                        className="viewer-display-controls"
                        role="group"
                        aria-label={`Display options for the 3D view of ${formula || "this geometry"}`}
                    >
                        <fieldset className="viewer-style-choice">
                            <legend>Style</legend>
                            {(Object.keys(STYLE_LABELS) as StyleMode[]).map((mode) => (
                                <button
                                    key={mode}
                                    type="button"
                                    aria-pressed={style === mode}
                                    onClick={() => setStyleMode(mode)}
                                >
                                    {STYLE_LABELS[mode]}
                                </button>
                            ))}
                        </fieldset>
                        <label className="viewer-label-choice">
                            Atom labels
                            <select
                                value={labelMode}
                                onChange={(event) => setLabelMode(event.target.value as LabelMode)}
                            >
                                {LABEL_MODE_OPTIONS.map((option) => (
                                    <option key={option.value} value={option.value}>{option.text}</option>
                                ))}
                            </select>
                        </label>
                    </div>
                </>
            )}
            <div
                ref={containerRef}
                className="viewer-canvas"
                aria-hidden="true"
                data-viewer-status={status}
            />
            {status === "unavailable" && (
                <p className="empty-projection" role="status">
                    The 3D view could not be initialised — this browser or environment may not support WebGL.
                    The coordinate table and raw XYZ block below are unaffected.
                </p>
            )}
            {status === "loading" && (
                <p className="section-note" role="status">
                    Loading the 3D view of {formula || "this geometry"}…
                </p>
            )}
        </div>
    )
}
