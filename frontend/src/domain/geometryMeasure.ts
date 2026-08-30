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
 * Verified two ways (see `geometryMeasure.test.ts`): a hand-computed
 * construction with a known signed torsion (b-c bond along the x-axis, `a`
 * fixed in a reference half-plane, `d` rotated by a known angle θ around
 * that axis, giving +60°/-60° as an independently-derived mirror-image
 * pair, not just "the function agrees with itself"); and an external
 * oracle — a fixture of coordinate sets with dihedrals computed by RDKit's
 * `rdMolTransforms.GetDihedralDeg` (`dihedralOracle.fixture.ts`), generated
 * offline and committed as static data. The oracle exists because the
 * hand-computed construction alone did not catch a real bug: an earlier
 * version of this function built `m1` as `cross(n1, scale(b2, 1/b2Length))`
 * — the wrong operand order — which returns the exact *negation* of the
 * correct torsion for every input (magnitude right, sign backwards,
 * every dihedral read as its own mirror-image conformer). Both the
 * hand-computed test and this function's own self-consistency checks
 * passed anyway, because they were built by recording that version's own
 * output — a self-consistent suite cannot detect a self-consistent sign
 * error. Only comparing against RDKit, a source that does not share this
 * module's code, exposed it (PR #295's review; see the git history of
 * this function and of `geometryMeasure.test.ts`'s "external oracle"
 * describe block for the fix).
 *
 * Returns `NaN` for every degenerate case that leaves the a-b-c or b-c-d
 * half-plane undefined, not only the b-and-c-coincide case: `b` and `c`
 * coinciding (zero-length central bond, no axis to measure a torsion
 * around); `a`-`b` or `c`-`d` coinciding (the a-b-c or b-c-d vertex vector
 * has no direction); and — the case an earlier version of this function
 * missed — any three of the four points being collinear or
 * near-collinear, which makes `n1` or `n2` a (near-)zero vector with no
 * well-defined plane to measure from. That last case is real and
 * reachable from actual clicks, not just a defensive contract boundary:
 * any linear fragment a reader can click (CO₂, HCN, acetylene, the
 * near-linear H···H–C of an abstraction saddle point) puts three of the
 * four picked atoms on one line. Returning `0°` for that case (as an
 * earlier version of this function did, since `atan2(0, 0)` is exactly
 * `0`, not `NaN`) would be a silent wrong answer indistinguishable from a
 * genuine syn-periplanar torsion — see `GeometryViewer.tsx`'s
 * `formatDegrees` for how the DOM surfaces this `NaN` honestly instead.
 */
export function dihedral(a: Vec3, b: Vec3, c: Vec3, d: Vec3): number {
    const b1 = subtract(b, a)
    const b2 = subtract(c, b)
    const b3 = subtract(d, c)
    const b1Length = magnitude(b1)
    const b2Length = magnitude(b2)
    const b3Length = magnitude(b3)
    // b1Length/b3Length === 0 means a coincides with b, or c coincides
    // with d — an even more degenerate relative of the b2Length===0 (b
    // coincides with c) case just below: the a-b-c or b-c-d angle isn't
    // just undefined-by-collinearity, its vertex vector has no direction
    // at all. Guarded here (rather than falling through into a 0/0 in the
    // sine computation just below, which would silently produce NaN <=
    // threshold === false and let the degenerate case slip through to a
    // false answer) for the same reason `angle()` guards its own
    // coincident-vertex case.
    if (b1Length === 0 || b2Length === 0 || b3Length === 0) return NaN
    const n1 = cross(b1, b2)
    const n2 = cross(b2, b3)
    // NaN guard: a zero (or near-zero) n1/n2 means three of the four
    // points are (near-)collinear, which leaves the half-plane the
    // "praxeolitic" formula measures from undefined — see this
    // function's docstring's "Returns NaN" paragraph. A relative
    // threshold (scaled by the bond lengths actually involved), not
    // `=== 0`: near-collinear is exactly as undefined numerically as
    // exactly collinear, and float noise means real near-linear
    // fragments (CO2, HCN, acetylene, a near-linear abstraction saddle
    // point) essentially never hit an exact zero.
    //
    // |n1| = |b1||b2|sin(a-b-c) and |n2| = |b2||b3|sin(b-c-d), so dividing
    // each normal's magnitude by the product of the two bond lengths that
    // built it recovers that sine directly — a genuinely dimensionless,
    // scale-invariant collinearity test, not an arbitrary absolute cutoff
    // that would flag a long, precisely-linear bond as "borderline" while
    // missing a short but equally collinear one.
    const abcSin = magnitude(n1) / (b1Length * b2Length)
    const bcdSin = magnitude(n2) / (b2Length * b3Length)
    const COLLINEAR_SIN_THRESHOLD = 1e-8
    if (abcSin <= COLLINEAR_SIN_THRESHOLD || bcdSin <= COLLINEAR_SIN_THRESHOLD) return NaN
    // NOT cross(n1, scale(b2, 1/b2Length)) — that operand order returns
    // the exact NEGATION of the correct signed torsion (n1 x b2hat =
    // -(b2hat x n1)), which is a mirror-image reading of every torsion:
    // magnitude right, sign backwards, indistinguishable from the correct
    // answer without an external check. See geometryMeasure.test.ts's
    // "external oracle" describe block (RDKit-verified) and PR #295's
    // review for the measured 200/200 mismatch this operand order
    // produced before the fix.
    const m1 = cross(scale(b2, 1 / b2Length), n1)
    const x = dot(n1, n2)
    const y = dot(m1, n2)
    return (Math.atan2(y, x) * 180) / Math.PI
}
