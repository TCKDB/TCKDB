/**
 * Pure Cartesian geometry: distance, angle, and dihedral (torsion) between
 * points, in the same spirit as `geometryXyz.ts` — no 3Dmol dependency, no
 * DOM, no React. `GeometryViewer.tsx` is the only caller: it maps a 3Dmol
 * atom-click callback back to this archive's own `atoms[]` rows (never
 * trusting 3Dmol's internal atom identity beyond the array-position
 * `serial` the XYZ parser assigns — see that component's module docstring,
 * "Labels follow the coordinate table" section, for the established
 * precedent this follows) and hands the resulting `Vec3` points here.
 *
 * All three functions operate on whatever unit the caller's `Vec3` values
 * are in and return a result in that same length unit (`distance`) or in
 * degrees (`angle`, `dihedral` — unitless by construction, so the
 * coordinate unit never matters for those two). This archive's atom
 * coordinates are always ångström on the wire (`coordinate_units` on
 * `GeometryRecord`, see `api/geometryApi.ts`), and every call site in this
 * codebase passes ångström-valued points — `distance()` returns ångström
 * for that reason, not because of anything internal to this module. A
 * caller displaying bohr does so by scaling the *returned* distance by
 * `ANGSTROM_TO_BOHR` (`geometryXyz.ts`), not by scaling the input points:
 * multiplying every input coordinate by a constant scales the computed
 * Euclidean distance by that exact same constant (it is linear), so the
 * two approaches are mathematically identical and re-deriving the sum from
 * bohr-valued points would be strictly redundant work for the same answer.
 */

export type Vec3 = { x: number; y: number; z: number }

function subtract(a: Vec3, b: Vec3): Vec3 {
    return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z }
}

function dot(a: Vec3, b: Vec3): number {
    return a.x * b.x + a.y * b.y + a.z * b.z
}

function cross(a: Vec3, b: Vec3): Vec3 {
    return {
        x: a.y * b.z - a.z * b.y,
        y: a.z * b.x - a.x * b.z,
        z: a.x * b.y - a.y * b.x,
    }
}

function magnitude(a: Vec3): number {
    return Math.sqrt(dot(a, a))
}

function scale(a: Vec3, factor: number): Vec3 {
    return { x: a.x * factor, y: a.y * factor, z: a.z * factor }
}

/**
 * Clamps a value into `[-1, 1]` — specifically for a normalised dot
 * product immediately before `Math.acos`. Two unit vectors' dot product is
 * mathematically bounded to `[-1, 1]`, but floating-point division
 * (`dot(v1, v2) / (|v1| * |v2|)`) can push a value that is mathematically
 * exactly ±1 to e.g. `1.0000000000000002` — MEASURED here, not
 * hypothetical: `angle({x:1,y:1,z:1}, ORIGIN, {x:2,y:2,z:2})` (three
 * exactly-collinear points, same direction) computes a raw cosine of
 * `1.0000000000000002`, and `angle({x:1,y:1,z:1}, ORIGIN,
 * {x:-1,y:-1,z:-1})` (collinear, opposite direction) computes
 * `-1.0000000000000002` — both from `Math.sqrt(3) * Math.sqrt(12)`
 * rounding to a hair under the mathematically-exact 6 rather than exactly
 * 6. `Math.acos` of either unclamped value is `NaN` (outside its domain),
 * which would silently turn "these three atoms are exactly in a line" —
 * a real, unremarkable geometry a reader can legitimately click — into a
 * displayed "NaN°" instead of the correct 0°/180°. See
 * `geometryMeasure.test.ts` for both directions asserted against this
 * exact construction, and a mutation test that removes this clamp to
 * confirm the NaN is real, not a defensive-but-unnecessary guard.
 */
function clampToUnitRange(value: number): number {
    return Math.min(1, Math.max(-1, value))
}

/** Euclidean distance between two points, in whatever unit the inputs are. */
export function distance(a: Vec3, b: Vec3): number {
    return magnitude(subtract(a, b))
}

