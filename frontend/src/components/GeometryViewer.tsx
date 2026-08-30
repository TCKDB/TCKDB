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
 * `false`), so drag-to-rotate and two-finger touch gestures work
 * exactly as 3Dmol implements them. On top of that, this component adds
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
 * `container.replaceChildren()` after `clear()` removes 3Dmol's canvas
 * (and, with it, the DOM-node-scoped listeners, including this
 * component's own capture-phase wheel/touchmove listeners) outright.
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
    wireframe: { line: { linewidth: 2 } },
}

const STYLE_LABELS: Record<StyleMode, string> = {
    ballstick: "Ball & stick",
    spacefill: "Spacefill",
    wireframe: "Wireframe",
}

/**
 * Whether the given style draws an explicit bond primitive (a stick or a
 * line) rather than touching/overlapping spheres with no bond geometry
 * of their own. Spacefill is the one style here where it is false — see
 * the module docstring's "Bonds 3Dmol draws" section.
 */
const STYLE_DRAWS_BONDS: Record<StyleMode, boolean> = {
    ballstick: true,
    spacefill: false,
    wireframe: true,
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
                        created.setStyle({}, STYLE_SPECS.ballstick)
                        // animationDuration defaults to 0 on both calls below
                        // — no camera animation, so there is nothing for
                        // prefers-reduced-motion to gate.
                        created.zoomTo()
                        created.render()
                        viewerRef.current = created
                        initialViewRef.current = created.getView?.() ?? null
                        setStatus("ready")
                    } catch {
                        // Covers both a thrown `createViewer` (no WebGL
                        // context available — see the module docstring) and
                        // any error from the subsequent addModel/setStyle/
                        // render calls.
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

    // Re-applies the selected representation style to the existing viewer
    // instance (no re-init, so the reader's current rotation/zoom is not
    // reset by a style change). A no-op until the viewer is ready — the
    // style/label controls are only rendered once `status === "ready"`
    // (see below), so `style` cannot change before then and this effect's
    // very first run (while `viewerRef.current` is still null) is always
    // a no-op, never a redundant duplicate of the initial ballstick
    // setStyle call inside `attemptInit` above.
    useEffect(() => {
        const viewer = viewerRef.current
        if (!viewer) return
        viewer.setStyle?.({}, STYLE_SPECS[style])
        viewer.render?.()
    }, [style])

    // See the module docstring's "Labels follow the coordinate table"
    // section — positions/text come from this component's own `atoms`
    // prop, never from querying 3Dmol's model back out.
    useEffect(() => {
        const viewer = viewerRef.current
        if (!viewer) return
        viewer.removeAllLabels?.()
        if (labelMode !== "none") {
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
    }, [labelMode, atoms])

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

    const bondsSentence = STYLE_DRAWS_BONDS[style]
        ? "Bonds shown are inferred from interatomic distance for legibility only; they are not part of the deposited record."
        : "This spacefill style does not draw an explicit bond between atoms; each sphere marks exactly one deposited atomic position."

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
