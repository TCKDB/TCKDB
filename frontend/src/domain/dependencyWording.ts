/**
 * Canonical plain-words wording for `CalculationDependencyRole`
 * (`backend/app/db/models/common.py::CalculationDependencyRole`, seven
 * values: `optimized_from`, `freq_on`, `single_point_on`, `arkane_source`,
 * `irc_start`, `irc_followup`, `scan_parent`).
 *
 * Both `CalculationDetailPage.tsx`'s Related-calculations sentence list AND
 * `CalculationDependencyGraph.tsx`'s edge labels read from this ONE table,
 * so the vocabulary the two views use for the same `dependencies` payload
 * can never fork — a wording change here is a single edit both surfaces
 * pick up.
 *
 * All seven roles are bespoke here (2026-09 rewrite, below) — a role with
 * no entry falls back to `roleLabel(role)` (the raw token with underscores
 * replaced by spaces) in every view, never to another role's wording. That
 * fallback exists for a role this backend enum has not shipped yet, not
 * for any of the seven current values — see the "falls back to the raw
 * role token" test in `CalculationDetailPage.test.tsx`, which uses a
 * synthetic `some_future_role` and which this table's fallback path must
 * keep satisfying.
 *
 * Owner complaint (2026-09): the live graph drew the arrow from an
 * optimisation DOWN to its frequency child labelled "RUN ON THIS
 * GEOMETRY" — phrased from the PARENT's own point of view even though the
 * arrow itself is drawn parent -> child, so the owner read it backwards
 * ("the optimisation came from them"). Every `edgeLabel` below now reads
 * in the direction of the arrow, source -> target, and names what the
 * TARGET is. `childSentence`/`parentSentence` were checked against the
 * actual parent/child semantics in `backend/app/db/models/calculation.py`
 * (`CalculationDependency.parent_calculation_id` is the geometry/data
 * SOURCE, `.child_calculation_id` is what depends on it) and against
 * `_DEPENDENCY_ROLE_TO_PARENT_TYPE` / `_TS_UPSTREAM_DEPENDENCY_ROLES` /
 * `_TS_DOWNSTREAM_DEPENDENCY_ROLES` in
 * `backend/app/services/{calculation_resolution,trust/rubrics}.py` — not
 * just reworded in place — so each sentence names the calc it is
 * template'd for correctly as parent or child of the edge.
 */

/** `"foo_bar"` -> `"foo bar"`. The one shared fallback for any role (or
 * status word, elsewhere on this page) with no bespoke wording. */
export function roleLabel(role: string): string {
    return role.replaceAll("_", " ")
}

export interface DependencyRoleWording {
    /**
     * Full sentence TEMPLATE for when the calculation being viewed is the
     * CHILD of this edge (`dependency.direction === "child"`) -- the link
     * names the PARENT. Contains exactly one `{link}` placeholder, which
     * the caller (owning the JSX) substitutes with the actual link node.
     */
    childSentence: string
    /**
     * Full sentence TEMPLATE for when the calculation being viewed is the
     * PARENT of this edge (`dependency.direction === "parent"`) -- the
     * link names the CHILD. Contains exactly one `{link}` placeholder.
     */
    parentSentence: string
    /**
     * Short, direction-neutral phrase for a graph edge label, always
     * describing the relationship in the parent -> child (data-flow)
     * direction and naming what the TARGET (child) is -- e.g. "geometry
     * for frequencies" sits on the freq_on arrow and names the frequency
     * calculation the arrow points at, not the geometry it left from.
     * No placeholder.
     */
    edgeLabel: string
}

export const DEPENDENCY_ROLE_WORDING: Record<string, DependencyRoleWording> = {
    optimized_from: {
        childSentence: "This is the fine optimisation; its starting geometry came from {link}",
        parentSentence: "This coarse optimisation's geometry was the starting point for {link}",
        edgeLabel: "starting geometry for the fine optimisation",
    },
    freq_on: {
        childSentence: "This frequency calculation was computed on the geometry from {link}",
        parentSentence: "Frequencies were computed on this geometry by {link}",
        edgeLabel: "geometry for frequencies",
    },
    single_point_on: {
        childSentence: "This single point was computed on the geometry from {link}",
        parentSentence: "The single point was computed on this geometry by {link}",
        edgeLabel: "geometry for single point",
    },
    irc_start: {
        childSentence: "This IRC started from the geometry of {link}",
        parentSentence: "{link} IRC started from this geometry",
        edgeLabel: "starting point for the IRC",
    },
    irc_followup: {
        childSentence: "This is the IRC follow-up that continues {link}",
        parentSentence: "{link} is the IRC follow-up that continues this run",
        edgeLabel: "continued by the IRC follow-up",
    },
    scan_parent: {
        childSentence: "This optimisation continued from the scan {link}",
        parentSentence: "{link} continued from this scan",
        edgeLabel: "parent of the scan",
    },
    arkane_source: {
        childSentence: "This used {link} as an Arkane source",
        parentSentence: "{link} used this as an Arkane source",
        edgeLabel: "source for Arkane",
    },
}

const LINK_PLACEHOLDER = "{link}"

/** The sentence template for the child-side (this calc is the child) --
 * bespoke when `DEPENDENCY_ROLE_WORDING` has an entry, else the same
 * fallback shape `dependencySentence` has always used:
 * "This — {role} — {link}". */
export function dependencyChildSentenceTemplate(role: string): string {
    const wording = DEPENDENCY_ROLE_WORDING[role]
    return wording ? wording.childSentence : `This — ${roleLabel(role)} — ${LINK_PLACEHOLDER}`
}

/** The sentence template for the parent-side (this calc is the parent) --
 * bespoke when `DEPENDENCY_ROLE_WORDING` has an entry, else the same
 * fallback shape `dependencySentence` has always used: "{link} — {role}". */
export function dependencyParentSentenceTemplate(role: string): string {
    const wording = DEPENDENCY_ROLE_WORDING[role]
    return wording ? wording.parentSentence : `${LINK_PLACEHOLDER} — ${roleLabel(role)}`
}

/** Short edge label for the dependency graph, in the parent -> child
 * direction. Falls back to `roleLabel(role)` for a role with no bespoke
 * wording, so an unrecognised role (a future addition to the backend
 * enum, or `some_future_role` in a test) still renders a real word
 * instead of a blank edge. */
export function dependencyEdgeLabel(role: string): string {
    return DEPENDENCY_ROLE_WORDING[role]?.edgeLabel ?? roleLabel(role)
}

/** Splits a `{link}` template into its before/after text around the
 * placeholder, so a caller can interpolate a React node (a `<Link>`)
 * in between without string-concatenating JSX. Every template above
 * contains exactly one `{link}`. */
export function splitLinkTemplate(template: string): { before: string; after: string } {
    const index = template.indexOf(LINK_PLACEHOLDER)
    if (index === -1) return { before: template, after: "" }
    return { before: template.slice(0, index), after: template.slice(index + LINK_PLACEHOLDER.length) }
}
