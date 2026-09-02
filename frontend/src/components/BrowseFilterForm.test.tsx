import { useState } from "react"
import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { BROWSE_KINDS, EMPTY_BROWSE_FILTERS } from "../api/browseApi"
import type { BrowseFilters, BrowseKind } from "../api/browseApi"
import { BrowseFilterForm } from "./BrowseFilterForm"

/**
 * `BrowseFilterForm`'s six vocabulary-backed PROVENANCE fields (Method,
 * Basis, Software, Software version, Workflow tool, Workflow tool version
 * -- `ProvenanceFields`, mounted for EVERY `kind`) and the transition-state-
 * only `EvidenceFields` (Status + seven `has_*` flags). Fixtures mirror the
 * MEASURED live archive vocabulary (see the design brief), not invented
 * values: Gaussian genuinely has two version strings that look like
 * duplicates and are not (`"16"` vs `"Gaussian 16, Revision C.02"`, a
 * parsed output banner deposited into the version column -- issue #305),
 * Arkane genuinely appears in both the software and workflow-tool lists,
 * and ORCA/Molpro/Arkane genuinely have zero recorded versions
 * (`software_release.version` is NULL for all three).
 */

const METHODS = [
    { value: "CCSD(T)-F12", count: 12 },
    { value: "MRCI+Davidson", count: 8 },
    { value: "b3lyp", count: 5 },
    { value: "wb97xd", count: 3 },
]
const BASIS_SETS = [
    { value: "def2tzvp", count: 9 },
    { value: "aug-cc-pV(T+d)Z", count: 4 },
    { value: "cc-pVTZ-F12", count: 2 },
]
const SOFTWARE = [
    { value: "Arkane", count: 1 },
    { value: "Gaussian", count: 1 },
    { value: "Molpro", count: 1 },
    { value: "ORCA", count: 1 },
]
const WORKFLOW_TOOLS = [
    { value: "ARC", count: 1 },
    { value: "Arkane", count: 1 },
]
const GAUSSIAN_VERSIONS = [
    { value: "09", count: 4 },
    { value: "16", count: 11 },
    { value: "Gaussian 16, Revision C.02", count: 2 },
]

type MetaOptions = {
    methodsStatus?: number
    captureSoftwareVersionUrls?: URL[]
    captureWorkflowToolVersionUrls?: URL[]
    captureSoftwareUrls?: URL[]
    captureWorkflowToolUrls?: URL[]
    versionsByParent?: Record<string, { value: string; count: number }[]>
}

function metaHandlers(options: MetaOptions = {}) {
    const versionsByParent = options.versionsByParent ?? { Gaussian: GAUSSIAN_VERSIONS }
    return [
        http.get("/api/v1/scientific/meta/methods", () => {
            if (options.methodsStatus) return HttpResponse.json({ detail: "archive unavailable" }, { status: options.methodsStatus })
            return HttpResponse.json({ results: METHODS })
        }),
        http.get("/api/v1/scientific/meta/basis-sets", () => HttpResponse.json({ results: BASIS_SETS })),
        http.get("/api/v1/scientific/meta/software", ({ request }) => {
            options.captureSoftwareUrls?.push(new URL(request.url))
            return HttpResponse.json({ results: SOFTWARE })
        }),
        http.get("/api/v1/scientific/meta/workflow-tools", ({ request }) => {
            options.captureWorkflowToolUrls?.push(new URL(request.url))
            return HttpResponse.json({ results: WORKFLOW_TOOLS })
        }),
        http.get("/api/v1/scientific/meta/software-versions", ({ request }) => {
            const url = new URL(request.url)
            options.captureSoftwareVersionUrls?.push(url)
            const software = url.searchParams.get("software")
            if (!software) return HttpResponse.json({ code: "missing_version_parent", detail: "software is required" }, { status: 422 })
            return HttpResponse.json({ results: versionsByParent[software] ?? [] })
        }),
        http.get("/api/v1/scientific/meta/workflow-tool-versions", ({ request }) => {
            const url = new URL(request.url)
            options.captureWorkflowToolVersionUrls?.push(url)
            const workflowTool = url.searchParams.get("workflow_tool")
            if (!workflowTool) return HttpResponse.json({ code: "missing_version_parent", detail: "workflow_tool is required" }, { status: 422 })
            return HttpResponse.json({ results: versionsByParent[workflowTool] ?? [] })
        }),
    ]
}

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

