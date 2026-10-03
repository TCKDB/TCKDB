import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import "../methods.css"
import { LevelOfTheoryLink } from "../components/LevelOfTheoryLink"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton } from "../components/RefsDisclosure"
import {
    bindingSourceLabel,
    cardinalLabel,
    componentLabel,
    formatCoefficient,
    formulaLabel,
    linearSummary,
    linearityText,
    operationLabel,
    schemeTitle,
    slotLabel,
    termExpression,
} from "../domain/compositeSchemeFormat"
import { words } from "../domain/provenanceFormat"
import { useCompositeScheme } from "../hooks/useCompositeScheme"
import type { CompositeSchemeRecord, CompositeSchemeTerm } from "../api/methodsApi"

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/**
 * `/methods/composite-schemes/:schemeRef` -- a composite recipe (CBS-QB3, G4,
 * a CCSD(T)/CBS extrapolation, a focal-point sum), ADR 0021, P7b. The page
 * answers "what is this number built from": each term with its operation,
 * component and formula, each input level with its cardinal number and
 * weight, the levels of theory bound to the recipe, and the levels the recipe
 * runs internally.
 *
 * Nothing is computed here. Every coefficient is the server's; a non-linear
 * term (the three-point exponential) has none and is said to have none.
 */
export default function CompositeSchemePage() {
    const { schemeRef = "" } = useParams<{ schemeRef: string }>()
    const state = useCompositeScheme(schemeRef)

    if (state.status === "ready") {
        return <CompositeSchemeDetail key={state.record.composite_scheme.composite_scheme_ref} record={state.record} />
    }
    return (
        <RecordStatus
            state={state}
            ref={schemeRef}
            kind="composite scheme"
            loadingDetail="Retrieving this recipe's terms, input levels, and the levels of theory bound to it."
        />
    )
}

function CompositeSchemeDetail({ record }: { record: CompositeSchemeRecord }) {
    const scheme = record.composite_scheme
    const title = schemeTitle(scheme)
    return (
        <section className="conformer-page methods-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to="/methods">Methods</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">{title}</span>
            </nav>

            <PageShell
                identity={(
                    <header className="basin-header">
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Composite recipe · provenance vocabulary</span>
                            </div>
                            <h1 className="t-display-1 record-identity-title">{title}</h1>
                            <p className="composite-label"><code className="data">{scheme.name}</code></p>
                            <p className="t-body section-intro">
                                How one composite energy is put together from the energies of other calculations,
                                and which levels of theory name it.
                            </p>
                            <div className="record-identity-known">
                                <dl className="kv-list record-identity-facts">
                                    <div>
                                        <dt>Composite scheme ref</dt>
                                        <dd className="record-identity-fact-copyable">
                                            <code className="data">{scheme.composite_scheme_ref}</code>
                                            <CopyButton value={scheme.composite_scheme_ref} label="Composite scheme ref" srLabel="value" />
                                        </dd>
                                    </div>
                                    <div><dt>Kind</dt><dd>{words(scheme.kind)}</dd></div>
                                    <div>
                                        <dt>Recipe geometry level</dt>
                                        <dd>
                                            {scheme.geometry_level_of_theory
                                                ? <LevelOfTheoryLink levelOfTheory={scheme.geometry_level_of_theory} />
                                                : "not stated by the recipe"}
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Recipe frequency level</dt>
                                        <dd>
                                            {scheme.frequency_level_of_theory
                                                ? <LevelOfTheoryLink levelOfTheory={scheme.frequency_level_of_theory} />
                                                : "not stated by the recipe"}
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Recipe ZPE scale factor</dt>
                                        <dd>{scheme.recipe_zpe_scale_factor ?? "not stated by the recipe"}</dd>
                                    </div>
                                    <div>
                                        <dt>Source literature ref</dt>
                                        <dd>
                                            {scheme.source_literature_ref
                                                ? (
                                                    <span className="record-identity-fact-copyable">
                                                        <code className="data">{scheme.source_literature_ref}</code>
                                                        <CopyButton value={scheme.source_literature_ref} label="Source literature ref" srLabel="value" />
                                                    </span>
                                                )
                                                : "not recorded"}
                                        </dd>
                                    </div>
                                    {scheme.note && <div><dt>Note</dt><dd>{scheme.note}</dd></div>}
                                </dl>
                            </div>
                        </div>
                        <dl className="kv-list basin-context">
                            <div><dt>Deposited</dt><dd>{isoDate(scheme.created_at)}</dd></div>
                            <div><dt>Terms</dt><dd>{record.terms.length}</dd></div>
                        </dl>
                    </header>
                )}
            >
                <TermsSection record={record} />
                <BoundLevelsSection record={record} />
            </PageShell>
        </section>
    )
}

