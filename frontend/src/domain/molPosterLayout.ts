export type PosterAtom = { element: string; x: number; y: number; z: number }
export type PosterSphere = { index: number; element: string; cx: number; cy: number; r: number; depth: number }
export type PosterBond = { a: number; b: number; x1: number; y1: number; x2: number; y2: number }
export type PosterLayout = { spheres: PosterSphere[]; bonds: PosterBond[] }

// Covalent radii in angstrom; unlisted elements use the fallback.
const COVALENT: Record<string, number> = {
    H: 0.31, B: 0.84, C: 0.76, N: 0.71, O: 0.66, F: 0.57, Si: 1.11, P: 1.07, S: 1.05, Cl: 1.02, Br: 1.2, I: 1.39,
}
const FALLBACK_RADIUS = 1.2
const MAX_BOND_ATOMS = 400
const radiusOf = (element: string) => COVALENT[element] ?? FALLBACK_RADIUS

type Vec = [number, number, number]

/** Eigenvectors of a symmetric 3x3 matrix by cyclic Jacobi sweeps, ordered by descending eigenvalue. */
function principalAxes(m: number[][]): Vec[] {
    const a = m.map((row) => [...row])
    const v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for (let sweep = 0; sweep < 24; sweep += 1) {
        for (const [p, q] of [[0, 1], [0, 2], [1, 2]]) {
            if (Math.abs(a[p][q]) < 1e-12) continue
            const theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
            const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1))
            const c = 1 / Math.sqrt(t * t + 1)
            const s = t * c
            for (let k = 0; k < 3; k += 1) {
                const akp = a[k][p]
                const akq = a[k][q]
                a[k][p] = c * akp - s * akq
                a[k][q] = s * akp + c * akq
            }
            for (let k = 0; k < 3; k += 1) {
                const apk = a[p][k]
                const aqk = a[q][k]
                a[p][k] = c * apk - s * aqk
                a[q][k] = s * apk + c * aqk
            }
            for (let k = 0; k < 3; k += 1) {
                const vkp = v[k][p]
                const vkq = v[k][q]
                v[k][p] = c * vkp - s * vkq
                v[k][q] = s * vkp + c * vkq
            }
        }
    }
    const order = [0, 1, 2].sort((i, j) => a[j][j] - a[i][i])
    return order.map((i): Vec => {
        const axis: Vec = [v[0][i], v[1][i], v[2][i]]
        // The sign follows the largest component so the same input always gives the same picture.
        const big = axis.reduce((best, x) => (Math.abs(x) > Math.abs(best) ? x : best), 0)
        return big < 0 ? [-axis[0], -axis[1], -axis[2]] : axis
    })
}

/**
 * Projects stored coordinates to a square picture: the two widest spreads of the atoms lie in the picture plane, then a
 * fixed small tilt shows depth. The same coordinates always give the same layout.
 */
export function layoutPoster(atoms: PosterAtom[], size: number): PosterLayout {
    if (atoms.length === 0) return { spheres: [], bonds: [] }
    const n = atoms.length
    const centre = atoms.reduce<Vec>((sum, atom) => [sum[0] + atom.x / n, sum[1] + atom.y / n, sum[2] + atom.z / n], [0, 0, 0])
    const rel = atoms.map((atom): Vec => [atom.x - centre[0], atom.y - centre[1], atom.z - centre[2]])
    const cov = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    for (const p of rel) for (let i = 0; i < 3; i += 1) for (let j = 0; j < 3; j += 1) cov[i][j] += p[i] * p[j]
    const [ex, ey, ez] = principalAxes(cov)
    const tilt = 0.35
    const dot = (p: Vec, e: Vec) => p[0] * e[0] + p[1] * e[1] + p[2] * e[2]
    const placed = rel.map((p) => {
        const u = dot(p, ex)
        const w = dot(p, ey)
        const d = dot(p, ez)
        return { x: u, y: w * Math.cos(tilt) + d * Math.sin(tilt), depth: d * Math.cos(tilt) - w * Math.sin(tilt) }
    })
    const reach = Math.max(1e-6, ...placed.map((p) => Math.max(Math.abs(p.x), Math.abs(p.y))))
    const scale = (size / 2 - 22) / reach
    const spheres = atoms.map((atom, index): PosterSphere => ({
        index,
        element: atom.element,
        cx: size / 2 + placed[index].x * scale,
        cy: size / 2 - placed[index].y * scale,
        r: Math.max(3, Math.min(14, (0.2 + radiusOf(atom.element) * 0.35) * Math.min(scale, 40))),
        depth: placed[index].depth,
    }))
    const bonds: PosterBond[] = []
    if (n <= MAX_BOND_ATOMS) {
        for (let i = 0; i < n; i += 1) {
            for (let j = i + 1; j < n; j += 1) {
                const dx = atoms[i].x - atoms[j].x
                const dy = atoms[i].y - atoms[j].y
                const dz = atoms[i].z - atoms[j].z
                const dist = Math.sqrt(dx * dx + dy * dy + dz * dz)
                const limit = 1.2 * (radiusOf(atoms[i].element) + radiusOf(atoms[j].element))
                if (dist > 0.4 && dist <= limit) {
                    bonds.push({ a: i, b: j, x1: spheres[i].cx, y1: spheres[i].cy, x2: spheres[j].cx, y2: spheres[j].cy })
                }
            }
        }
    }
    // Far atoms first so near atoms paint over them.
    spheres.sort((p, q) => p.depth - q.depth)
    return { spheres, bonds }
}
