import { useMemo, useState } from "react"
import type { GeometryAtom } from "../api/geometryApi"

/**
 * A hand-rolled SVG projection of deposited Cartesian coordinates — not a
 * WebGL/3Dmol.js/Three.js viewer (this project may not add an npm
 * dependency, and the plan requires the viewer to work with no capability
 * detection at all, not merely degrade gracefully when WebGL is absent).
 *
 * This is a static orthographic projection the reader can rotate in fixed
 * steps via ordinary buttons — never an animated/auto-rotating view, so
 * there is nothing here that interacts with `prefers-reduced-motion`
 * beyond what `index.css` already disables globally (transitions).
 *
 * The SVG itself is `aria-hidden` — it is a supplementary picture, not the
 * accessible representation of the geometry. The coordinate table and raw
 * XYZ block rendered alongside it in `GeometryDetailPage` are the actual
 * accessible/copyable fallback the plan requires. This component only
 * ever adds a picture on top of data that is already fully present
 * elsewhere on the page — and if this component's own render throws
 * (a degenerate geometry, a malformed atom row), `GeometryDetailPage`
 * wraps it in a `SectionErrorBoundary` so the table/XYZ/provenance
 * sections stay mounted regardless. That guarantee lives in the parent,
 * not here — this component makes no promise about what survives its own
 * crash on its own.
 *
 * Bonds drawn between atoms are inferred client-side from interatomic
 * distance for legibility only — the payload carries no bond list, and
 * this is disclosed in the caption rather than presented as deposited
 * data.
 */

const ELEMENT_STYLE: Record<string, { color: string; radius: number }> = {
    H: { color: "#c9d2db", radius: 0.31 },
    C: { color: "#3c4856", radius: 0.76 },
    N: { color: "#205493", radius: 0.71 },
    O: { color: "#b23b3b", radius: 0.66 },
    F: { color: "#4caf7d", radius: 0.57 },
    Cl: { color: "#5fae5f", radius: 0.99 },
    Br: { color: "#a5672b", radius: 1.14 },
    S: { color: "#c9a227", radius: 1.05 },
    P: { color: "#c97a27", radius: 1.07 },
}
const DEFAULT_STYLE = { color: "#7a8a9a", radius: 0.75 }

const VIEWBOX = 320
const CENTER = VIEWBOX / 2
const PADDING = 34
const BOND_DISTANCE_SCALE = 1.3
const ROTATE_STEP_DEG = 20

function styleFor(element: string) {
    return ELEMENT_STYLE[element] ?? DEFAULT_STYLE
}

function toRad(deg: number) {
    return (deg * Math.PI) / 180
}

