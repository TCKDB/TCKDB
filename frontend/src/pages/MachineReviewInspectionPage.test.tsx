import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useEffect } from "react"
import { MemoryRouter, useNavigate } from "react-router-dom"
import MachineReviewInspectionPage from "./MachineReviewInspectionPage"
import { AuthProvider } from "../components/AuthProvider"
import { CuratorTaskBuildResultSchema } from "../types/curatorTask"
import {
    knownRunStatus,
    MachineReviewRunResultSchema,
    reviewFailed,
} from "../types/machineReviewRun"
import { DEPOSITOR_SENTINEL } from "../test/depositorSentinel"
import * as fx from "../test/machineReviewFixtures"
import { bySummaryText } from "../test/disclosureQueries"

/**
 * The inspection page: who may open it, how a submission is chosen (a list,
 * never a typed id), what the header, records, findings and runs say, and
 * the two controls that act on a submission. The run and build controls keep
 * their outcome regions and their per-submission reset.
 */

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); server.events.removeAllListeners(); cleanup() })
afterAll(() => server.close())

const admin = { id: 1, username: "ada", email: "ada@example.com", full_name: "Ada Example", role: "admin", is_active: true }
const curator = { ...admin, id: 3, username: "cora", role: "curator" }
const plainUser = { ...admin, id: 2, username: "noah", role: "user" }

function meIs(who: Record<string, unknown>) {
    server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(who)))
}

const adminUsers = {
    items: [{ ...admin, affiliation: null, created_at: "2026-01-01T00:00:00Z" }],
    total: 1,
    skip: 0,
    limit: 200,
}

/** Routes the header reads, for any submission. Per-test handlers added later take precedence. */
function serveHeaderRoutes() {
    server.use(
        http.get("/api/v1/submissions/:id", ({ params }) =>
            HttpResponse.json(fx.submission({ id: Number(params.id), public_ref: `sub_${params.id}` })),
        ),
        http.get("/api/v1/submissions/:id/record-links", () =>
            HttpResponse.json([fx.recordLink(), fx.recordLink({ id: 2, record_id: 11 }), fx.recordLink({ id: 3, record_type: "calculation" })]),
        ),
        http.get("/api/v1/submissions/:id/audit-events", () => HttpResponse.json([fx.auditEvent()])),
        http.get("/api/v1/admin/users", () => HttpResponse.json(adminUsers)),
    )
}

let navigateTo: (to: string) => void = () => {}
function NavGrab() {
    const navigate = useNavigate()
    useEffect(() => {
        navigateTo = (to) => void navigate(to)
    }, [navigate])
    return null
}

/** Render the page as `who`, at `?submission=<id>` when an id is given. */
function renderPage(id?: string, who: Record<string, unknown> = admin, query = "") {
    meIs(who)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
        <AuthProvider>
            <MemoryRouter initialEntries={[id === undefined ? `/admin/machine-review-inspection${query}` : `/admin/machine-review-inspection?submission=${id}`]}>
                <NavGrab />
                <QueryClientProvider client={client}>
                    <MachineReviewInspectionPage />
                </QueryClientProvider>
            </MemoryRouter>
        </AuthProvider>,
    )
}

/** Open one submission as an admin; returns the `user` for further acts. */
async function inspect(id = "7") {
    serveHeaderRoutes()
    renderPage(id)
    return userEvent.setup()
}

/** Move to a different submission, as following a link would. */
async function reinspect(_user: ReturnType<typeof userEvent.setup>, id: string) {
    act(() => navigateTo(`/admin/machine-review-inspection?submission=${id}`))
}

const INSPECTION = "/api/v1/admin/submissions/:submissionId/machine-review-inspection"

function serveInspection(body: Record<string, unknown> = fx.inspection()) {
    server.use(http.get(INSPECTION, () => HttpResponse.json(body)))
}

/** The text of the page, for absence checks. */
function pageText(): string {
    return document.body.textContent ?? ""
}