/**
 * A controlled wrapper -- `BrowseFilterForm` is a pure controlled
 * component, so a real state owner is needed for a selection to actually
 * stick and for a version field to actually clear. Also surfaces the raw
 * `softwareVersion`/`workflowToolVersion` STATE via a `data-testid`
 * readout: a native `<select>` silently falls back to displaying no
 * selection when its `value` prop no longer matches any of its current
 * `<option>`s, which would make a DOM-only assertion on the select's
 * value pass even if the underlying filter value was never actually
 * cleared -- exactly the bug this form must not have (a stale
 * `software_version` surviving a `software` change and reaching the
 * outgoing browse query, see the design brief's walkthrough). Reading
 * the state directly, not just what the browser happens to render,
 * is what makes that mutation actually fail a test.
 *
 * `kind` defaults to "transition_state" to keep every EXISTING test in
 * this file (written against the six provenance fields plus the
 * transition-state-only Status/`has_*` fields) unchanged; tests that care
 * about a different kind pass it explicitly.
 */
function Wrapper({ kind = "transition_state" }: { kind?: BrowseKind }) {
    const [filters, setFilters] = useState<BrowseFilters>(EMPTY_BROWSE_FILTERS)
    return <>
        <BrowseFilterForm filters={filters} kind={kind} onChange={(patch) => setFilters((current) => ({ ...current, ...patch }))} />
        <output data-software-version={filters.softwareVersion} data-testid="debug-filters" data-workflow-tool-version={filters.workflowToolVersion} />
    </>
}

function renderForm(kind?: BrowseKind) {
    render(<Wrapper kind={kind} />)
}

describe("no vocabulary option renders a count", () => {
    // The endpoints DO return a `count`, and it means different things per
    // endpoint -- structurally always 1 for software/workflow-tool (both
    // carry `UniqueConstraint("name")`), a real tally for methods/basis.
    // A number beside an option reads as a record count either way, so a
    // figure whose meaning changes between two adjacent dropdowns is worse
    // than no figure. This asserts across EVERY vocabulary select, not one:
    // a per-field flag is exactly how the inconsistency arose.
    it("renders bare values in every select, never a trailing (n)", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))
        for (const label of ["Method", "Basis", "Software", "Workflow tool"]) {
            const texts = [...screen.getByLabelText(label).querySelectorAll("option")].map((o) => o.textContent ?? "")
            expect(texts.length).toBeGreaterThan(1)
            for (const text of texts) expect(text).not.toMatch(/\s\(\d+\)$/)
        }
    })
})

