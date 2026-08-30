import { useEffect, useMemo, useRef, useState } from "react"
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
 *   (`SectionErrorBoundary` in `GeometryDetailPage` still exists as a
 *   second, independent line of defence against an actual render throw
 *   in this component's own JSX — the two failure modes are handled by
 *   two different mechanisms on purpose.)
 *
 * This component never calls `viewer.spin()` or `viewer.animate()`, and
 * calls `zoomTo()` with its default `animationDuration` of 0 (no camera
 * animation) — so there is nothing here for `prefers-reduced-motion` to
 * need to turn off; the viewer is static-on-load by construction, not by
 * a media-query branch that could be missed.
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
 */

type ViewerStatus = "loading" | "ready" | "unavailable"

const VIEWER_BACKGROUND = "white"

/**
 * The one API call, `viewer.setStyle(...)`, that decides what 3Dmol draws.
 * Kept as a named constant (not inlined) so a future change to the visual
 * style is a one-line diff, and so the "no surface, no cartoon, no
 * volumetric render — atoms and inferred bonds only" decision is visible
 * on its own rather than buried in a call site.
 */
const ATOM_STYLE = { stick: { radius: 0.14 }, sphere: { scale: 0.28 } }

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
    const [status, setStatus] = useState<ViewerStatus>("loading")

    const xyzForViewer = useMemo(() => {
        if (xyzText) return xyzText
        if (atoms.length === 0) return null
        return buildXyzBlock(atoms)
    }, [xyzText, atoms])

    useEffect(() => {
        const container = containerRef.current
        if (!container || !xyzForViewer) {
            setStatus("unavailable")
            return
        }
        if (container.offsetWidth === 0 || container.offsetHeight === 0) {
            // 3Dmol's own Renderer treats a zero-size container as
            // "start lost" (see `parameters.containerWidth == 0 ||
            // parameters.containerHeight == 0` in
            // node_modules/3dmol/src/WebGL/Renderer.ts) and returns from
            // its constructor WITHOUT ever attempting
            // canvas.getContext('webgl') and WITHOUT throwing. A real
            // WebGL failure and an unlaid-out container are therefore
            // two different silent paths on 3Dmol's side, and the
            // try/catch below only ever sees the first one — a viewer
            // built on a zero-size container reports success (`ready`)
            // while never having rendered anything. This check folds
            // that second path into the same honest "unavailable"
            // outcome as an actual WebGL failure, rather than trusting
            // "createViewer did not throw" as proof anything drew.
            //
            // This is also why this component's non-mocked test
            // (`GeometryViewer.webgl-unavailable.test.tsx`) exercises
            // this branch for real without needing to fake a WebGL
            // failure: jsdom never performs layout, so every container
            // measured in a test is genuinely zero-sized — the same
            // condition a real browser hits if this section is measured
            // before its CSS has laid it out.
            setStatus("unavailable")
            return
        }

        let cancelled = false
        let viewer: { clear?: () => void } | null = null
        setStatus("loading")

        import("3dmol")
            .then(($3Dmol) => {
                if (cancelled) return
                try {
                    const created = $3Dmol.createViewer(container, { backgroundColor: VIEWER_BACKGROUND })
                    if (!created) throw new Error("3Dmol did not return a viewer")
                    viewer = created
                    created.addModel(xyzForViewer, "xyz")
                    created.setStyle({}, ATOM_STYLE)
                    // animationDuration defaults to 0 — no camera animation,
                    // so there is nothing for prefers-reduced-motion to gate.
                    created.zoomTo()
                    created.render()
                    setStatus("ready")
                } catch {
                    // Covers both a thrown `createViewer` (no WebGL context
                    // available — see the module docstring) and any error
                    // from the subsequent addModel/setStyle/render calls.
                    setStatus("unavailable")
                }
            })
            .catch(() => {
                if (!cancelled) setStatus("unavailable")
            })

        return () => {
            cancelled = true
            viewer?.clear?.()
        }
    }, [xyzForViewer])

    return (
        <div className="geometry-viewer">
            <p className="section-note">
                An interactive 3D view of the deposited Cartesian coordinates, rendered client-side with WebGL.
                Bonds shown are inferred from interatomic distance for legibility only; they are not part of the
                deposited record. The coordinate table and raw XYZ block further down are the authoritative,
                accessible representation of this geometry, and render whether or not this picture does.
            </p>
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
