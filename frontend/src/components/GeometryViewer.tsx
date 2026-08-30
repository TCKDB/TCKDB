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
 *   two different mechanisms on purpose.)
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
 * `zoomTo()` on load and `rotate()`/`setView()` from the buttons below
 * are all called with no animation duration (0, the default for each) —
 * so there is nothing here for `prefers-reduced-motion` to need to turn
 * off; every view change is an instant snap by construction, not a
 * media-query branch that could be missed.
 *
 * `nomouse: true` is passed to `createViewer` — 3Dmol's default mouse/
 * touch handling installs non-passive `wheel`/`touchstart`/`touchmove`
 * listeners on its canvas and calls `preventDefault()` on all of them
 * unconditionally (`GLViewer.ts:295-297`, `:1054-1055`). On a viewer
 * that is most of the width of a phone screen, that traps a reader's
 * swipe-to-scroll (the page does not scroll; the molecule spins
 * instead) and a desktop reader's wheel-to-scroll (zooms the molecule
 * instead of scrolling the page) — a regression the previous SVG
 * projection did not have, since it was never large enough nor did it
 * capture input at all. The rotate/reset buttons below are this
 * component's own replacement input, and are ordinary keyboard-operable
 * `<button>` elements rather than a canvas capturing every gesture.
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
 * caption rather than presented as deposited data.
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
 * (and, with it, the DOM-node-scoped listeners) outright.
 */

type ViewerStatus = "loading" | "ready" | "unavailable"

const VIEWER_BACKGROUND = "white"
const ROTATE_STEP_DEG = 20

/**
 * The one API call, `viewer.setStyle(...)`, that decides what 3Dmol draws.
 * Kept as a named constant (not inlined) so a future change to the visual
 * style is a one-line diff, and so the "no surface, no cartoon, no
 * volumetric render — atoms and inferred bonds only" decision is visible
 * on its own rather than buried in a call site.
 */
const ATOM_STYLE = { stick: { radius: 0.14 }, sphere: { scale: 0.28 } }

/** The subset of GLViewer's instance API this component actually calls. */
type Viewer3DHandle = {
    clear?: () => void
    render?: () => void
    rotate?: (angle: number, axis?: string) => unknown
    getView?: () => number[]
    setView?: (view: number[]) => unknown
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
                            nomouse: true,
                            id: `3dmol-canvas-${viewerDomId}`,
                        })
                        if (!created) throw new Error("3Dmol did not return a viewer")
                        created.addModel(xyzForViewer, "xyz")
                        created.setStyle({}, ATOM_STYLE)
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
            teardownViewer()
        }
    }, [xyzForViewer, viewerDomId])

    function rotateBy(angle: number, axis: "x" | "y") {
        const viewer = viewerRef.current
        if (!viewer) return
        viewer.rotate?.(angle, axis)
        viewer.render?.()
    }

    function resetView() {
        const viewer = viewerRef.current
        const view = initialViewRef.current
        if (!viewer || !view) return
        viewer.setView?.(view)
        viewer.render?.()
    }

    return (
        <div className="geometry-viewer">
            <p className="section-note">
                An interactive 3D view of the deposited Cartesian coordinates, rendered client-side with WebGL.
                Bonds shown are inferred from interatomic distance for legibility only; they are not part of the
                deposited record. The coordinate table and raw XYZ block further down are the authoritative,
                accessible representation of this geometry, and render whether or not this picture does.
            </p>
            {status === "ready" && (
                <div
                    className="viewer-controls"
                    role="group"
                    aria-label={`Rotate the 3D view of ${formula || "this geometry"} (does not change the coordinate table)`}
                >
                    <button type="button" onClick={() => rotateBy(-ROTATE_STEP_DEG, "y")}>Rotate left</button>
                    <button type="button" onClick={() => rotateBy(ROTATE_STEP_DEG, "y")}>Rotate right</button>
                    <button type="button" onClick={() => rotateBy(-ROTATE_STEP_DEG, "x")}>Rotate up</button>
                    <button type="button" onClick={() => rotateBy(ROTATE_STEP_DEG, "x")}>Rotate down</button>
                    <button type="button" onClick={resetView}>Reset view</button>
                </div>
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
