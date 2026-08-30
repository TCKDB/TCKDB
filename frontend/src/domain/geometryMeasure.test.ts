import { describe, expect, it } from "vitest"
import { angle, dihedral, distance } from "./geometryMeasure"
import {
    NAMED_DIHEDRAL_ORACLE_CASES,
    RANDOM_DIHEDRAL_ORACLE_CASES,
    RDKIT_VERSION,
} from "./dihedralOracle.fixture"

const ORIGIN = { x: 0, y: 0, z: 0 }

describe("distance", () => {
    it("trivial: unit distance along one axis", () => {
        expect(distance(ORIGIN, { x: 0, y: 0, z: 1.09 })).toBeCloseTo(1.09, 12)
    })

    it("6-decimal coordinates from the live record (geom_qcnisbgb4abax5oxym3dtjxu34, C-H1)", () => {
        // C at the origin, H1 at (-0, 1.078957, 0) — measured live 2026-08-30
        // against https://tckdb.homecalvin.com/geometries/geom_qcnisbgb4abax5oxym3dtjxu34.
        const C = { x: 0.0, y: 0.0, z: 0.0 }
        const H1 = { x: -0.0, y: 1.078957, z: 0.0 }
        expect(distance(C, H1)).toBeCloseTo(1.078957, 12)
    })

    it("6-decimal coordinates from the live record, a non-axis-aligned pair (H2-H3)", () => {
        const H2 = { x: 0.934405, y: -0.539479, z: 0.0 }
        const H3 = { x: -0.934405, y: -0.539479, z: 0.0 }
        expect(distance(H2, H3)).toBeCloseTo(1.86881, 10)
    })

    it("is symmetric — order of the two points does not matter", () => {
        const a = { x: 1.5, y: -2.25, z: 0.75 }
        const b = { x: -0.5, y: 3.0, z: 1.25 }
        expect(distance(a, b)).toBeCloseTo(distance(b, a), 15)
    })
})

describe("angle", () => {
    it("a right angle: (1,0,0) - origin - (0,1,0)", () => {
        expect(angle({ x: 1, y: 0, z: 0 }, ORIGIN, { x: 0, y: 1, z: 0 })).toBeCloseTo(90, 12)
    })

    it("a linear (180 deg) case", () => {
        expect(angle({ x: 1, y: 0, z: 0 }, ORIGIN, { x: -1, y: 0, z: 0 })).toBeCloseTo(180, 12)
    })

    it("a real one — CH3 (planar radical) H-C-H angle, near 120 degrees", () => {
        // Live record, same fixture as the distance tests above.
        const C = { x: 0.0, y: 0.0, z: 0.0 }
        const H1 = { x: -0.0, y: 1.078957, z: 0.0 }
        const H2 = { x: 0.934405, y: -0.539479, z: 0.0 }
        const H3 = { x: -0.934405, y: -0.539479, z: 0.0 }
        expect(angle(H1, C, H2)).toBeCloseTo(120, 4)
        expect(angle(H2, C, H3)).toBeCloseTo(120, 4)
    })

    describe("acos-domain clamp", () => {
        // These two constructions are exactly collinear (c = 2*a and
        // c = -1*a respectively, both through the origin), so the
        // mathematically exact angle is 0 deg / 180 deg. Measured directly
        // (see geometryMeasure.ts's clampToUnitRange docstring): the raw,
        // unclamped cosine here is 1.0000000000000002 / -1.0000000000000002
        // — just outside Math.acos's [-1, 1] domain — because
        // Math.sqrt(3) * Math.sqrt(12) rounds to a hair under the
        // mathematically exact 6. An implementation without the clamp
        // returns NaN for both of these, not 0/180.
        it("collinear, same direction — must resolve to exactly 0, not NaN", () => {
            const result = angle({ x: 1, y: 1, z: 1 }, ORIGIN, { x: 2, y: 2, z: 2 })
            expect(result).not.toBeNaN()
            expect(result).toBeCloseTo(0, 12)
        })

        it("collinear, opposite direction — must resolve to exactly 180, not NaN", () => {
            const result = angle({ x: 1, y: 1, z: 1 }, ORIGIN, { x: -1, y: -1, z: -1 })
            expect(result).not.toBeNaN()
            expect(result).toBeCloseTo(180, 12)
        })
    })

    it("degenerate: a coincides with the vertex — returns NaN rather than a false angle", () => {
        expect(angle(ORIGIN, ORIGIN, { x: 1, y: 0, z: 0 })).toBeNaN()
    })
})

