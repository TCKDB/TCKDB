import { useMemo } from "react"
import "../mol-poster.css"
import { layoutPoster } from "../domain/molPosterLayout"
import type { GeometryAtom } from "../api/geometryApi"

const SIZE = 240
const ELEMENT_CLASS: Record<string, string> = { C: "c", H: "h", O: "o", N: "n" }

/** A ball-and-stick picture of stored coordinates, drawn once with no interaction. Bonds are inferred from distance. */
export function MolPoster({ atoms, formula }: { atoms: GeometryAtom[]; formula: string }) {
    const layout = useMemo(
        () => layoutPoster(atoms.map((atom) => ({ element: atom.element, x: atom.x, y: atom.y, z: atom.z })), SIZE),
        [atoms],
    )
    return (
        <svg
            className="mol-poster"
            viewBox={`0 0 ${SIZE} ${SIZE}`}
            width={SIZE}
            height={SIZE}
            role="img"
            aria-label={`Ball-and-stick picture of ${formula || "this geometry"}`}
        >
            {layout.bonds.map((bond) => (
                <line key={`${bond.a}-${bond.b}`} className="mol-poster-bond" x1={bond.x1} y1={bond.y1} x2={bond.x2} y2={bond.y2} />
            ))}
            {layout.spheres.map((sphere) => (
                <circle
                    key={sphere.index}
                    className={`mol-poster-atom mol-poster-atom--${ELEMENT_CLASS[sphere.element] ?? "other"}`}
                    cx={sphere.cx}
                    cy={sphere.cy}
                    r={sphere.r}
                />
            ))}
        </svg>
    )
}