describe("each vocabulary select is populated from ITS OWN endpoint", () => {
    it("Method reads /meta/methods, not any other endpoint's values", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))
        const options = [...screen.getByLabelText("Method").querySelectorAll("option")].map((o) => o.textContent)
        expect(options).toEqual(["Any", "CCSD(T)-F12", "MRCI+Davidson", "b3lyp", "wb97xd"])
    })

    it("Basis reads /meta/basis-sets, not Method's values", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Basis").querySelectorAll("option")).toHaveLength(BASIS_SETS.length + 1))
        const options = [...screen.getByLabelText("Basis").querySelectorAll("option")].map((o) => o.textContent)
        expect(options).toEqual(["Any", "def2tzvp", "aug-cc-pV(T+d)Z", "cc-pVTZ-F12"])
    })

    it("Software reads /meta/software, not /meta/workflow-tools -- a select showing the right SHAPE of label while reading the wrong vocabulary is the exact failure this guards against", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
        const options = [...screen.getByLabelText("Software").querySelectorAll("option")].map((o) => o.textContent)
        expect(options).toEqual(["Any", "Arkane", "Gaussian", "Molpro", "ORCA"])
    })

    it("Workflow tool reads /meta/workflow-tools, not /meta/software", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Workflow tool").querySelectorAll("option")).toHaveLength(WORKFLOW_TOOLS.length + 1))
        const options = [...screen.getByLabelText("Workflow tool").querySelectorAll("option")].map((o) => o.textContent)
        expect(options).toEqual(["Any", "ARC", "Arkane"])
    })

    it("MUTATION CHECK: 'Arkane' appears in BOTH the Software and Workflow tool selects -- the archive genuinely lists it in both", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
        await waitFor(() => expect(screen.getByLabelText("Workflow tool").querySelectorAll("option")).toHaveLength(WORKFLOW_TOOLS.length + 1))
        const softwareValues = [...screen.getByLabelText("Software").querySelectorAll("option")].map((o) => (o as HTMLOptionElement).value)
        const workflowValues = [...screen.getByLabelText("Workflow tool").querySelectorAll("option")].map((o) => (o as HTMLOptionElement).value)
        expect(softwareValues).toContain("Arkane")
        expect(workflowValues).toContain("Arkane")
    })
})

describe("count is never rendered for Software or Workflow tool (structurally always 1 -- UniqueConstraint(name))", () => {
    it("Software options carry no '(1)' suffix", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
        for (const option of screen.getByLabelText("Software").querySelectorAll("option")) expect(option.textContent).not.toMatch(/\(\d+\)/)
    })

    it("Workflow tool options carry no '(1)' suffix", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Workflow tool").querySelectorAll("option")).toHaveLength(WORKFLOW_TOOLS.length + 1))
        for (const option of screen.getByLabelText("Workflow tool").querySelectorAll("option")) expect(option.textContent).not.toMatch(/\(\d+\)/)
    })
})

describe("Gaussian's two version strings are DISTINCT options, never collapsed", () => {
    it("'16' and 'Gaussian 16, Revision C.02' both render as separate, unmodified options", async () => {
        const user = userEvent.setup()
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
        await user.selectOptions(screen.getByLabelText("Software"), "Gaussian")
        await waitFor(() => expect(screen.getByLabelText("Software version").querySelectorAll("option")).toHaveLength(GAUSSIAN_VERSIONS.length + 1))
        const options = [...screen.getByLabelText("Software version").querySelectorAll("option")].map((o) => (o as HTMLOptionElement).value)
        expect(options).toContain("16")
        expect(options).toContain("Gaussian 16, Revision C.02")
        expect(new Set(options).size).toBe(options.length) // no accidental dedupe collision
    })
})

