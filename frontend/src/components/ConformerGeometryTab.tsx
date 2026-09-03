import { Fragment, type CSSProperties } from "react"
import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import type { ConformerProjection } from "../api/speciesEntryApi"
import { lotLabel } from "../api/scientificSchemas"
import { conformerLabel } from "../domain/conformerEvidence"
import { SectionHeading } from "./PageSections"

// This site's established link convention (`conformer-group.css`'s
// `.basin-context a, .stage-table a, .geometry-links a,
// .observation-geometries a` rule: accent colour + underline), applied
// inline here rather than as a new selector. Neither shared stylesheet
// this component already imports (`entry-science.css`'s `.checklist`,
// `species-entry.css`'s -- not imported here -- `.geometry-item`) styles an
// `<a>` nested this way; both currently fall through to the global `a {
// color: inherit; text-decoration: none }` reset in `index.css`, which is
// exactly the "plain black monospace, no link affordance" bug this fixes.
// Kept local to this file (owned by the geometry-review batch) instead of
// adding a rule to either shared stylesheet, which other batches also own.
const linkStyle: CSSProperties = { color: "var(--accent)", textDecoration: "underline", textUnderlineOffset: ".16em" }

type Observation = NonNullable<ConformerProjection["observations"]>[number]
type OptCalc = NonNullable<Observation["calculations"]>[number]
type GeometryLink = NonNullable<ConformerProjection["geometries"]>[number]["geometry"]

// One producing group per DISTINCT geometry an observation's optimisation
// calculations ended at -- most observations have exactly one, but a
// staged coarse-then-fine reoptimisation can run two opt calculations that
// both land on the same final geometry, and that geometry must still
// render as ONE link (not the same ref repeated once per calculation) so
// this stays a row a reader can click, not a row that repeats itself.
type ProducerGroup = { key: string; calcRefs: OptCalc[]; geometry: GeometryLink | null }

function groupByGeometry(optCalcs: OptCalc[], geometryByCalculationRef: Map<string, GeometryLink>): ProducerGroup[] {
    const groups = new Map<string, ProducerGroup>()
    const order: string[] = []
    for (const calculation of optCalcs) {
        const geometry = geometryByCalculationRef.get(calculation.calculation_ref) ?? null
        // Calculations with no matching geometry each get their own group --
        // there is no shared identity to merge them on.
        const key = geometry ? `geom:${geometry.geometry_ref}` : `calc:${calculation.calculation_ref}`
        const existing = groups.get(key)
        if (existing) {
            existing.calcRefs.push(calculation)
        } else {
            groups.set(key, { key, calcRefs: [calculation], geometry })
            order.push(key)
        }
    }
    return order.map((key) => groups.get(key)!)
}

/**
 * Reads straight off the conformer projection already loaded for the
 * picker -- no extra request.
 *
 * One row per deposited observation (never one row per stored geometry with
 * no attribution -- the bug this replaces): each observation's own
 * optimisation calculation(s), the level of theory each ran at, and the
 * geometry it ended at. A geometry a reader could previously only see as a
 * bare ref + truncated hash, with nothing on the page saying which
 * calculation, level of theory, or observation produced it -- "geom_...
 * final · 6 atoms · 427b9a..." repeated across fourteen indistinguishable
 * rows -- now sits directly under the observation and calculation that
 * produced it, each a real link.
 *
 * The summary "N optimization calculation(s)" line per observation is kept
 * as its own list item, byte-identical to before this change, alongside
 * (not merged into) the new per-calculation detail rows -- `SpeciesEntryPage
 * .test.tsx` asserts that line's exact text and is outside this batch's
 * file list, so it is preserved rather than folded into a longer sentence
 * that would break it.
 */
export function ConformerGeometryTab({ conformer }: { conformer: ConformerProjection }) {
    const observations = conformer.observations ?? []

    // The geometry-link entries on the wire (`conformer.geometries`) carry
    // only a `calculation_ref` (see `api/speciesEntryApi.ts`) -- the level
    // of theory for that calculation lives on the matching row inside
    // `observations[].calculations`, so join the two here rather than
    // showing a geometry with no calculation/LOT attribution at all.
    const geometryByCalculationRef = new Map(
        (conformer.geometries ?? []).map((link) => [link.calculation_ref, link.geometry]),
    )

    return (
        <section className="ledger-section" aria-labelledby="geometry-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Evidence for this conformer</p>
                <SectionHeading id="geometry-heading">Geometry</SectionHeading>
                <p>
                    One row per deposited observation for {conformerLabel(conformer)}: each optimisation
                    calculation it carries, the level of theory it ran at, and the geometry it ended at.
                </p>
            </div>
            {observations.length === 0 ? (
                <p className="empty-projection">No stored geometry is projected for this conformer.</p>
            ) : (
                <ul className="checklist">
                    {observations.map((observation) => {
                        const ref = observation.conformer_observation.conformer_observation_ref
                        const optCalcs = (observation.calculations ?? []).filter((calculation) => calculation.type === "opt")
                        const groups = groupByGeometry(optCalcs, geometryByCalculationRef)
                        return (
                            <Fragment key={ref}>
                                <li>
                                    <Link to={`/conformer-observations/${ref}`} style={linkStyle}>{ref}</Link>
                                    {" — "}{optCalcs.length} optimization calculation{optCalcs.length === 1 ? "" : "s"}
                                </li>
                                {groups.map((group) => {
                                    const { geometry } = group
                                    return (
                                        <li key={`${ref}-${group.key}`}>
                                            {group.calcRefs.map((calculation, index) => (
                                                <Fragment key={calculation.calculation_ref}>
                                                    {index > 0 && ", "}
                                                    <Link to={`/calculations/${calculation.calculation_ref}`} style={linkStyle}>
                                                        {calculation.calculation_ref}
                                                    </Link>
                                                    {calculation.level_of_theory ? ` (${lotLabel(calculation.level_of_theory)})` : " (level of theory not recorded)"}
                                                </Fragment>
                                            ))}
                                            {" → "}
                                            {geometry ? (
                                                <>
                                                    <Link to={`/geometries/${geometry.geometry_ref}`} style={linkStyle}>
                                                        {geometry.geometry_ref}
                                                    </Link>
                                                    {geometry.natoms != null ? ` · ${geometry.natoms} atoms` : ""}
                                                    {geometry.geom_hash ? ` · ${geometry.geom_hash.slice(0, 12)}…` : ""}
                                                </>
                                            ) : "geometry not recorded"}
                                        </li>
                                    )
                                })}
                            </Fragment>
                        )
                    })}
                </ul>
            )}
        </section>
    )
}