/**
 * The angle a-b-c in degrees, with `b` as the vertex (the *second*
 * argument — matching second-clicked-atom-is-the-vertex, the order
 * `GeometryViewer` builds a 3-atom selection in). Always in `[0, 180]`:
 * an angle between two vectors has no sign to get wrong (unlike a
 * dihedral — see below), so there is no sign convention to document here.
 *
 * Returns `NaN` if `a` or `c` coincides exactly with the vertex `b` (a
 * zero-length vector has no direction to take an angle from) — this can
 * only happen if a caller passes the same atom position twice, which
 * `GeometryViewer`'s selection model prevents by construction (an
 * already-selected atom toggles OFF on a second click rather than
 * appearing twice in the same selection), so this is a defensive
 * boundary for this pure function's own contract, not a path this
 * codebase's only caller can reach.
 */
export function angle(a: Vec3, b: Vec3, c: Vec3): number {
    const v1 = subtract(a, b)
    const v2 = subtract(c, b)
    const m1 = magnitude(v1)
    const m2 = magnitude(v2)
    if (m1 === 0 || m2 === 0) return NaN
    const cosTheta = clampToUnitRange(dot(v1, v2) / (m1 * m2))
    return (Math.acos(cosTheta) * 180) / Math.PI
}

/**
 * The signed dihedral (torsion) angle a-b-c-d in degrees, in
 * `(-180, 180]` — the angle between the half-plane containing a-b-c and
 * the half-plane containing b-c-d, measured looking down the b-c axis.
 *
 * SIGN MATTERS (this is the whole reason this function exists as a
 * dedicated implementation rather than "just take an angle between two
 * normals" — that loses the sign): a torsion of +60° and -60° describe
 * two different, non-superimposable conformations (they are mirror
 * images of each other around the b-c bond) — getting the sign backwards
 * is a wrong scientific answer that *looks* completely plausible (the
 * magnitude is still right), which is exactly the failure mode a naive
 * `Math.acos`-based implementation (no sign information at all) would
 * produce silently.
 *
 * Implementation: the numerically-stable atan2-based formula (sometimes
 * called the "praxeolitic" formula), NOT `acos` of the angle between the
 * two plane normals `n1 = (b-a)×(c-b)` and `n2 = (c-b)×(d-c)`. That
 * simpler-looking alternative has two independent problems this version
 * avoids: (1) it is sign-blind on its own — `acos` never returns a
 * negative angle, so a caller would need a separate cross-product-based
 * sign correction bolted on afterward, which is an easy place to get the
 * sign backwards (see the module docstring's "sign matters" framing —
 * this is precisely the mistake this function exists to not make); and
 * (2) it inherits the same acos-domain float-precision hazard `angle()`
 * needs `clampToUnitRange` for. `atan2(y, x)` here returns a correctly
 * signed angle across the full circle directly, with no separate acos
 * clamp needed: `atan2`'s domain is all of `(x, y) ≠ (0, 0)`, so there is
 * no boundary-value hazard analogous to `acos`'s `[-1, 1]` — see
 * `geometryMeasure.test.ts`'s mutation test for `angle()`'s clamp, which
 * has no counterpart here for exactly this reason.
 *
 * Verified against a hand-computed construction with a known signed
 * torsion (see `geometryMeasure.test.ts`): points placed so the b-c bond
 * lies along the x-axis, `a` fixed in a reference half-plane, and `d`
 * rotated by a known angle θ around that axis — this construction gives
 * +60°/-60° (mirror-image pair), 0°, and 180° as independently-verifiable
 * fixtures, not just "the function agrees with itself".
 *
 * Returns `NaN` if `b` and `c` coincide (a zero-length central bond has
 * no axis to measure a torsion around) — like `angle()`'s degenerate
 * case, `GeometryViewer`'s selection model cannot construct this from
 * real clicks (see `angle()`'s docstring), so this is a contract
 * boundary for the pure function, not a reachable product state.
 */
export function dihedral(a: Vec3, b: Vec3, c: Vec3, d: Vec3): number {
    const b1 = subtract(b, a)
    const b2 = subtract(c, b)
    const b3 = subtract(d, c)
    const b2Length = magnitude(b2)
    if (b2Length === 0) return NaN
    const n1 = cross(b1, b2)
    const n2 = cross(b2, b3)
    const m1 = cross(n1, scale(b2, 1 / b2Length))
    const x = dot(n1, n2)
    const y = dot(m1, n2)
    return (Math.atan2(y, x) * 180) / Math.PI
}