describe("changing the parent select refetches the version vocabulary AND clears the stale value", () => {
    it("Gaussian -> pick '16' -> switch to ORCA: version select clears, and the refetch carries software=ORCA", async () => {
        const user = userEvent.setup()
        const softwareVersionUrls: URL[] = []
        // ORCA ALSO happens to list a "16" version here -- deliberately, so
        // that once ORCA's own vocabulary finishes loading, "16" is once
        // again a valid <option>. If the stale value were never actually
        // cleared from state (only hidden while the select was briefly
        // disabled during the refetch), it would silently reappear here as
        // ORCA's own selection the moment loading finishes -- a DOM check
        // taken only immediately after the switch would miss that, because
        // the select shows no options at all while `loading` regardless of
        // whether the underlying value was cleared.
        server.use(...metaHandlers({ captureSoftwareVersionUrls: softwareVersionUrls, versionsByParent: { Gaussian: GAUSSIAN_VERSIONS, ORCA: [{ value: "16", count: 7 }] } }))
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))

        await user.selectOptions(screen.getByLabelText("Software"), "Gaussian")
        await waitFor(() => expect(screen.getByLabelText("Software version")).toBeEnabled())
        await user.selectOptions(screen.getByLabelText("Software version"), "16")
        expect(screen.getByLabelText("Software version")).toHaveValue("16")
        expect(screen.getByTestId("debug-filters")).toHaveAttribute("data-software-version", "16")

        await user.selectOptions(screen.getByLabelText("Software"), "ORCA")

        // Cleared immediately in STATE (not just visually hidden by the
        // select being disabled while loading).
        expect(screen.getByTestId("debug-filters")).toHaveAttribute("data-software-version", "")
        await waitFor(() => expect(softwareVersionUrls.at(-1)?.searchParams.get("software")).toBe("ORCA"))
        expect(softwareVersionUrls.some((url) => url.searchParams.get("software") === "Gaussian")).toBe(true)

        // ORCA's OWN vocabulary finishes loading (it also has a "16") --
        // the field must stay on "Any", not silently resurrect the stale
        // Gaussian selection just because the string happens to match.
        await waitFor(() => expect(screen.getByLabelText("Software version")).toBeEnabled())
        expect(screen.getByLabelText("Software version")).toHaveValue("")
        expect(screen.getByTestId("debug-filters")).toHaveAttribute("data-software-version", "")
    })

    it("mirrors for Workflow tool / Workflow tool version", async () => {
        const user = userEvent.setup()
        const workflowToolVersionUrls: URL[] = []
        // Arkane ALSO lists "1.2.0" -- same reasoning as the Software case above.
        server.use(...metaHandlers({ captureWorkflowToolVersionUrls: workflowToolVersionUrls, versionsByParent: { ARC: [{ value: "1.2.0", count: 3 }], Arkane: [{ value: "1.2.0", count: 1 }] } }))
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Workflow tool").querySelectorAll("option")).toHaveLength(WORKFLOW_TOOLS.length + 1))

        await user.selectOptions(screen.getByLabelText("Workflow tool"), "ARC")
        await waitFor(() => expect(screen.getByLabelText("Workflow tool version")).toBeEnabled())
        await user.selectOptions(screen.getByLabelText("Workflow tool version"), "1.2.0")
        expect(screen.getByLabelText("Workflow tool version")).toHaveValue("1.2.0")
        expect(screen.getByTestId("debug-filters")).toHaveAttribute("data-workflow-tool-version", "1.2.0")

        await user.selectOptions(screen.getByLabelText("Workflow tool"), "Arkane")
        expect(screen.getByTestId("debug-filters")).toHaveAttribute("data-workflow-tool-version", "")
        await waitFor(() => expect(workflowToolVersionUrls.at(-1)?.searchParams.get("workflow_tool")).toBe("Arkane"))

        await waitFor(() => expect(screen.getByLabelText("Workflow tool version")).toBeEnabled())
        expect(screen.getByLabelText("Workflow tool version")).toHaveValue("")
        expect(screen.getByTestId("debug-filters")).toHaveAttribute("data-workflow-tool-version", "")
    })
})

