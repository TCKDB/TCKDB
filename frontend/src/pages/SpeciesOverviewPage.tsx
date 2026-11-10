import { useParams } from "react-router-dom"
import "../species-overview.css"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import { AlsoKnownAs } from "../components/AlsoKnownAs"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { Formula } from "../components/Formula"
import { PageShell } from "../components/PageShell"
import { SpeciesRelationshipMap } from "../components/RelationshipMap"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { SpeciesValues } from "../components/SpeciesValues"
import { SpeciesVersions } from "../components/SpeciesVersions"
import { moleculeLede } from "../domain/entryVersions"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

export default function SpeciesOverviewPage() {
    const { speciesRef = "" } = useParams<{ speciesRef: string }>()
    const state = useSpeciesOverview(speciesRef)
    useDocumentTitle("Molecule", state.status === "ready" && state.speciesRef === speciesRef ? (state.species.formula ?? state.species.canonical_smiles) : null)

    if (state.speciesRef !== speciesRef || state.status === "loading") {
        return <State title="Loading molecule…" busy />
    }
    if (state.status === "missing") return <State title="Molecule not found" ref={speciesRef} />
    if (state.status === "rate-limited") {
        return (
            <State
                title="Archive is busy"
                ref={speciesRef}
                alert
                message={`The archive is receiving too many requests right now. Wait ${formatWaitSeconds(state.retryAfterSeconds)} and reload the page.`}
            />
        )
    }
    if (state.status !== "ready") {
        const title = state.status === "malformed" ? "Molecule data could not be read" : "Molecule unavailable"
        return <State title={title} alert />
    }
    return <SpeciesDocument species={state.species} />
}

function State({ title, ref, busy, alert, message }: {
    title: string
    ref?: string
    busy?: boolean
    alert?: boolean
    message?: string
}) {
    const resolvedMessage = message ?? (busy
        ? "Retrieving the molecule and its versions."
        : alert
            ? "The archive response could not be read. Try again later."
            : "No molecule with this stable reference is available in this archive projection.")
    return (
        <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            {ref && <code>{ref}</code>}
            <p>{resolvedMessage}</p>
        </section>
    )
}

function SpeciesDocument({ species }: { species: SpeciesOverview }) {
    // A formula is typeset with subscripts; the SMILES fallback is not formula text, so it is never run through `Formula`.
    const name = species.formula ? <Formula value={species.formula} /> : species.canonical_smiles
    return (
        <section className="species-overview">
            <PageShell
                rail={<SpeciesRelationshipMap species={species} />}
                identity={<>
                    <Breadcrumbs crumbs={[
                        { label: "Molecules", to: BROWSE_KIND_PATHS.species },
                        { label: name },
                    ]} />
                    <RecordIdentityHeader
                        kicker="A molecule"
                        glyph="species"
                        lede={moleculeLede(species.entries.length)}
                        title={name}
                        identity={{
                            kind: "species_entry",
                            formula: species.formula ?? null,
                            canonicalSmiles: species.canonical_smiles,
                            inchiKey: species.inchi_key,
                            charge: species.charge,
                            multiplicity: species.multiplicity,
                        }}
                        ownRef={{ label: "Molecule ref", value: species.species_ref }}
                    />
                </>}
            >
                <AlsoKnownAs />
                <SpeciesVersions species={species} />
                <SpeciesValues species={species} />
            </PageShell>
        </section>
    )
}