function TermsSection({ record }: { record: CompositeSchemeRecord }) {
    return (
        <section className="ledger-section" aria-labelledby="composite-terms-heading">
            <SectionHeading
                id="composite-terms-heading"
                kicker="Recipe"
                intro="The terms the total is built from, in order. Each input level carries the weight it has in its term, where the term has fixed weights."
            >
                Terms
            </SectionHeading>
            <p className="note" data-linear-in-energies={String(record.linear_in_energies ?? null)}>
                {linearSummary(record.linear_in_energies)}
            </p>
            {record.terms.length === 0 ? (
                <p className="empty-projection">This recipe states no terms. The energy it names is one number a program reports.</p>
            ) : (
                record.terms.map((term) => <TermBlock key={term.position} term={term} />)
            )}
        </section>
    )
}

function TermBlock({ term }: { term: CompositeSchemeTerm }) {
    const expression = termExpression(term)
    const formula = formulaLabel(term.formula)
    return (
        <div className="composite-term" data-term-position={term.position} data-term-linearity={term.linearity}>
            <h3 className="t-heading-2">Term {term.position}</h3>
            <dl className="kv-list">
                <div><dt>Operation</dt><dd>{operationLabel(term.operation)}</dd></div>
                <div><dt>Energy component</dt><dd>{componentLabel(term.energy_component)}</dd></div>
                <div><dt>Formula</dt><dd>{formula ?? "none"}</dd></div>
                <div><dt>Exponent</dt><dd>{term.exponent ?? "none"}</dd></div>
                <div><dt>Weights</dt><dd>{linearityText(term)}</dd></div>
                {expression && <div className="kv-list--wide"><dt>As a sum</dt><dd><code className="data">{expression}</code></dd></div>}
            </dl>
            <div className="table-scroll">
                <table className="data-table" aria-label={`Inputs of term ${term.position}`}>
                    <thead>
                        <tr>
                            <th scope="col">Input level of theory</th>
                            <th scope="col">Coefficient</th>
                            <th scope="col">Slot</th>
                            <th scope="col">Cardinal number</th>
                        </tr>
                    </thead>
                    <tbody>
                        {term.inputs.map((input, index) => (
                            <tr key={`${input.slot}-${index}`}>
                                <td data-label="Input level of theory"><LevelOfTheoryLink levelOfTheory={input.level_of_theory} /></td>
                                <td data-label="Coefficient" className="num">
                                    {input.coefficient === null || input.coefficient === undefined
                                        ? "none, not a fixed weight"
                                        : formatCoefficient(input.coefficient)}
                                </td>
                                <td data-label="Slot">{slotLabel(input.slot)}</td>
                                <td data-label="Cardinal number">
                                    {input.cardinal_number === null || input.cardinal_number === undefined
                                        ? "none"
                                        : `${input.cardinal_number} (${cardinalLabel(input.cardinal_number)})`}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

function BoundLevelsSection({ record }: { record: CompositeSchemeRecord }) {
    const rows = record.bound_levels_of_theory
    return (
        <section className="ledger-section" aria-labelledby="composite-bound-heading">
            <SectionHeading
                id="composite-bound-heading"
                kicker="Provenance vocabulary"
                intro="Levels of theory whose energy this recipe names. A calculation run at one of them is a composite calculation of this recipe."
            >
                Levels of theory bound to this recipe
            </SectionHeading>
            {rows.length > 0 ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Levels of theory bound to this composite recipe">
                        <thead>
                            <tr>
                                <th scope="col">Level of theory</th>
                                <th scope="col">How it is bound</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={row.level_of_theory.level_of_theory_ref ?? row.level_of_theory.method}>
                                    <td data-label="Level of theory" className="composite-label"><LevelOfTheoryLink levelOfTheory={row.level_of_theory} /></td>
                                    <td data-label="How it is bound">{bindingSourceLabel(row.binding_source)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <p className="empty-projection">No level of theory is bound to this recipe.</p>
            )}
        </section>
    )
}