describe("the version select's three states render DISTINCT copy", () => {
    it("no parent chosen: disabled, with 'choose a parent first' copy -- not the loading or empty-versions copy", async () => {
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
        expect(screen.getByLabelText("Software version")).toBeDisabled()
        expect(screen.getByText("Choose a software first.")).toBeVisible()
        expect(screen.queryByText("Loading versions…")).not.toBeInTheDocument()
        expect(screen.queryByText(/No versions recorded/)).not.toBeInTheDocument()
    })

    it("loading: disabled, with 'Loading versions…' copy, distinct from the no-parent and empty-versions copy", async () => {
        const user = userEvent.setup()
        let resolveVersions: ((response: Response) => void) | undefined
        server.use(
            // Registered FIRST so it wins over `metaHandlers()`'s own
            // software-versions handler below -- msw matches in
            // registration order, and the base handler would otherwise
            // resolve immediately and skip straight past "loading".
            http.get("/api/v1/scientific/meta/software-versions", () => new Promise((resolve) => {
                resolveVersions = resolve
            })),
            ...metaHandlers(),
        )
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
        await user.selectOptions(screen.getByLabelText("Software"), "Gaussian")

        await waitFor(() => expect(screen.getByText("Loading versions…")).toBeVisible())
        expect(screen.getByLabelText("Software version")).toBeDisabled()
        expect(screen.queryByText("Choose a software first.")).not.toBeInTheDocument()
        expect(screen.queryByText(/No versions recorded/)).not.toBeInTheDocument()

        resolveVersions?.(HttpResponse.json({ results: GAUSSIAN_VERSIONS }))
        await waitFor(() => expect(screen.getByLabelText("Software version")).toBeEnabled())
    })

    it("parent chosen but no versions recorded (ORCA): distinct 'no versions recorded for ORCA' copy, select enabled with only 'Any'", async () => {
        const user = userEvent.setup()
        server.use(...metaHandlers({ versionsByParent: { ORCA: [] } }))
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
        await user.selectOptions(screen.getByLabelText("Software"), "ORCA")
        expect(await screen.findByText("No versions recorded for ORCA.")).toBeVisible()
        expect(screen.getByLabelText("Software version")).toBeEnabled()
        expect(screen.getByLabelText("Software version").querySelectorAll("option")).toHaveLength(1) // "Any" only
        expect(screen.queryByText("Choose a software first.")).not.toBeInTheDocument()
        expect(screen.queryByText("Loading versions…")).not.toBeInTheDocument()
    })
})

describe("'Any' clears a filter value with no lingering selection", () => {
    it("Method: pick b3lyp, then Any -- the field value goes back to empty", async () => {
        const user = userEvent.setup()
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))
        await user.selectOptions(screen.getByLabelText("Method"), "b3lyp")
        expect(screen.getByLabelText("Method")).toHaveValue("b3lyp")
        await user.selectOptions(screen.getByLabelText("Method"), "")
        expect(screen.getByLabelText("Method")).toHaveValue("")
    })

    it("Software version: pick '16', then Any -- clears without needing a parent change", async () => {
        const user = userEvent.setup()
        server.use(...metaHandlers())
        renderForm()
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
        await user.selectOptions(screen.getByLabelText("Software"), "Gaussian")
        await waitFor(() => expect(screen.getByLabelText("Software version")).toBeEnabled())
        await user.selectOptions(screen.getByLabelText("Software version"), "16")
        expect(screen.getByLabelText("Software version")).toHaveValue("16")
        await user.selectOptions(screen.getByLabelText("Software version"), "")
        expect(screen.getByLabelText("Software version")).toHaveValue("")
    })
})

describe("a failed vocabulary fetch degrades ONE select without breaking the others", () => {
    it("Method fetch fails (500): Method shows 'could not load' copy and stays unselectable-but-present, while Basis/Software still populate normally", async () => {
        server.use(...metaHandlers({ methodsStatus: 500 }))
        renderForm()
        await waitFor(() => expect(screen.getByText("Could not load method list.")).toBeVisible())
        expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(1) // "Any" only, never crashes into zero options
        await waitFor(() => expect(screen.getByLabelText("Basis").querySelectorAll("option")).toHaveLength(BASIS_SETS.length + 1))
        await waitFor(() => expect(screen.getByLabelText("Software").querySelectorAll("option")).toHaveLength(SOFTWARE.length + 1))
    })
})

const PROVENANCE_LABELS = ["Method", "Basis", "Software", "Software version", "Workflow tool", "Workflow tool version"]
const EVIDENCE_ONLY_LABELS = [
    "Status", "Has optimization", "Has frequency", "Has single point", "Has IRC",
    "Has path search", "Has geometry validation", "Has SCF stability",
]