export function GeometryViewer({ atoms, formula }: { atoms: GeometryAtom[]; formula: string }) {
    const [yaw, setYaw] = useState(-30)
    const [pitch, setPitch] = useState(15)

    const centered = useMemo(() => {
        if (atoms.length === 0) return []
        const cx = atoms.reduce((sum, a) => sum + a.x, 0) / atoms.length
        const cy = atoms.reduce((sum, a) => sum + a.y, 0) / atoms.length
        const cz = atoms.reduce((sum, a) => sum + a.z, 0) / atoms.length
        return atoms.map((a) => ({ ...a, x: a.x - cx, y: a.y - cy, z: a.z - cz }))
    }, [atoms])

    // Bounding radius is rotation-invariant, so the scale factor is
    // computed once and reused across every rotation state — atoms never
    // jump in scale as the reader rotates the view.
    const maxNorm = useMemo(() => {
        const norms = centered.map((a) => Math.sqrt(a.x ** 2 + a.y ** 2 + a.z ** 2))
        return Math.max(...norms, 0.001)
    }, [centered])
    const scale = (CENTER - PADDING) / maxNorm

    const projected = useMemo(() => {
        const yawRad = toRad(yaw)
        const pitchRad = toRad(pitch)
        return centered.map((atom) => {
            // Yaw around Y, then pitch around X.
            const x1 = atom.x * Math.cos(yawRad) + atom.z * Math.sin(yawRad)
            const z1 = -atom.x * Math.sin(yawRad) + atom.z * Math.cos(yawRad)
            const y1 = atom.y
            const y2 = y1 * Math.cos(pitchRad) - z1 * Math.sin(pitchRad)
            const z2 = y1 * Math.sin(pitchRad) + z1 * Math.cos(pitchRad)
            return {
                atom_index: atom.atom_index,
                element: atom.element,
                screenX: CENTER + x1 * scale,
                // SVG y grows downward; flip so "up" in the data reads as up on screen.
                screenY: CENTER - y2 * scale,
                depth: z2,
            }
        }).sort((a, b) => a.depth - b.depth)
    }, [centered, yaw, pitch, scale])

    // O(n²) pair enumeration with an O(1) lookup per pair (a
    // Map<atom_index, atom> built once outside the loop) — the previous
    // version resolved each side of every pair via `centered.find(...)`,
    // an O(n) linear scan *inside* the O(n²) pair loop, i.e. O(n³)
    // overall. At the public per-geometry atom cap (500,
    // `max_geometry_atoms_public`) that was ~10^8 operations on every
    // rotation click.
    const byAtomIndex = useMemo(() => {
        const map = new Map<number, (typeof centered)[number]>()
        for (const atom of centered) map.set(atom.atom_index, atom)
        return map
    }, [centered])

    const bonds = useMemo(() => {
        const pairs: { key: string; x1: number; y1: number; x2: number; y2: number }[] = []
        for (let i = 0; i < projected.length; i += 1) {
            for (let j = i + 1; j < projected.length; j += 1) {
                const a = projected[i]
                const b = projected[j]
                const atomA = byAtomIndex.get(a.atom_index)
                const atomB = byAtomIndex.get(b.atom_index)
                if (!atomA || !atomB) continue
                const dx = atomA.x - atomB.x
                const dy = atomA.y - atomB.y
                const dz = atomA.z - atomB.z
                const distance = Math.sqrt(dx ** 2 + dy ** 2 + dz ** 2)
                const cutoff = (styleFor(atomA.element).radius + styleFor(atomB.element).radius) * BOND_DISTANCE_SCALE
                if (distance <= cutoff) {
                    pairs.push({ key: `${a.atom_index}-${b.atom_index}`, x1: a.screenX, y1: a.screenY, x2: b.screenX, y2: b.screenY })
                }
            }
        }
        return pairs
    }, [projected, byAtomIndex])

    return (
        <div className="geometry-viewer">
            <p className="section-note">
                A 2D projection of the deposited Cartesian coordinates, rotatable in fixed steps below — not an
                interactive 3D molecular viewer, and not WebGL. Bonds shown are inferred from interatomic distance
                for legibility only; they are not part of the deposited record. The coordinate table and raw XYZ
                block further down are the authoritative, accessible representation of this geometry.
            </p>
            <div
                className="viewer-controls"
                role="group"
                aria-label={`Rotate the visual-only projection of ${formula || "this geometry"} (does not change the coordinate table)`}
            >
                <button type="button" onClick={() => setYaw((v) => v - ROTATE_STEP_DEG)}>Rotate left</button>
                <button type="button" onClick={() => setYaw((v) => v + ROTATE_STEP_DEG)}>Rotate right</button>
                <button type="button" onClick={() => setPitch((v) => v - ROTATE_STEP_DEG)}>Rotate up</button>
                <button type="button" onClick={() => setPitch((v) => v + ROTATE_STEP_DEG)}>Rotate down</button>
                <button type="button" onClick={() => { setYaw(-30); setPitch(15) }}>Reset view</button>
            </div>
            <svg
                className="viewer-svg"
                viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
                aria-hidden="true"
                focusable="false"
            >
                {/* No `role="img"` and no `<title>`: an `aria-hidden` element
                    is already removed from the accessibility tree, so an
                    accessible name on it is dead markup that never reaches
                    assistive tech — asserting one here would just be a
                    second, contradictory claim about what this element is. */}
                <g className="viewer-bonds">
                    {bonds.map((bond) => (
                        <line key={bond.key} x1={bond.x1} y1={bond.y1} x2={bond.x2} y2={bond.y2} />
                    ))}
                </g>
                <g className="viewer-atoms">
                    {projected.map((atom) => {
                        const style = styleFor(atom.element)
                        const r = 5 + style.radius * 7
                        return (
                            <circle
                                key={atom.atom_index}
                                cx={atom.screenX}
                                cy={atom.screenY}
                                r={r}
                                fill={style.color}
                                stroke="#20242b"
                                strokeWidth="0.75"
                            />
                        )
                    })}
                </g>
            </svg>
        </div>
    )
}