/** The card of one record: the list item that holds the heading naming it. */
async function recordCard(name: string | RegExp): Promise<HTMLElement> {
    const pattern = typeof name === "string" ? new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`) : name
    const heading = await screen.findByRole("heading", { level: 3, name: pattern })
    const card = heading.closest("li")
    if (card === null) throw new Error("the record heading is not inside a list item")
    return card
}

describe("who can open the page", () => {
    it("a curator is refused and no inspection is requested", async () => {
        const requested: string[] = []
        server.events.on("request:start", ({ request }) => requested.push(new URL(request.url).pathname))
        renderPage("7", curator)
        expect(await screen.findByRole("alert")).toHaveTextContent("This page is for administrators.")
        expect(requested.filter((path) => /machine-review/.test(path))).toEqual([])
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
    })

    it("a plain user is refused and nothing is requested", async () => {
        const requested: string[] = []
        server.events.on("request:start", ({ request }) => requested.push(new URL(request.url).pathname))
        renderPage(undefined, plainUser)
        expect(await screen.findByRole("alert")).toHaveTextContent("This page is for administrators.")
        expect(requested.filter((path) => /machine-review|submissions/.test(path))).toEqual([])
    })

    it("a backend outage does not render as 'you are not an admin'", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.error()))
        render(
            <AuthProvider>
                <MemoryRouter>
                    <QueryClientProvider client={new QueryClient()}>
                        <MachineReviewInspectionPage />
                    </QueryClientProvider>
                </MemoryRouter>
            </AuthProvider>,
        )
        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/could not be reached/i)
        expect(alert).not.toHaveTextContent(/for administrators/i)
    })
})

describe("choosing a submission", () => {
    function serveOverview(pages: Record<string, unknown>[] | Record<string, unknown> = fx.overviewPage()) {
        const seen: URLSearchParams[] = []
        server.use(
            http.get("/api/v1/submissions/machine-review-overview", ({ request }) => {
                const url = new URL(request.url)
                seen.push(url.searchParams)
                const body = Array.isArray(pages) ? pages[Number(url.searchParams.get("offset") ?? 0) > 0 ? 1 : 0] : pages
                return HttpResponse.json(body)
            }),
        )
        return seen
    }

    it("reads the filter and page from the address", async () => {
        const seen = serveOverview()
        renderPage(undefined, admin, "?show=open&offset=50")
        await screen.findByRole("table", { name: "Submissions" })
        expect(seen[0].get("offset")).toBe("50")
        expect(seen[0].get("has_open_tasks")).toBe("true")
        expect(screen.getByLabelText("Show")).toHaveValue("open")
    })

    it("lists submissions to pick from and has no field to type an id into", async () => {
        serveOverview()
        renderPage()

        const table = await screen.findByRole("table", { name: "Submissions" })
        const cells = within(within(table).getAllByRole("row")[1]).getAllByRole("cell").map((c) => c.textContent?.trim())
        expect(cells[0]).toBe(fx.SUBMISSION_REF)
        expect(cells).toContain("computed reaction")
        expect(cells).toContain("approved")
        expect(cells).toContain("2026-09-01")
        expect(cells).toContain("ada")
        expect(screen.queryByRole("spinbutton")).toBeNull()
        expect(screen.getByRole("link", { name: new RegExp(`Inspect ${fx.SUBMISSION_REF}`) })).toHaveAttribute(
            "href",
            `/admin/machine-review-inspection?submission=${fx.SUBMISSION_ID}`,
        )
        expect(pageText()).toContain("2 open of 5")
        expect(pageText()).not.toContain(String(fx.SUBMISSION_ID))
        expect(pageText()).not.toContain("DEPOSITOR_TITLE")
    })

    it("shows an exact total and pages with Next", async () => {
        const rows = Array.from({ length: 50 }, (_, i) => fx.overviewRow({ id: 1000 + i, public_ref: `sub_page1_${i}` }))
        const seen = serveOverview([
            fx.overviewPage(rows, { total: 132 }),
            fx.overviewPage([fx.overviewRow({ id: 2000, public_ref: "sub_page2_0" })], { total: 132, skip: 50 }),
        ])
        renderPage()
        const user = userEvent.setup()

        expect(await screen.findByText("Showing 1–50 of 132 submissions")).toBeInTheDocument()
        await user.click(screen.getByRole("button", { name: "Next" }))
        expect(await screen.findByText("sub_page2_0")).toBeInTheDocument()
        expect(seen.at(-1)?.get("offset")).toBe("50")
        expect(screen.getByText("Showing 51–51 of 132 submissions")).toBeInTheDocument()
    })

    it("filters to reviewed submissions", async () => {
        const seen = serveOverview()
        renderPage()
        const user = userEvent.setup()
        await screen.findByRole("table", { name: "Submissions" })
        await user.selectOptions(screen.getByLabelText("Show"), "reviewed")
        await waitFor(() => expect(seen.at(-1)?.get("reviewed")).toBe("true"))
    })

    it("says so when there are no submissions", async () => {
        serveOverview(fx.overviewPage([]))
        renderPage()
        expect(await screen.findByText("No submissions match.")).toBeInTheDocument()
    })

    it("a link that does not name a submission says so and still offers the list", async () => {
        serveOverview()
        renderPage("abc")
        expect(await screen.findByText("That link does not name a submission.")).toBeInTheDocument()
        expect(await screen.findByRole("table", { name: "Submissions" })).toBeInTheDocument()
    })
})

describe("the submission header", () => {
    it("names the submission by its reference, status, kind, dates, uploader and linked records", async () => {
        serveInspection()
        await inspect("42")

        const header = await screen.findByRole("region", { name: "Submission" })
        await screen.findByText("sub_42")
        await screen.findByText("ada")
        await screen.findByText("Calculation 1")
        const text = header.textContent ?? ""
        expect(text).toContain("sub_42")
        expect(text).toContain("approved")
        expect(text).toContain("computed reaction")
        expect(text).toContain("2026-09-01 08:15 UTC")
        expect(text).toContain("ada")
        expect(text).toMatch(/Kinetics\s*2/)
        expect(text).toMatch(/Calculation\s*1/)
        expect(pageText()).not.toContain("not returned by endpoint")
    })

    it("lists the audit trail by kind, actor and status change, without free text", async () => {
        serveInspection()
        await inspect("42")
        const trail = await screen.findByRole("region", { name: "Audit trail" })
        await waitFor(() => expect(trail.textContent).toContain("2026-09-02"))
        expect(trail.textContent).toContain("2026-09-02 12:30 UTC")
        expect(trail.textContent).toContain("approved by a curator")
        expect(trail.textContent).toContain("administrator")
        expect(trail.textContent).toContain("pending → approved")
        for (const hidden of ["AUDIT_REASON", "AUDIT_SUMMARY", "AUDIT_DETAILS"]) {
            expect(document.body.outerHTML).not.toContain(hidden)
        }
    })

    it("shows depositor words only inside the field marked as deposited", async () => {
        const title = `${DEPOSITOR_SENTINEL}_TITLE`
        const summary = `${DEPOSITOR_SENTINEL}_SUMMARY`
        serveInspection()
        serveHeaderRoutes()
        server.use(http.get("/api/v1/submissions/:id", () => HttpResponse.json(fx.submission({ title, summary }))))
        renderPage("42")

        const marked = await screen.findByTestId("as-deposited")
        expect(marked).toHaveTextContent(title)
        expect(marked).toHaveTextContent("as deposited")
        expect(document.body.outerHTML.split(title).length - 1).toBe(1)
        expect(document.body.outerHTML).not.toContain(summary)
    })

    it("a missing submission says so in words, with no id in the message", async () => {
        server.use(
            http.get(INSPECTION, () => HttpResponse.json({ detail: "Submission 424242 not found" }, { status: 404 })),
            http.get("/api/v1/submissions/:id", () => HttpResponse.json({ detail: "Submission 424242 not found" }, { status: 404 })),
            http.get("/api/v1/submissions/:id/record-links", () => HttpResponse.json({ detail: "x" }, { status: 404 })),
            http.get("/api/v1/submissions/:id/audit-events", () => HttpResponse.json({ detail: "x" }, { status: 404 })),
            http.get("/api/v1/admin/users", () => HttpResponse.json(adminUsers)),
        )
        renderPage("424242")
        expect(await screen.findByText("No submission was found for this link.")).toBeInTheDocument()
        expect(pageText()).not.toContain("424242")
    })
})

describe("records and findings", () => {
    it("lists each record with its findings, priority and the reviewer's own words", async () => {
        serveInspection()
        await inspect("42")

        const kinetics = await recordCard(`Kinetics ${fx.KINETICS_REF}`)
        const text = kinetics.textContent ?? ""
        expect(text).toContain("screened: needs attention")
        expect(text).toContain("high")
        expect(text).toContain("2026-09-14")
        expect(text).toContain("REVIEWER_MESSAGE the rate constant is far above the collision limit")
        expect(text).toContain("kinetics.a")
        expect(text).toContain("REVIEWER_ACTION re-check the A factor units")
        expect(text).toContain("units")
        expect(text).toContain("critical")
        expect(within(kinetics).getAllByText(/the reviewer/i).length).toBeGreaterThan(0)
    })

    it("links a finding that has a curator task to that task", async () => {
        serveInspection()
        await inspect("42")
        const kinetics = await recordCard(`Kinetics ${fx.KINETICS_REF}`)
        const link = within(kinetics).getByRole("link", { name: /curator task/i })
        const href = link.getAttribute("href") ?? ""
        expect(href).toContain("/admin/curator-queue?")
        expect(href).toContain("task=41")
        expect(href).toContain("record_type=kinetics")
        expect(pageText()).not.toMatch(/task 41|#41/)
    })

    it("lists earlier reviews in a disclosure with their date and status", async () => {
        serveInspection()
        await inspect("42")
        const kinetics = await recordCard(`Kinetics ${fx.KINETICS_REF}`)
        const user = userEvent.setup()
        await user.click(within(kinetics).getByText(bySummaryText(/Earlier reviews \(1\)/)))
        expect(kinetics.textContent).toContain("2026-09-10")
        expect(kinetics.textContent).toContain("screened: warning")
        expect(kinetics.textContent).toContain("REVIEWER_MESSAGE earlier concern")
    })

    it("a calculation links to its own page", async () => {
        serveInspection()
        await inspect("42")
        const calc = await recordCard(`Calculation ${fx.CALC_REF}`)
        expect(within(calc).getByRole("link", { name: new RegExp(fx.CALC_REF) })).toHaveAttribute("href", `/calculations/${fx.CALC_REF}`)
    })

    it("an energy correction with no reference of its own links to the species entry it is shown on", async () => {
        serveInspection()
        await inspect("42")
        const heading = await recordCard(/^Energy correction/)
        expect(heading.textContent).toContain("(no reference of its own)")
        expect(within(heading).getByRole("link", { name: new RegExp(fx.SPECIES_ENTRY_REF) })).toHaveAttribute("href", `/species-entries/${fx.SPECIES_ENTRY_REF}`)
        expect(heading.textContent).not.toContain("—")
    })

    it("lists each finding that is about no record, with the reviewer's citation verbatim", async () => {
        serveInspection()
        await inspect("42")
        const section = await screen.findByRole("region", { name: "Findings about no record" })
        const text = section.textContent ?? ""
        expect(text).toContain("about the whole submission")
        expect(text).toContain("record type not recognised")
        expect(text).toContain("REVIEWER_MESSAGE the submission has no stated purpose")
        const cited = within(section).getByText("CITED_REF_VERBATIM")
        expect(cited.tagName).toBe("CODE")
        expect(text).toContain("as the reviewer cited it")
        expect(text).toContain("CITED_TYPE_VERBATIM")
    })

    it("lists the reviewer's runs, with the summary and the reason an unreadable one could not be read", async () => {
        serveInspection()
        await inspect("42")
        const section = await screen.findByRole("region", { name: "Reviewer runs" })
        const text = section.textContent ?? ""
        expect(text).toContain("REVIEWER_SUMMARY overall the kinetics look unreliable")
        expect(text).toContain("reviewer-model-1")
        expect(text).toContain("reviewer-provider")
        expect(text).toContain("2026-09-10")
        expect(text).toContain("The stored result could not be read")
        expect(text).toContain("RUN_PARSE_WARNING the reply was not JSON")
    })

    it("shows the mapping and parse warnings, and no source event ids", async () => {
        serveInspection()
        await inspect("42")
        expect(await screen.findByText("MAPPING_WARNING one finding named two records")).toBeInTheDocument()
        expect(screen.getByText("PARSE_WARNING one finding had no severity")).toBeInTheDocument()
        expect(pageText()).not.toMatch(/source audit event/i)
    })

    it("says nothing was mapped when no record has findings", async () => {
        serveInspection(fx.inspection({ record_summaries: [], unmapped_findings: [], unmapped_findings_count: 0, runs: [] }))
        await inspect("42")
        expect(await screen.findByText("No record received a mapped finding.")).toBeInTheDocument()
    })
})

describe("what the page leaves out", () => {
    it("has no raw JSON drawer and no scratchpad", async () => {
        serveInspection()
        await inspect("42")
        await recordCard(`Kinetics ${fx.KINETICS_REF}`)
        expect(screen.queryByRole("button", { name: /raw json/i })).toBeNull()
        expect(screen.queryByRole("checkbox")).toBeNull()
        expect(screen.queryByRole("textbox")).toBeNull()
        expect(pageText()).not.toMatch(/observations/i)
    })

    it("shows no field name, row id or ISO timestamp as text", async () => {
        serveInspection()
        await inspect("42")
        await recordCard(`Kinetics ${fx.KINETICS_REF}`)
        const text = pageText()
        for (const raw of ["record_public_ref", "highest_severity", "all_record_reviews_count", "submission_id", "reviewed_at"]) {
            expect(text).not.toContain(raw)
        }
        expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}T\d{2}:/)
        for (const id of [fx.KINETICS_RECORD_ID, fx.CALC_RECORD_ID, fx.AEC_RECORD_ID, 7001, 7000]) {
            expect(text).not.toContain(String(id))
        }
    })

    it("is one h1 inside the page and nothing is styled inline", async () => {
        serveInspection()
        await inspect("42")
        await recordCard(`Kinetics ${fx.KINETICS_REF}`)
        expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1)
        expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Machine review inspection")
        expect(document.body.querySelectorAll("[style]")).toHaveLength(0)
    })
})


/* ------------------------------------------------------------------ *
 * Building curator tasks
 *
 * Until this button existed nothing in the product could create a curator
 * task: the backend route is explicit-only and runs on no upload, so the
 * queue stayed empty however many submissions arrived, and the only way
 * to fill it was a hand-written curl.
 * ------------------------------------------------------------------ */

const BUILD =
    "/api/v1/admin/machine-review/curator-tasks/build-for-submission/:submissionId"

const BUILD_BUTTON = "Build curator tasks for this submission"

/**
 * Serve the inspection for ANY submission id.
 *
 * The record's public ref names the submission it came from (`spc_sub_9`),
 * which is what makes "wait until the page is showing submission 9" an
 * assertion rather than a hope. Every control on this page is present both
 * before and after a submission change, so without a value that differs
 * per submission there is nothing to wait for.
 */
function serveAnyInspection() {
    server.use(
        http.get(INSPECTION, ({ params }) =>
            HttpResponse.json(inspectionBody(params.submissionId, [record(`spc_sub_${params.submissionId}`)])),
        ),
    )
}

/** Wait until the page is showing this submission's findings. */
function showing(id: string): Promise<HTMLElement> {
    return screen.findByText(`spc_sub_${id}`)
}

/**
 * The page's four live regions, by accessible name.
 *
 * An unqualified `getByRole("status")` matches two elements and throws,
 * because "the status region" does not name one thing. The regions carry
 * `aria-label`s for the same reason a screen-reader user needs them: to
 * tell which button a result answered.
 */
const BUILD_ALERT = "curator task build problem"
const BUILD_STATUS = "curator task build result"
const RUN_ALERT = "machine review request problem"
const RUN_STATUS = "machine review run outcome"

function liveRegion(role: "status" | "alert", name: string): HTMLElement {
    return screen.getByRole(role, { name })
}

/**
 * Wait until a live region has something in it.
 *
 * Every one of them is permanently mounted and initially EMPTY (see the
 * component), so its mere presence proves nothing and `findByRole` resolves
 * instantly whether anything has run or not. Emptiness is the signal.
 */
async function regionFills(region: HTMLElement): Promise<HTMLElement> {
    await waitFor(() => expect(region).not.toBeEmptyDOMElement())
    return region
}

async function regionSays(region: HTMLElement, pattern: RegExp): Promise<HTMLElement> {
    await waitFor(() => expect(region).toHaveTextContent(pattern))
    return region
}

/** The curator-task tally region, once it has something in it. */
function tallyShown(): Promise<HTMLElement> {
    return regionFills(liveRegion("status", BUILD_STATUS))
}

/** The curator-task alert region's text, once it has some. */
function alertSays(pattern: RegExp): Promise<HTMLElement> {
    return regionSays(liveRegion("alert", BUILD_ALERT), pattern)
}

/** The machine-review run outcome region, once it has something in it. */
function runShown(): Promise<HTMLElement> {
    return regionFills(liveRegion("status", RUN_STATUS))
}

/** The machine-review run alert region's text, once it has some. */
function runAlertSays(pattern: RegExp): Promise<HTMLElement> {
    return regionSays(liveRegion("alert", RUN_ALERT), pattern)
}

type Tally = {
    created_count?: number
    reused_count?: number
    refreshed_count?: number
    skipped_info_count?: number
    skipped_unmapped_count?: number
    skipped_terminal_count?: number
    task_ids?: number[]
    warnings?: string[]
}

/** A build reply with every count present, overridable one at a time. */
function tally(over: Tally = {}): Record<string, unknown> {
    return {
        created_count: 0,
        reused_count: 0,
        refreshed_count: 0,
        skipped_info_count: 0,
        skipped_unmapped_count: 0,
        skipped_terminal_count: 0,
        task_ids: [],
        warnings: [],
        ...over,
    }
}

/**
 * Serve the build route. The returned array collects the submission ids it
 * was actually called with, which is how "built for the one on screen" is
 * asserted rather than assumed.
 */
function serveBuild(
    body: Record<string, unknown>,
    init?: { status: number },
): { calledFor: number[] } {
    const calledFor: number[] = []
    server.use(
        http.post(BUILD, ({ params }) => {
            calledFor.push(Number(params.submissionId))
            return HttpResponse.json(body, init)
        }),
    )
    return { calledFor }
}

/** A promise the test releases, so "while it is running" has no timer in it. */
function gate(): { held: Promise<void>; release: () => void } {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => {
        release = resolve
    })
    return { held, release }
}

describe("the schema behind the build tally", () => {
    it("drops task_ids, so no page can print one", () => {
        /**
         * The whole "an admin never sees a row id here" argument rests on
         * zod stripping undeclared keys. If a future zod, or a future
         * `.passthrough()`, changed that, the ids would silently become
         * available to render and nothing else would notice.
         */
        const parsed = CuratorTaskBuildResultSchema.parse(
            tally({ created_count: 2, task_ids: [4471, 4472] }),
        )
        expect("task_ids" in parsed).toBe(false)
        expect(parsed.created_count).toBe(2)
    })
})

describe("building curator tasks for a submission", () => {
    it("is not offered before a submission has been chosen", async () => {
        server.use(http.get("/api/v1/submissions/machine-review-overview", () => HttpResponse.json(fx.overviewPage())))
        renderPage()
        await screen.findByRole("table", { name: "Submissions" })
        expect(screen.queryByRole("button", { name: BUILD_BUTTON })).toBeNull()
    })

    it("builds for the submission on screen, not some other one", async () => {
        serveAnyInspection()
        const build = serveBuild(tally({ created_count: 1 }))
        const user = await inspect("9")
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))
        await tallyShown()

        // The id is in the URL path, so a page that built for a constant, or
        // for whatever was typed last, would still show a plausible tally.
        expect(build.calledFor).toEqual([9])
    })

    it("reports each count, with refreshed nested inside reused", async () => {
        serveAnyInspection()
        serveBuild(
            tally({
                created_count: 3,
                reused_count: 2,
                refreshed_count: 1,
                skipped_terminal_count: 4,
                skipped_info_count: 5,
                skipped_unmapped_count: 6,
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        const lines = within(status)
            .getAllByRole("listitem")
            .map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim())

        expect(lines).toContain("new tasks created: 3")
        // Nested, not a sibling line: `refreshed_count` is a SUB-count of
        // `reused_count`, so listing it alongside would describe 2 reused
        // findings and 1 more refreshed one -- three findings where the
        // server saw two.
        expect(lines).toContain(
            "findings that already had an open task: 2 (1 of them refreshed with the latest snapshot)",
        )
        expect(lines).toContain(
            "findings whose task is already closed, left closed: 4",
        )
        expect(lines).toContain("info findings, which never become tasks: 5")
        expect(lines).toContain(
            "unmapped findings, which are about no record: 6",
        )
        expect(
            within(status).getByText(/3 new tasks are now under Machine findings/),
        ).toBeInTheDocument()

        // And somewhere to go and look at them. A tally with no way through
        // to the queue leaves the admin to remember the URL.
        expect(within(status).getByRole("link", { name: "Open Machine findings" })).toHaveAttribute(
            "href",
            "/admin/curator-queue?submission=7",
        )
    })

    it("says on the page that nothing builds these on upload", async () => {
        /**
         * The reason this button exists at all. An admin who assumes tasks
         * appear by themselves reads an empty queue as "no problems found",
         * when it means "nobody has run this". The same paragraph says a
         * task endorses nothing, which keeps it apart from `record_review`.
         */
        serveAnyInspection()
        await inspect()
        const button = await screen.findByRole("button", { name: BUILD_BUTTON })
        // Scoped to this control's own section. The run control above it
        // makes the same promise about machine review, in nearly the same
        // words, so an unscoped `getByText(/endorses nothing/i)` now matches
        // two paragraphs -- and would have gone on passing while this
        // paragraph said nothing of the kind, satisfied by the other one.
        const section = button.closest("section")!

        expect(within(section).getByText(/created on upload/i)).toBeInTheDocument()
        expect(within(section).getByText(/endorses nothing/i)).toBeInTheDocument()
    })

    it("counts one task in the singular", async () => {
        // "1 new task(s) are now in the queue" is the sort of thing a
        // reader stops trusting the rest of the sentence over.
        serveAnyInspection()
        serveBuild(tally({ created_count: 1 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).getByText(/1 new task is now under Machine findings/),
        ).toBeInTheDocument()
    })

    it("never prints a task id", async () => {
        serveAnyInspection()
        serveBuild(tally({ created_count: 2, task_ids: [4471, 4472] }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))
        await tallyShown()

        // `textContent` covers the rendered words; `outerHTML` covers the
        // places an id can hide from it -- a `title`, a `data-` attribute,
        // an `aria-label`. A reviewer landed exactly that mutation and the
        // textContent-only check passed with both ids in the DOM.
        const text = document.body.textContent ?? ""
        const markup = document.body.outerHTML
        for (const id of ["4471", "4472"]) {
            expect(text).not.toContain(id)
            expect(markup).not.toContain(id)
        }
        expect(text).toContain("new tasks created: 2")
    })

    it("says nothing was queued when there was nothing to queue", async () => {
        serveAnyInspection()
        serveBuild(tally({ skipped_info_count: 9 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).getByText(
                /nothing in this submission is a warning or critical finding/i,
            ),
        ).toBeInTheDocument()
        // The other empty-handed sentence would be a lie here: there was no
        // warning or critical finding at all, so nothing "already has" a task.
        expect(within(status).queryByText(/already has a task/i)).toBeNull()
    })

    it("distinguishes 'nothing to queue' from 'already queued'", async () => {
        serveAnyInspection()
        serveBuild(tally({ reused_count: 3, refreshed_count: 3 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).getByText(
                /every warning or critical finding here already has a task/i,
            ),
        ).toBeInTheDocument()
        expect(
            within(status).queryByText(/nothing in this submission is a warning/i),
        ).toBeNull()
    })

    it("does not imply a closed task is waiting in the queue", async () => {
        /**
         * `created = reused = 0, skipped_terminal = 3`. "Already has one"
         * on its own invites the inference that it is therefore in the
         * queue -- and the queue's open filter will show nothing for this
         * submission, because all three tasks are closed.
         */
        serveAnyInspection()
        serveBuild(tally({ skipped_terminal_count: 3 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).getByText(/already has a task, open or closed/i),
        ).toBeInTheDocument()
    })

    it("does not say there was nothing to build from when there was", async () => {
        /**
         * A finding on a record the builder could not key -- no resolved
         * internal id, an unknown record type -- reaches NONE of the six
         * counts and lands in `warnings` instead. Saying "nothing here is
         * a warning or critical finding" would contradict the warning
         * printed two inches below it.
         */
        serveAnyInspection()
        serveBuild(
            tally({
                warnings: ["Record species/spc_x has no resolved internal id; skipped"],
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).queryByText(/nothing in this submission is a warning/i),
        ).toBeNull()
        expect(
            within(status).getByText(/the warnings below say what was skipped/i),
        ).toBeInTheDocument()
    })

    it("drops the refreshed aside when nothing was reused", async () => {
        // "0 (0 of them refreshed with the latest snapshot)" is noise
        // about a thing that did not happen.
        serveAnyInspection()
        serveBuild(tally({ created_count: 2 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        const lines = within(status)
            .getAllByRole("listitem")
            .map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim())
        expect(lines).toContain("findings that already had an open task: 0")
    })

    it("counts a finding whose task is already closed as considered", async () => {
        /**
         * A submission whose only warning was dismissed months ago has
         * `created = reused = 0` and `skipped_terminal = 1`. Saying "nothing
         * here is a warning or critical finding" would contradict the
         * findings table directly above it.
         */
        serveAnyInspection()
        serveBuild(tally({ skipped_terminal_count: 1 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const status = await tallyShown()
        expect(
            within(status).queryByText(/nothing in this submission is a warning/i),
        ).toBeNull()
    })

    it("lists the build's own warnings", async () => {
        serveAnyInspection()
        serveBuild(
            tally({ created_count: 1, warnings: ["a finding named no record"] }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        expect(
            await screen.findByText("a finding named no record"),
        ).toBeInTheDocument()
    })

    it("says nothing was built when the server refused", async () => {
        serveAnyInspection()
        serveBuild({ detail: "Submission not found." }, { status: 404 })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        await alertSays(/No tasks were built: Submission not found/)
        // No tally: a refused build must not leave a row of zeroes looking
        // like a run that happened and found nothing.
        expect(liveRegion("status", BUILD_STATUS)).toBeEmptyDOMElement()
        expect(screen.getByRole("button", { name: BUILD_BUTTON })).toBeEnabled()
    })

    it("does NOT say nothing was built when the build ran unreadably", async () => {
        /**
         * The 2xx-with-an-unparseable-body case. The build HAS run and may
         * have written rows. The page must not prefix every failure with
         * "No tasks were built:", which would assert a thing and
         * its opposite in a single sentence:
         *
         *   "No tasks were built: The build ran, but this page could not
         *    read the tally. Any tasks it made are under Machine findings."
         */
        serveAnyInspection()
        serveBuild({ this_is: "not a tally" })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const alert = await alertSays(/could not read the tally/)
        expect(alert).not.toHaveTextContent(/No tasks were built/)
        // And it must point at the one place the truth can be found.
        expect(alert).toHaveTextContent(/Machine findings/)
    })

    it("admits it does not know when the request got no answer", async () => {
        // Offline, DNS, a dropped connection. The server may or may not
        // have committed, and guessing either way is a false report.
        serveAnyInspection()
        server.use(http.post(BUILD, () => HttpResponse.error()))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        const alert = await alertSays(/may or may not have run/)
        expect(alert).not.toHaveTextContent(/No tasks were built/)
    })

    it("cannot be pressed twice while one build is in flight", async () => {
        serveAnyInspection()
        const held = gate()
        server.use(
            http.post(BUILD, async () => {
                await held.held
                return HttpResponse.json(tally({ created_count: 1 }))
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))

        // The build writes rows. A second press before the first answers is
        // a second write, and the admin has no way to know which tally they
        // are reading.
        const busy = await screen.findByRole("button", { name: /Building/ })
        expect(busy).toHaveAttribute("aria-disabled", "true")

        held.release()
        await tallyShown()
    })

    it("does not follow the admin to another submission", async () => {
        /**
         * This assertion is the guard, not any one mechanism in the page.
         * Today the tally is dropped because the parent unmounts the whole
         * results block while the next inspection loads; the
         * `key={submissionId}` beside it catches the case where that stops
         * being true. Removing the key fails nothing -- measured -- so
         * without this test both could go and nobody would know until an
         * admin read one submission's tally under another's findings.
         */
        serveAnyInspection()
        serveBuild(tally({ created_count: 3 }))
        const user = await inspect("7")
        await user.click(await screen.findByRole("button", { name: BUILD_BUTTON }))
        await tallyShown()

        await reinspect(user, "9")
        await showing("9")

        // Submission 9's findings with submission 7's tally under them is a
        // false report, and the more convincing for being partly true.
        expect(liveRegion("status", BUILD_STATUS)).toBeEmptyDOMElement()
    })

    it("does not follow the admin BACK to a submission already seen", async () => {
        /**
         * The same rule through the cached door, and the one that made the
         * first version of this feature's comments wrong.
         *
         * Walking to a submission this session has not fetched, `query.data`
         * goes undefined while it loads and the parent unmounts the whole
         * results block -- so the tally dies whether or not the component
         * carries a `key`. Walking BACK, react-query answers from its cache
         * (`gcTime`, five minutes by default): data never goes undefined,
         * nothing unmounts, and the `key` is the only thing left that drops
         * the tally. Without the key this test fails and the
         * one above still passes.
         */
        serveAnyInspection()
        serveBuild(tally({ created_count: 3 }))
        const user = await inspect("7")
        await showing("7")

        await reinspect(user, "9")
        await showing("9")
        await user.click(screen.getByRole("button", { name: BUILD_BUTTON }))
        await tallyShown()

        await reinspect(user, "7")
        await showing("7")
        expect(liveRegion("status", BUILD_STATUS)).toBeEmptyDOMElement()
    })
})

/* ------------------------------------------------------------------ *
 * Running machine review
 *
 * The other half of the same hole. The builder above turns machine-review
 * findings into work for a person, and until this control existed nothing
 * in the product could produce a finding to turn: the machine-review stack
 * is not wired into uploads, so the live deployment held 44 submissions
 * and zero machine reviews, and every screen built on top of them had
 * nothing to show.
 * ------------------------------------------------------------------ */


const RUN = "/api/v1/admin/machine-review/run-for-submission/:submissionId"

const RUN_BUTTON = "Run machine review for this submission"

/** One inspection payload, with whichever record summaries a test wants. */
function inspectionBody(submissionId: unknown, records: Record<string, unknown>[]) {
    return fx.inspection({ submission_id: Number(submissionId), record_summaries: records })
}

/** A record summary naming where it came from, so a wait can assert on it. */
function record(publicRef: string): Record<string, unknown> {
    return fx.recordInspection({
        record_type: "species",
        record_public_ref: publicRef,
        container_type: null,
        container_ref: null,
    })
}

type RunReply = {
    submission_id?: number
    status?: string
    findings_count?: number
    summary?: string | null
    model?: string | null
    provider?: string | null
    audit_event_recorded?: boolean
    failure_reason?: string | null
    audit_event_id?: number
}

/** A run reply with every contract field present, overridable one at a time. */
function runReply(over: RunReply = {}): Record<string, unknown> {
    return {
        submission_id: 7,
        status: "machine_screened_warning",
        findings_count: 0,
        summary: null,
        model: "anthropic/claude-x",
        provider: "CloudMachineReviewProvider",
        audit_event_recorded: true,
        failure_reason: null,
        ...over,
    }
}

/**
 * Serve the run route. The returned array collects the submission ids it was
 * actually called with, which is how "ran for the one on screen" is asserted
 * rather than assumed.
 */
function serveRun(
    body: Record<string, unknown>,
    init?: { status: number },
): { calledFor: number[] } {
    const calledFor: number[] = []
    server.use(
        http.post(RUN, ({ params }) => {
            calledFor.push(Number(params.submissionId))
            return HttpResponse.json(body, init)
        }),
    )
    return { calledFor }
}

describe("the schema behind a machine-review run", () => {
    it("drops keys the contract does not list, so no page can print one", () => {
        /**
         * The contract carries no row id on purpose: `audit_event_recorded`
         * says WHETHER a run was written to the audit log, never which row it
         * became. This pins the mechanism that keeps it that way if the
         * server ever sends one anyway. zod strips what the schema does not
         * declare, so the id never reaches the object the page renders from.
         */
        const parsed = MachineReviewRunResultSchema.parse(
            runReply({ findings_count: 2, audit_event_id: 9911 }),
        )
        expect("audit_event_id" in parsed).toBe(false)
        expect(parsed.findings_count).toBe(2)
    })

    it("reads a status this build has never heard of rather than failing", () => {
        /**
         * The reply is ONE object, so a strict `z.enum` on `status` would
         * throw the whole result away over a token nothing branches on,
         * losing `findings_count`, `summary`, `model` and `failure_reason`,
         * every one of which is readable and is what the admin came for. The
         * page's single branch is a positive test for `machine_review_failed`,
         * so an unfamiliar token is honestly "not that".
         */
        const parsed = MachineReviewRunResultSchema.parse(
            runReply({ status: "machine_screened_something_new" }),
        )
        expect(parsed.status).toBe("machine_screened_something_new")
        expect(reviewFailed(parsed)).toBe(false)
        // ...and it is not dressed up as one of the five this build knows.
        expect(knownRunStatus(parsed.status)).toBeNull()
    })
})

describe("running machine review for a submission", () => {
    it("is not offered before a submission has been chosen", async () => {
        server.use(http.get("/api/v1/submissions/machine-review-overview", () => HttpResponse.json(fx.overviewPage())))
        renderPage()
        await screen.findByRole("table", { name: "Submissions" })
        expect(screen.queryByRole("button", { name: RUN_BUTTON })).toBeNull()
    })

    it("runs for the submission on screen, not some other one", async () => {
        serveAnyInspection()
        const run = serveRun(runReply({ submission_id: 9, findings_count: 1 }))
        const user = await inspect("9")
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))
        await runShown()

        // The id is in the URL path, so a page that ran for a constant, or
        // for whatever was typed first, would still show a plausible result.
        expect(run.calledFor).toEqual([9])
    })

    it("sends the admin session cookie, and no request body", async () => {
        /**
         * Two halves of the same contract. Without `credentials: "include"`
         * the browser attaches no `tckdb_session`, so a logged-in admin
         * reaches an admin-only route anonymously and is refused -- a failure
         * that looks like a permissions problem and is not one. And the route
         * takes no body, so sending one is the page inventing parameters the
         * contract has not got.
         */
        serveAnyInspection()
        const seen: { credentials?: string; body?: string | null }[] = []
        server.use(
            http.post(RUN, async ({ request }) => {
                seen.push({
                    credentials: request.credentials,
                    body: await request.text(),
                })
                return HttpResponse.json(runReply())
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))
        await runShown()

        expect(seen).toHaveLength(1)
        expect(seen[0].credentials).toBe("include")
        expect(seen[0].body).toBe("")
    })

    it("reports what the run recorded", async () => {
        serveAnyInspection()
        serveRun(
            runReply({
                findings_count: 3,
                model: "anthropic/claude-x",
                provider: "CloudMachineReviewProvider",
                summary: "Two geometries lack a frequency calculation.",
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        const lines = within(outcome)
            .getAllByRole("listitem")
            .map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim())

        expect(lines).toContain("findings recorded: 3")
        expect(lines).toContain("model: anthropic/claude-x")
        expect(lines).toContain("provider: CloudMachineReviewProvider")
        expect(outcome).toHaveTextContent(/recorded 3 findings/)
        expect(outcome).toHaveTextContent(
            /Two geometries lack a frequency calculation/,
        )
        // The status badge, which carries the "not human-approved" wording.
        expect(outcome).toHaveTextContent(/screened: warning/)
    })

    it("counts one finding in the singular", async () => {
        serveAnyInspection()
        serveRun(runReply({ findings_count: 1 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/recorded 1 finding\./)
        expect(outcome).not.toHaveTextContent(/recorded 1 findings/)
    })

    it("does not call an off switch a review that found nothing", async () => {
        /**
         * `not_run` is the one outcome with nothing behind it: the route's
         * service returns it "without calling anything" when the reviewer is
         * disabled, and writes no audit event. Reporting it as "the review ran
         * and recorded 0 findings" tells an admin the submission was looked at
         * and is clean. Zero findings from a reviewer that looked and zero
         * from one that is switched off are the same number and opposite news.
         */
        serveAnyInspection()
        serveRun(
            runReply({
                status: "not_run",
                findings_count: 0,
                summary: null,
                model: null,
                provider: null,
                audit_event_recorded: false,
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/No review happened/)
        expect(outcome).toHaveTextContent(/switched off/i)
        expect(outcome).not.toHaveTextContent(/The review ran/)
        expect(outcome).not.toHaveTextContent(/recorded 0 findings/)
        // And it says the absence of a journal entry, which is the archive's
        // own record that nothing happened.
        expect(outcome).toHaveTextContent(/not written to the audit log/)
    })

    it("presents a failed review as a review that ran, not a failed request", async () => {
        /**
         * The whole point of the route answering 200 here. Machine review is
         * advisory, so a reviewer that failed is an OUTCOME the archive
         * recorded, not a broken request, and the two send an admin to
         * different places. "The request failed" points at the network and
         * the deployment; the truth is that a review happened and the
         * reviewer did not manage to produce findings, which is what
         * `failure_reason` explains.
         */
        serveAnyInspection()
        serveRun(
            runReply({
                status: "machine_review_failed",
                findings_count: 0,
                failure_reason: "provider returned no usable JSON after 3 tries",
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/did not produce a usable result/i)
        expect(outcome).toHaveTextContent(/Why the run failed/i)
        expect(outcome).toHaveTextContent(
            /provider returned no usable JSON after 3 tries/,
        )
        // Not this page's three request-failure sentences, any of which would
        // be a false report about a request that in fact succeeded.
        expect(outcome).not.toHaveTextContent(/did not run/i)
        expect(outcome).not.toHaveTextContent(/did not report back/i)
        expect(outcome).not.toHaveTextContent(/could not read/i)
        // And structurally, not in the region this page keeps for requests
        // that went wrong. A reviewer that failed is not one of those.
        expect(liveRegion("alert", RUN_ALERT)).toBeEmptyDOMElement()
    })

    it("does not blame the submission for the reviewer failing", async () => {
        serveAnyInspection()
        serveRun(
            runReply({
                status: "machine_review_failed",
                failure_reason: "rate limited",
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/not a finding about this submission/i)
    })

    it("does not put a local configuration failure in the reviewer's mouth", async () => {
        /**
         * Not every failure on this path reached a reviewer. A missing API
         * key or an unreachable endpoint fails inside this application, and
         * the runner records exactly that sentence.
         *
         * The page must not label it "Reason the reviewer gave" or headline
         * it "the reviewer failed" -- which points an admin at the model when
         * the fault is their own configuration, and blurs the axis between a
         * machine reviewer's judgement and this system's plumbing. Those are
         * the three axes this page is careful about everywhere else.
         */
        serveAnyInspection()
        serveRun(
            runReply({
                status: "machine_review_failed",
                failure_reason:
                    "Machine review could not be configured: Cloud mode requires " +
                    "LLM_PRECHECK_MODEL to be set.",
                summary:
                    "Machine review could not be configured: Cloud mode requires " +
                    "LLM_PRECHECK_MODEL to be set.",
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const outcome = await runShown()
        expect(outcome).toHaveTextContent(/Why the run failed/i)
        expect(outcome).not.toHaveTextContent(/reason the reviewer gave/i)
        expect(outcome).not.toHaveTextContent(/the reviewer failed/i)

        // And the same sentence must not also appear as the reviewer's own
        // summary. The runner sets `summary` and `failure_reason` to the same
        // text on every failure, so an unguarded page printed a configuration
        // error twice -- once as the reviewer's words, which it never was.
        expect(outcome).not.toHaveTextContent(/reviewer.s summary/i)
        const shown = (outcome.textContent ?? "").match(
            /Cloud mode requires LLM_PRECHECK_MODEL to be set/g,
        )
        expect(shown).toHaveLength(1)
    })

    it("shows the findings the run just produced, without a second inspect", async () => {
        /**
         * The findings table is a cached react-query read taken before the
         * run. Without invalidating it the admin is shown the run's own
         * "recorded 1 finding" directly above a table still saying no record
         * received any: the page contradicting itself about work it just
         * did, and an admin's reasonable conclusion is that the run failed.
         */
        const archive = { reviewed: false }
        server.use(
            http.get(INSPECTION, ({ params }) =>
                HttpResponse.json(
                    inspectionBody(
                        params.submissionId,
                        archive.reviewed ? [record("spc_found_by_the_run")] : [],
                    ),
                ),
            ),
            http.post(RUN, () => {
                archive.reviewed = true
                return HttpResponse.json(runReply({ findings_count: 1 }))
            }),
        )

        const user = await inspect()
        // Before: the table says the archive holds nothing for this
        // submission, which is the state 44 of 44 submissions were in.
        await screen.findByText(/No record received a mapped finding/)

        await user.click(screen.getByRole("button", { name: RUN_BUTTON }))
        await runShown()

        expect(await screen.findByText("spc_found_by_the_run")).toBeInTheDocument()
    })

    it("cannot be pressed twice while one run is in flight", async () => {
        serveAnyInspection()
        const held = gate()
        server.use(
            http.post(RUN, async () => {
                await held.held
                return HttpResponse.json(runReply({ findings_count: 1 }))
            }),
        )
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        // A run calls a paid provider and writes rows. A second press before
        // the first answers buys a second review of the same submission, and
        // the admin has no way to know which result they end up reading.
        const busy = await screen.findByRole("button", {
            name: /Running machine review/,
        })
        expect(busy).toHaveAttribute("aria-disabled", "true")

        held.release()
        await runShown()
    })

    it("says the review did not run when the server refused", async () => {
        serveAnyInspection()
        serveRun({ detail: "Submission not found." }, { status: 404 })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        await runAlertSays(/The review did not run: Submission not found/)
        // No outcome: a refused request must not leave a row of zeroes
        // looking like a review that happened and found nothing.
        expect(liveRegion("status", RUN_STATUS)).toBeEmptyDOMElement()
        expect(screen.getByRole("button", { name: RUN_BUTTON })).toBeEnabled()
    })

    it("does NOT say the review did not run when the reply was unreadable", async () => {
        /**
         * The 2xx-with-an-unparseable-body case. A review HAS run, the
         * provider has been paid and rows are written; only the reply could
         * not be read. Telling the admin it did not run invites them to press
         * again and buy a second one.
         */
        serveAnyInspection()
        serveRun({ this_is: "not a run result" })
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const alert = await runAlertSays(/could not read its result/)
        expect(alert).not.toHaveTextContent(/did not run/i)
        // And it must point at the one place the truth can be found.
        expect(alert).toHaveTextContent(/Inspect this submission again/)
    })

    it("admits it does not know when the request got no answer", async () => {
        // Offline, DNS, a dropped connection. A review may or may not have
        // happened, and guessing either way is a false report.
        serveAnyInspection()
        server.use(http.post(RUN, () => HttpResponse.error()))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))

        const alert = await runAlertSays(/may or may not have happened/)
        expect(alert).not.toHaveTextContent(/did not run/i)
    })

    it("says on the page that a machine review is advisory", async () => {
        /**
         * Three separate axes, and this paragraph is where the page keeps
         * them apart: machine review is not human review of a record, and
         * neither is moderation of the submission. An admin who reads a
         * screened-pass badge as an endorsement has been misled by this page,
         * not by the reviewer.
         */
        serveAnyInspection()
        await inspect()
        const button = await screen.findByRole("button", { name: RUN_BUTTON })
        const section = button.closest("section")!

        expect(within(section).getByText(/advisory/i)).toBeInTheDocument()
        expect(within(section).getByText(/endorses\s+nothing/i)).toBeInTheDocument()
        expect(within(section).getByText(/human\s+review/i)).toBeInTheDocument()
        expect(within(section).getByText(/moderation/i)).toBeInTheDocument()
        // And that nothing produces these by itself, which is why an archive
        // with no machine reviews means "nobody has run one", not "clean".
        expect(within(section).getByText(/on upload/i)).toBeInTheDocument()
    })

    it("never prints a row id the server sent anyway", async () => {
        serveAnyInspection()
        serveRun(runReply({ findings_count: 2, audit_event_id: 9911 }))
        const user = await inspect()
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))
        await runShown()

        // `textContent` covers the rendered words; `outerHTML` covers the
        // places an id can hide from it: a `title`, a `data-` attribute, an
        // `aria-label`.
        expect(document.body.textContent ?? "").not.toContain("9911")
        expect(document.body.outerHTML).not.toContain("9911")
        expect(document.body.textContent ?? "").toContain("findings recorded: 2")
    })

    it("does not follow the admin to another submission", async () => {
        /**
         * Submission 9's findings under submission 7's run result is a false
         * report, and the more convincing for being partly true. Today the
         * result is dropped because the parent unmounts the whole results
         * block while the next inspection loads; the `key={submissionId}`
         * beside it catches the case where that stops being true.
         */
        serveAnyInspection()
        serveRun(runReply({ findings_count: 3 }))
        const user = await inspect("7")
        await user.click(await screen.findByRole("button", { name: RUN_BUTTON }))
        await runShown()

        await reinspect(user, "9")
        await showing("9")

        expect(liveRegion("status", RUN_STATUS)).toBeEmptyDOMElement()
    })

    it("does not follow the admin BACK to a submission already seen", async () => {
        /**
         * The same rule through the cached door, and the only one of the two
         * the `key` is load-bearing for. Walking to a submission this session
         * has not fetched, `query.data` goes undefined while it loads and the
         * parent unmounts the whole results block, so the result dies whether
         * or not the component carries a key. Walking BACK, react-query
         * answers from its cache (`gcTime`, five minutes by default): data
         * never goes undefined, nothing unmounts, and the key is the only
         * thing left that drops the stale result.
         */
        serveAnyInspection()
        serveRun(runReply({ findings_count: 3 }))
        const user = await inspect("7")
        await showing("7")

        await reinspect(user, "9")
        await showing("9")
        await user.click(screen.getByRole("button", { name: RUN_BUTTON }))
        await runShown()

        await reinspect(user, "7")
        await showing("7")
        expect(liveRegion("status", RUN_STATUS)).toBeEmptyDOMElement()
    })
})