// The six provenance selects used to live inside a section mounted ONLY
// for `kind="transition_state"` -- `/species/browse` has genuinely
// accepted all six all along (see the design brief), so hiding them on
// "species"/"vdw" hid a real capability. This asserts PER KIND, not once,
// because a bug here is exactly the kind that a single-kind assertion
// (as `describe`s above all render with kind="transition_state") cannot
// see: rendering correctly for TS while staying broken for the other two
// would pass a suite that only ever mounts with TS.
describe("the six provenance selects render on EVERY browse kind, not just transition state", () => {
    for (const kind of BROWSE_KINDS) {
        it(`kind="${kind}": all six provenance selects are present`, async () => {
            server.use(...metaHandlers())
            renderForm(kind)
            await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))
            for (const label of PROVENANCE_LABELS) expect(screen.getByLabelText(label)).toBeInTheDocument()
        })
    }
})

// The inverse claim: Status and the seven `has_*` evidence flags are
// TRANSITION-STATE ONLY -- `/species/browse` accepts none of them, so if
// they rendered for "species"/"vdw" a filled-in value would be silently
// dropped on the way to the request rather than doing anything. Asserted
// as an ABSENCE on the two kinds that must not have them, not just a
// presence check on transition_state, per the design brief: a component
// that renders both sections unconditionally would still pass any test
// that only checks transition_state.
describe("Status and the has_* evidence fields render ONLY on transition state", () => {
    it('kind="species": none of the evidence-only fields are present', async () => {
        server.use(...metaHandlers())
        renderForm("species")
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))
        for (const label of EVIDENCE_ONLY_LABELS) expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    })

    it('kind="vdw": none of the evidence-only fields are present', async () => {
        server.use(...metaHandlers())
        renderForm("vdw")
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))
        for (const label of EVIDENCE_ONLY_LABELS) expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    })

    it('kind="transition_state": every evidence-only field IS present', async () => {
        server.use(...metaHandlers())
        renderForm("transition_state")
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))
        for (const label of EVIDENCE_ONLY_LABELS) expect(screen.getByLabelText(label)).toBeInTheDocument()
    })
})

// `record_kind` scoping decision (see the design brief): Software and
// Workflow tool narrow their /meta/* fetch to the calculation-owner scope
// that matches the browse `kind` -- "species" for BOTH "species" and
// "vdw" (a van der Waals complex is a species_entry row, so it has no
// separate CalculationRecordKind value), "transition_state" for
// "transition_state". Method and Basis carry NO such parameter, since
// `/meta/methods`/`/meta/basis-sets` do not accept one.
describe("Software/Workflow tool scope their vocabulary fetch by record_kind; Method/Basis do not", () => {
    it('kind="species" sends record_kind=species on /meta/software and /meta/workflow-tools', async () => {
        const softwareUrls: URL[] = []
        const workflowToolUrls: URL[] = []
        server.use(...metaHandlers({ captureSoftwareUrls: softwareUrls, captureWorkflowToolUrls: workflowToolUrls }))
        renderForm("species")
        await waitFor(() => expect(softwareUrls).toHaveLength(1))
        await waitFor(() => expect(workflowToolUrls).toHaveLength(1))
        expect(softwareUrls[0]?.searchParams.get("record_kind")).toBe("species")
        expect(workflowToolUrls[0]?.searchParams.get("record_kind")).toBe("species")
    })

    it('kind="vdw" ALSO sends record_kind=species -- a vdW complex has no CalculationRecordKind of its own', async () => {
        const softwareUrls: URL[] = []
        server.use(...metaHandlers({ captureSoftwareUrls: softwareUrls }))
        renderForm("vdw")
        await waitFor(() => expect(softwareUrls).toHaveLength(1))
        expect(softwareUrls[0]?.searchParams.get("record_kind")).toBe("species")
    })

    it('kind="transition_state" sends record_kind=transition_state', async () => {
        const softwareUrls: URL[] = []
        const workflowToolUrls: URL[] = []
        server.use(...metaHandlers({ captureSoftwareUrls: softwareUrls, captureWorkflowToolUrls: workflowToolUrls }))
        renderForm("transition_state")
        await waitFor(() => expect(softwareUrls).toHaveLength(1))
        await waitFor(() => expect(workflowToolUrls).toHaveLength(1))
        expect(softwareUrls[0]?.searchParams.get("record_kind")).toBe("transition_state")
        expect(workflowToolUrls[0]?.searchParams.get("record_kind")).toBe("transition_state")
    })

    it("Method and Basis carry no record_kind param on any kind -- /meta/methods and /meta/basis-sets accept none", async () => {
        let methodsUrl: URL | undefined
        let basisUrl: URL | undefined
        server.use(
            http.get("/api/v1/scientific/meta/methods", ({ request }) => {
                methodsUrl = new URL(request.url)
                return HttpResponse.json({ results: METHODS })
            }),
            http.get("/api/v1/scientific/meta/basis-sets", ({ request }) => {
                basisUrl = new URL(request.url)
                return HttpResponse.json({ results: BASIS_SETS })
            }),
            ...metaHandlers(),
        )
        renderForm("species")
        await waitFor(() => expect(methodsUrl).toBeDefined())
        await waitFor(() => expect(basisUrl).toBeDefined())
        expect(methodsUrl?.searchParams.has("record_kind")).toBe(false)
        expect(basisUrl?.searchParams.has("record_kind")).toBe(false)
    })

    it("switching from species to transition_state refetches Software with the new record_kind", async () => {
        const user = userEvent.setup()
        const softwareUrls: URL[] = []
        server.use(...metaHandlers({ captureSoftwareUrls: softwareUrls }))
        function SwitchableWrapper() {
            const [kind, setKind] = useState<BrowseKind>("species")
            const [filters, setFilters] = useState<BrowseFilters>(EMPTY_BROWSE_FILTERS)
            return <>
                <button onClick={() => setKind("transition_state")} type="button">go TS</button>
                <BrowseFilterForm filters={filters} kind={kind} onChange={(patch) => setFilters((current) => ({ ...current, ...patch }))} />
            </>
        }
        render(<SwitchableWrapper />)
        await waitFor(() => expect(softwareUrls).toHaveLength(1))
        expect(softwareUrls[0]?.searchParams.get("record_kind")).toBe("species")

        await user.click(screen.getByRole("button", { name: "go TS" }))
        await waitFor(() => expect(softwareUrls).toHaveLength(2))
        expect(softwareUrls[1]?.searchParams.get("record_kind")).toBe("transition_state")
    })
})