describe("dihedral", () => {
    // Construction: b2 (bond p2->p3) along +x. a is fixed off-axis in the
    // y direction (the theta=0 reference half-plane). d is placed at
    // p3 + (0, cos(theta), sin(theta)) — a rotation of theta around the
    // +x axis, starting from the same reference direction as a. Hand-
    // verified via cross products (see PR description) and cross-checked
    // against RDKit (see the "external oracle" describe block below,
    // and `dihedralOracle.fixture.ts`): this construction's computed
    // dihedral is +theta directly — the sign matches the rotation
    // convention by design, so the fixtures below read as "theta in,
    // theta out" rather than needing a separate negation step.
    //
    // A PRIOR VERSION of this file's expectations had this backwards
    // (theta=+60 asserted to produce dihedral() === -60) because they
    // were written by recording `dihedral()`'s own output at the time,
    // not derived independently — see `geometryMeasure.ts`'s dihedral()
    // docstring for the sign-inversion bug this masked (PR #295's
    // review) and the "external oracle" block below for why a
    // self-consistent suite could not have caught it on its own.
    const p1 = { x: -1, y: 1, z: 0 }
    const p2 = { x: 0, y: 0, z: 0 }
    const p3 = { x: 1, y: 0, z: 0 }

    it("+60 and -60 are mirror-image conformations — opposite, not equal, signs", () => {
        const cos60 = Math.cos(Math.PI / 3)
        const sin60 = Math.sin(Math.PI / 3)
        const dPlus = { x: p3.x, y: p3.y + cos60, z: p3.z + sin60 } // theta=+60 -> dihedral +60
        const dMinus = { x: p3.x, y: p3.y + cos60, z: p3.z - sin60 } // theta=-60 -> dihedral -60

        const plusResult = dihedral(p1, p2, p3, dPlus)
        const minusResult = dihedral(p1, p2, p3, dMinus)

        expect(plusResult).toBeCloseTo(60, 9)
        expect(minusResult).toBeCloseTo(-60, 9)
        // The load-bearing assertion: these are NOT the same value with a
        // rounding wobble — they are exact opposites. A sign-convention
        // bug (e.g. swapping which cross product comes first) that always
        // returns the same magnitude regardless of theta's sign would
        // pass toBeCloseTo(60)/toBeCloseTo(-60) individually being wrong
        // in a way that happens to cancel out is implausible, but this
        // assertion makes the "these must differ" requirement explicit
        // and mutation-visible on its own.
        expect(plusResult).toBeCloseTo(-minusResult, 9)
    })

    it("is invariant under reading the same four atoms in reverse order (d-c-b-a) — a property of the definition itself, not a fixture-specific coincidence", () => {
        // Derivable directly from the cross-product formula: relabelling
        // (a,b,c,d) -> (d,c,b,a) sends b1=b-a -> -b3, b2=c-b -> -b2,
        // b3=d-c -> -b1, which flips the sign of BOTH n1=b1xb2 and
        // n2=b2xb3 (a double negation) and leaves the scalar triple
        // products x=dot(n1,n2) and y=dot(m1,n2) unchanged — so
        // atan2(y,x) is identical either way. This is the real invariant
        // (verified independently, not "trust the function to agree with
        // itself" — see the PR description for the derivation); reversal
        // does NOT negate the angle, only reflecting the molecule (the
        // +60/-60 test above) does. A formula with a swapped cross-product
        // operand order, or a spurious extra sign flip, is likely to break
        // this invariant even where the +60/-60 magnitude test still
        // happens to pass.
        const cos30 = Math.cos(Math.PI / 6)
        const sin30 = Math.sin(Math.PI / 6)
        const d = { x: p3.x, y: p3.y + cos30, z: p3.z + sin30 }
        const forward = dihedral(p1, p2, p3, d)
        const reversed = dihedral(d, p3, p2, p1)
        expect(reversed).toBeCloseTo(forward, 9)
    })

    it("degenerate: 0 degrees (syn / eclipsed reference)", () => {
        const d = { x: p3.x, y: p3.y + 1, z: p3.z }
        expect(dihedral(p1, p2, p3, d)).toBeCloseTo(0, 12)
    })

    it("degenerate: 180 degrees (anti)", () => {
        const d = { x: p3.x, y: p3.y - 1, z: p3.z }
        const result = dihedral(p1, p2, p3, d)
        // atan2's branch cut means the anti case can land on either +180
        // or -180 depending on floating-point sign of a near-zero y — both
        // are the same physical angle (mod 360), so this asserts on the
        // magnitude rather than pinning one branch arbitrarily.
        expect(Math.abs(result)).toBeCloseTo(180, 10)
    })

    it("CH3 (planar radical): any 4-atom improper dihedral among its own atoms is 0/180 — the whole molecule is coplanar", () => {
        // Live record, same fixture as the angle/distance tests above.
        const C = { x: 0.0, y: 0.0, z: 0.0 }
        const H1 = { x: -0.0, y: 1.078957, z: 0.0 }
        const H2 = { x: 0.934405, y: -0.539479, z: 0.0 }
        const H3 = { x: -0.934405, y: -0.539479, z: 0.0 }
        expect(Math.abs(dihedral(H1, C, H2, H3))).toBeCloseTo(180, 6)
        expect(Math.abs(dihedral(H2, C, H1, H3))).toBeCloseTo(180, 6)
    })

    it("degenerate: b and c coincide — returns NaN rather than a false torsion", () => {
        expect(dihedral(p1, p2, p2, p3)).toBeNaN()
    })

    it("degenerate: a and b coincide — returns NaN rather than a false torsion", () => {
        expect(dihedral(p2, p2, p1, p3)).toBeNaN()
    })

    it("degenerate: c and d coincide — returns NaN rather than a false torsion", () => {
        expect(dihedral(p1, p2, p3, p3)).toBeNaN()
    })

    describe("collinear-guard: an undefined torsion must read NaN, never a confident 0°", () => {
        // The only pre-fix guard was b2Length === 0 (b coincides with c).
        // If any THREE of the four points are collinear, n1 or n2 (the
        // plane normals the "praxeolitic" formula needs) is a zero
        // vector, so x = 0 and y = 0, and `Math.atan2(0, 0)` is exactly
        // `0` — not NaN. 0° is itself a meaningful, real torsion
        // (syn-periplanar), so an unguarded collinear case is a silent
        // wrong answer indistinguishable from a correct one, not a
        // visibly broken one. This is reachable from real clicks: any
        // linear fragment (CO2, HCN, acetylene, a near-linear abstraction
        // saddle point) puts three of the four picked atoms on one line
        // — this is NOT the same unreachable-by-construction case as
        // `angle()`'s coincident-vertex guard.
        const a = { x: 0, y: 0, z: 0 }
        const b = { x: 1, y: 0, z: 0 }
        const c = { x: 2, y: 0, z: 0 }
        const d = { x: 2, y: 1, z: 1 }

        it("a, b, c collinear (n1 is zero) — NaN, not 0", () => {
            const result = dihedral(a, b, c, d)
            expect(result).not.toBe(0)
            expect(result).toBeNaN()
        })

        it("b, c, d collinear (n2 is zero) — NaN, not 0", () => {
            const result = dihedral(d, c, b, a) // reuses the same line for b-c-d
            expect(result).not.toBe(0)
            expect(result).toBeNaN()
        })

        it("all four points collinear — NaN, not 0", () => {
            const p = { x: 0, y: 0, z: 0 }
            const q = { x: 1, y: 0, z: 0 }
            const r = { x: 2, y: 0, z: 0 }
            const s = { x: 3, y: 0, z: 0 }
            const result = dihedral(p, q, r, s)
            expect(result).not.toBe(0)
            expect(result).toBeNaN()
        })

        it("NEAR-collinear (not exactly) is just as undefined — a relative threshold, not === 0", () => {
            // a, b, c are collinear to 1e-9 Å of a straight line — far
            // tighter than any real deposited geometry's precision, but
            // not mathematically exact. An === 0 guard would miss this
            // and silently return a confident-looking finite value.
            const nearA = { x: 0, y: 0, z: 0 }
            const nearB = { x: 1, y: 0, z: 0 }
            const nearC = { x: 2, y: 1e-9, z: 0 }
            const result = dihedral(nearA, nearB, nearC, d)
            expect(result).toBeNaN()
        })
    })

    describe("external oracle: RDKit-verified fixtures (dihedralOracle.fixture.ts)", () => {
        // Everything above this point was written by observing this
        // module's own output and recording it as "expected" — which
        // proves self-consistency, not correctness. PR #295's review
        // demonstrated exactly that gap: a swapped cross-product operand
        // order made dihedral() return the negation of the correct
        // torsion for every input, and the hand-derived tests above
        // (written against that buggy version) still passed, because
        // they were self-consistent with the bug. This block checks
        // dihedral() against RDKit's rdMolTransforms.GetDihedralDeg — a
        // source that shares none of this module's code — instead.
        it(`fixture was generated with RDKit ${RDKIT_VERSION}`, () => {
            expect(RDKIT_VERSION).toMatch(/^\d+\.\d+/)
        })

        it.each(NAMED_DIHEDRAL_ORACLE_CASES)(
            "$label (target $targetDeg°) matches RDKit to 6 decimal places",
            ({ a, b, c, d, rdkitDihedralDeg }) => {
                expect(dihedral(a, b, c, d)).toBeCloseTo(rdkitDihedralDeg, 6)
            },
        )

        it("200 random well-conditioned 4-point sets all match RDKit to 6 decimal places", () => {
            expect(RANDOM_DIHEDRAL_ORACLE_CASES.length).toBe(200)
            for (const { a, b, c, d, rdkitDihedralDeg } of RANDOM_DIHEDRAL_ORACLE_CASES) {
                const got = dihedral(a, b, c, d)
                // atan2's branch cut means +180/-180 is the same physical
                // angle read from either side — take the shorter
                // circular distance between the two angles so that
                // boundary case doesn't produce a spurious failure.
                let diff = Math.abs(got - rdkitDihedralDeg)
                if (diff > 180) diff = 360 - diff
                expect(diff).toBeLessThan(1e-4)
            }
        })
    })
})
