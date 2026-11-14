import { GuideLink } from "../components/GuideLink"
import { useParams } from "react-router-dom"
import "../conformer-group.css"
import "../methods.css"
import { LevelOfTheoryLink } from "../components/LevelOfTheoryLink"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { Stored } from "../components/QuantityValue"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
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
import { ledeText } from "../domain/ledeText"
import { deposited } from "../domain/quantityFormat"
import { useCompositeScheme } from "../hooks/useCompositeScheme"
import type { CompositeSchemeRecord, CompositeSchemeTerm } from "../api/methodsApi"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

const COMPOSITE_SCHEME_LEDE = ledeText`How a composite energy is defined, and which levels of theory name it.`

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/** `/methods/composite-schemes/:schemeRef`: a composite recipe, with its terms, their input levels and the levels of theory bound to it. */
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
    useDocumentTitle("Composite scheme", title)
    return (
        <section className="conformer-page methods-page">
            <PageShell
                crumbs={[{ label: "Methods", to: "/methods" }, { label: title }]}
                identity={(
                    <header className="basin-header">
                        <RecordIdentityHeader
                            kicker="Composite recipe"
                            lede={COMPOSITE_SCHEME_LEDE}
                            title={title}
                            ownRef={{ label: "Composite scheme ref", value: scheme.composite_scheme_ref }}
                            ownFacts={(
                                <>
                                    {scheme.kind === "named_method"
                                        ? <div><dt>Kind</dt><dd>{scheme.kind.replaceAll("_", " ")}</dd></div>
                                        : (
                                        <div className="kv-list--wide"><dt>Recipe label</dt><dd className="composite-label"><code className="data">{scheme.name}</code></dd></div>
                                    )}
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
                                        <dd><ZpeScaleFactor value={scheme.recipe_zpe_scale_factor} /></dd>
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
                                    {scheme.kind === "named_method" && scheme.note && <div><dt>Note</dt><dd>{scheme.note}</dd></div>}
                                </>
                            )}
                        />
                        <p><GuideLink to="composite-recipe" /></p>
                        <dl className="kv-list basin-context">
                            <div><dt>Deposited</dt><dd>{isoDate(scheme.created_at)}</dd></div>
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
            >
                Terms
            </SectionHeading>
            {record.terms.length > 0 && (
                <p className="note" data-linear-in-energies={String(record.linear_in_energies ?? null)}>
                    {linearSummary(record.linear_in_energies)}
                </p>
            )}
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
            <h3 className="t-heading-2">Term {term.position + 1}</h3>
            <dl className="kv-list">
                <div><dt>Operation</dt><dd>{operationLabel(term.operation)}</dd></div>
                <div><dt>Energy component</dt><dd>{componentLabel(term.energy_component)}</dd></div>
                <div><dt>Formula</dt><dd>{formula ?? "none"}</dd></div>
                <div><dt>α (exponent)</dt><dd>{term.exponent === null || term.exponent === undefined ? "none" : (deposited(term.exponent)?.value ?? term.exponent)}</dd></div>
                <div><dt>Weights</dt><dd>{linearityText(term)}</dd></div>
                {expression && <div className="kv-list--wide"><dt>As a sum</dt><dd><code className="data">{expression}</code></dd></div>}
            </dl>
            <div className="table-scroll">
                <table className="data-table" aria-label={`Inputs of term ${term.position + 1}`}>
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

function ZpeScaleFactor({ value }: { value: number | null | undefined }) {
    const shown = deposited(value)
    if (!shown) return <>not stated by the recipe</>
    return <Stored full={shown.full}>{shown.value}</Stored>
}