// ---------------------------------------------------------------------------
// Structure filter fields (query_smiles / query_smarts / mode /
// similarity_threshold), folded into the composition section per the
// owner's correction: "just make the struct and smiles search part of
// the browser-filters class". Species/vdW only, mirroring the rest of
// CompositionFields.
// ---------------------------------------------------------------------------

describe("structure search fields render on species/vdw, not on transition_state", () => {
    it('kind="species": Structure field, mode select and SMARTS checkbox are present', async () => {
        server.use(...metaHandlers())
        renderForm("species")
        expect(await screen.findByLabelText("Structure (SMILES)")).toBeInTheDocument()
        expect(screen.getByLabelText("Structure search mode")).toBeInTheDocument()
        expect(screen.getByLabelText("Treat structure as SMARTS")).toBeInTheDocument()
    })

    it('kind="vdw": also present -- the same composition section as species', async () => {
        server.use(...metaHandlers())
        renderForm("vdw")
        expect(await screen.findByLabelText("Structure (SMILES)")).toBeInTheDocument()
    })

    it('kind="transition_state": absent -- /transition-states/browse accepts no structure filter', async () => {
        server.use(...metaHandlers())
        renderForm("transition_state")
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))
        expect(screen.queryByLabelText("Structure (SMILES)")).not.toBeInTheDocument()
        expect(screen.queryByLabelText("Structure search mode")).not.toBeInTheDocument()
        // The rest of CompositionFields (Formula, Elements, ...) is gated on
        // the SAME `kind !== "transition_state"` conditional as the
        // structure controls -- a transition state has no stored species
        // SMILES to match against, so neither section should render.
        expect(screen.queryByLabelText("Formula")).not.toBeInTheDocument()
    })
})

