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
 * A role with no bespoke entry (only three of the seven exist in today's
 * data: `optimized_from`, `freq_on`, `single_point_on`, `irc_start`; the
 * other three -- `arkane_source`, `irc_followup`, `scan_parent` -- have
 * never been seen on the live archive) falls back to `roleLabel(role)`
 * (the raw token with underscores replaced by spaces) in every view,
 * never to another role's wording -- see the "falls back to the raw role
 * token" test in `CalculationDetailPage.test.tsx`, which this table's
 * fallback path must keep satisfying.
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
     * direction -- e.g. "optimized from" reads as "parent optimized
     * from -> child" when placed on the arrow between the two boxes.
     * No placeholder.
     */
    edgeLabel: string
}

export const DEPENDENCY_ROLE_WORDING: Record<string, DependencyRoleWording> = {
    optimized_from: {
        childSentence: "This was optimized from {link}",
        parentSentence: "{link} was optimized from this result",
        edgeLabel: "optimized from",
    },
    freq_on: {
        childSentence: "This frequency calculation was run on the geometry from {link}",
        parentSentence: "{link} (frequency) was run on this geometry",
        edgeLabel: "run on this geometry",
    },
    single_point_on: {
        childSentence: "This single point was run on the geometry from {link}",
        parentSentence: "{link} single point was run on this geometry",
        edgeLabel: "run on this geometry",
    },
    irc_start: {
        childSentence: "This IRC started from the geometry of {link}",
        parentSentence: "{link} IRC started from this geometry",
        edgeLabel: "IRC started from this geometry",
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
