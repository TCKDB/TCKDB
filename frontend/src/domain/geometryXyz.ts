import type { GeometryAtom } from "../api/geometryApi"

/**
 * Builds a minimal XYZ-format block from a geometry's own `atoms` rows —
 * the same rows the coordinate table renders — for the (expected-rare)
 * case where the archive did not return a ready-made `xyz_text`.
 * `GeometryViewer` only calls this as a fallback: the archive's own
 * `xyz_text` is passed to 3Dmol unmodified whenever it is present, so
 * this function is never on the path a normal deployment takes.
 *
 * Kept as a small, independently-testable pure function in its own
 * (non-component) module rather than inlined in `GeometryViewer.tsx` —
 * partly so `eslint-plugin-react-refresh`'s
 * `only-export-components` rule does not fire on a `.tsx` file
 * exporting something that is not a component, and partly because this
 * is exactly the kind of "recompute the same data a second way" spot
 * where a transcription bug (a swapped field, a dropped atom) can make
 * a rendered structure and the raw-XYZ block disagree about the same
 * geometry without either one individually looking wrong — see
 * `geometryXyz.test.ts` for a test that pins the exact output.
 */
export function buildXyzBlock(atoms: GeometryAtom[]): string {
    const lines = atoms.map((atom) => `${atom.element} ${atom.x} ${atom.y} ${atom.z}`)
    return `${atoms.length}\n\n${lines.join("\n")}`
}