// ---------------------------------------------------------------------------
// Field order: structure and formula lead the grid, per the owner's
// correction ("why isn't smiles and formula the first in the filter?").
// CompositionFields (Formula, Structure, Elements, Element match, Min/Max
// heavy atoms, Electronic state) now renders FIRST, ahead of Charge,
// Multiplicity, review status, and the two include-* checkboxes, which in
// turn precede the six provenance selects. A membership check (every field
// is present) cannot catch a reordering -- only asserting the actual
// sequence can.
// ---------------------------------------------------------------------------

describe("structure and formula controls lead the filter grid", () => {
    it('kind="species": the structure controls lead, then Formula, then Charge, review status and the provenance selects', async () => {
        server.use(...metaHandlers())
        const { container } = render(<Wrapper kind="species" />)
        await waitFor(() => expect(screen.getByLabelText("Method").querySelectorAll("option")).toHaveLength(METHODS.length + 1))

        const labels = [...container.querySelectorAll("label")].map((el) => el.textContent)
        expect(labels).toEqual([
            "Structure (SMILES)",
            "Treat structure as SMARTS",
            "Structure search mode",
            "Formula",
            "Elements",
            "Element match",
            "Min heavy atoms",
            "Max heavy atoms",
            "Electronic state",
            "Charge",
            "Multiplicity",
            "Minimum review status",
            "Include rejected",
            "Include deprecated",
            "Method",
            "Basis",
            "Software",
            "Software version",
            "Workflow tool",
            "Workflow tool version",
        ])
    })
})

describe("structure search mode gates its dependent controls", () => {
    it("the SMARTS checkbox is present only under mode=substructure; similarity threshold only under mode=similarity", async () => {
        const user = userEvent.setup()
        server.use(...metaHandlers())
        renderForm("species")
        await screen.findByLabelText("Structure (SMILES)")

        // Default mode is substructure: SMARTS toggle present, threshold absent.
        expect(screen.getByLabelText("Treat structure as SMARTS")).toBeInTheDocument()
        expect(screen.queryByLabelText("Similarity threshold")).not.toBeInTheDocument()

        await user.selectOptions(screen.getByLabelText("Structure search mode"), "similarity")
        expect(screen.queryByLabelText("Treat structure as SMARTS")).not.toBeInTheDocument()
        expect(screen.getByLabelText("Similarity threshold")).toBeInTheDocument()

        await user.selectOptions(screen.getByLabelText("Structure search mode"), "exact")
        expect(screen.queryByLabelText("Treat structure as SMARTS")).not.toBeInTheDocument()
        expect(screen.queryByLabelText("Similarity threshold")).not.toBeInTheDocument()
    })

    it("checking SMARTS then switching away from substructure clears the toggle -- never carries a SMARTS query into a mode that rejects it", async () => {
        const user = userEvent.setup()
        server.use(...metaHandlers())
        function Wrapper() {
            const [filters, setFilters] = useState<BrowseFilters>(EMPTY_BROWSE_FILTERS)
            return <>
                <BrowseFilterForm filters={filters} kind="species" onChange={(patch) => setFilters((current) => ({ ...current, ...patch }))} />
                <output data-query-is-smarts={String(filters.queryIsSmarts)} data-testid="debug-structure" />
            </>
        }
        render(<Wrapper />)
        await screen.findByLabelText("Structure (SMILES)")

        await user.click(screen.getByLabelText("Treat structure as SMARTS"))
        expect(screen.getByTestId("debug-structure")).toHaveAttribute("data-query-is-smarts", "true")

        await user.selectOptions(screen.getByLabelText("Structure search mode"), "similarity")
        expect(screen.getByTestId("debug-structure")).toHaveAttribute("data-query-is-smarts", "false")

        // Switching back to substructure shows an UNCHECKED toggle -- the
        // clear was real state, not just a hidden control remembering a
        // stale checked value.
        await user.selectOptions(screen.getByLabelText("Structure search mode"), "substructure")
        expect(screen.getByLabelText("Treat structure as SMARTS")).not.toBeChecked()
    })
})
