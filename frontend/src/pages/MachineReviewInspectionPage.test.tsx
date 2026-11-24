import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useEffect } from "react"
import { MemoryRouter, useNavigate } from "react-router-dom"
import MachineReviewInspectionPage from "./MachineReviewInspectionPage"
import { AuthProvider } from "../components/AuthProvider"
import { CuratorTaskBuildResultSchema } from "../types/curatorTask"
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

const PROVIDERS = "/api/v1/machine-review/providers"
const JOBS = "/api/v1/machine-review/jobs"
const JOB = "/api/v1/machine-review/jobs/:jobId"

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
        http.get(PROVIDERS, () => HttpResponse.json(fx.providers())),
        http.get(JOBS, () => HttpResponse.json(fx.jobPage([]))),
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
    it("a curator may open it, and no admin-only read is made", async () => {
        const requested: string[] = []
        server.events.on("request:start", ({ request }) => requested.push(new URL(request.url).pathname))
        serveHeaderRoutes()
        serveInspection()
        renderPage("7", curator)
        expect(await screen.findByRole("heading", { level: 2, name: "Machine review" })).toBeInTheDocument()
        await recordCard(`Kinetics ${fx.KINETICS_REF}`)
        expect(requested.filter((path) => path.startsWith("/api/v1/admin/users"))).toEqual([])
        expect(requested.filter((path) => path.includes("curator-tasks"))).toEqual([])
        expect(screen.queryByRole("button", { name: "Build curator tasks for this submission" })).toBeNull()
        for (const link of screen.queryAllByRole("link")) expect(link.getAttribute("href") ?? "").not.toContain("/admin/curator-queue")
        expect(screen.getByText("not named")).toBeInTheDocument()
    })

    it("an admin still has the builder and the task links", async () => {
        serveHeaderRoutes()
        serveInspection()
        renderPage("7", admin)
        await recordCard(`Kinetics ${fx.KINETICS_REF}`)
        expect(screen.getByRole("button", { name: "Build curator tasks for this submission" })).toBeInTheDocument()
        expect(screen.getAllByRole("link", { name: /curator task/i })[0].getAttribute("href")).toContain("/admin/curator-queue")
    })

    it("a plain user is refused and nothing is requested", async () => {
        const requested: string[] = []
        server.events.on("request:start", ({ request }) => requested.push(new URL(request.url).pathname))
        renderPage(undefined, plainUser)
        expect(await screen.findByRole("alert")).toHaveTextContent("This page is for curators and administrators.")
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
        expect(within(kinetics).getAllByRole("group", { name: "Written by the machine reviewer" }).length).toBeGreaterThan(0)
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
        expect(text).toContain("Cited:")
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
 * The page's curator-task build live regions, by accessible name.
 *
 * An unqualified `getByRole("status")` matches two elements and throws,
 * because "the status region" does not name one thing. The regions carry
 * `aria-label`s for the same reason a screen-reader user needs them: to
 * tell which button a result answered.
 */
const BUILD_ALERT = "curator task build problem"
const BUILD_STATUS = "curator task build result"

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


/* ------------------------------------------------------------------ *
 * Requesting a machine review and watching it
 *
 * A review is requested with POST /jobs, which answers at once; the page then
 * polls the job every two seconds, one step at a time, until it ends.
 * ------------------------------------------------------------------ */

const STEP_KINDS = ["claimed", "context_built", "model_turn", "tool_call", "final_output", "finished"]

/** A job read at one point of its life, with `steps` progress entries so far. */
function jobAt(status: string, steps: number, over: Record<string, unknown> = {}) {
    return fx.jobDetail({
        status,
        step_count: steps,
        started_at: status === "queued" ? null : "2026-11-24T10:00:01",
        finished_at: ["succeeded", "failed", "cancelled"].includes(status) ? "2026-11-24T10:00:07" : null,
        events: STEP_KINDS.slice(0, steps).map((kind, i) => fx.jobEvent({ seq: i + 1, kind })),
        ...over,
    })
}

/**
 * Serve GET /jobs/{id} from a script: the nth poll gets the nth body (the last repeats), cut to the steps after
 * `after_seq` as the server does. The returned array holds the `after_seq` of every poll.
 */
function serveJobScript(script: Record<string, unknown>[]): { afterSeq: number[] } {
    const afterSeq: number[] = []
    server.use(
        http.get(JOB, ({ request }) => {
            const after = Number(new URL(request.url).searchParams.get("after_seq") ?? "0")
            const body = script[Math.min(afterSeq.length, script.length - 1)]
            afterSeq.push(after)
            const events = (body.events as Array<{ seq: number }>).filter((e) => e.seq > after)
            return HttpResponse.json({ ...body, events })
        }),
    )
    return { afterSeq }
}

function serveRequest(job: Record<string, unknown> = fx.job({ status: "queued", step_count: 0, started_at: null, finished_at: null, audit_event_id: null })) {
    const posts: unknown[] = []
    server.use(
        http.post(JOBS, async ({ request }) => {
            posts.push(await request.json())
            return HttpResponse.json(job, { status: 202 })
        }),
    )
    return posts
}

const REQUEST_BUTTON = "Request machine review"

function jobPolls(afterSeq: number[]): number {
    return afterSeq.length
}

async function pressRequest(user: ReturnType<typeof userEvent.setup>) {
    const button = await screen.findByRole("button", { name: REQUEST_BUTTON })
    await waitFor(() => expect(button).not.toHaveAttribute("aria-disabled"))
    await user.click(button)
}

describe("requesting a machine review", () => {
    afterEach(() => vi.useRealTimers())

    function renderWithTimers(who: Record<string, unknown> = curator, id = "7") {
        vi.useFakeTimers({ shouldAdvanceTime: true })
        serveHeaderRoutes()
        serveInspection()
        renderPage(id, who)
        return userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    }

    it("sends the request for the submission on screen with the one reviewer on offer, and shows the job at once", async () => {
        const user = renderWithTimers()
        const posts = serveRequest()
        const { afterSeq } = serveJobScript([jobAt("queued", 0)])
        await pressRequest(user)
        expect(await screen.findByText("queued")).toBeInTheDocument()
        expect(posts).toEqual([{ submission_id: fx.SUBMISSION_ID, provider_key: "fake" }])
        expect(afterSeq.length).toBeGreaterThan(0)
        expect(screen.queryByRole("combobox", { name: "Reviewer" })).toBeNull()
    })

    it("polls every two seconds while the job is open and lists each step once", async () => {
        const user = renderWithTimers()
        serveRequest()
        const { afterSeq } = serveJobScript([jobAt("queued", 0), jobAt("running", 1), jobAt("running", 2), jobAt("running", 2)])
        await pressRequest(user)
        await screen.findByText("queued")
        await act(() => vi.advanceTimersByTimeAsync(2100))
        await act(() => vi.advanceTimersByTimeAsync(2100))
        await act(() => vi.advanceTimersByTimeAsync(2100))

        expect(jobPolls(afterSeq)).toBeGreaterThanOrEqual(4)
        const steps = within(screen.getByRole("list", { name: "steps of the review" })).getAllByRole("listitem")
        expect(steps.map((li) => li.textContent)).toEqual(["Step 1: picked up by a reviewer", "Step 2: reading the submission"])
        // After the first sight of a step, each poll asks only for what came after it.
        expect(afterSeq.slice(0, 4)).toEqual([0, 0, 1, 2])
    })

    it.each([
        ["succeeded", "Machine review finished"],
        ["failed", "Machine review did not finish"],
        ["cancelled", "Machine review cancelled"],
    ])("stops polling once the job has %s", async (status, heading) => {
        const user = renderWithTimers()
        serveRequest()
        const { afterSeq } = serveJobScript([jobAt("running", 1), jobAt(status, 2, status === "failed" ? { failure_code: "provider_timeout" } : {})])
        await pressRequest(user)
        await act(() => vi.advanceTimersByTimeAsync(2100))
        await screen.findByRole("heading", { level: 3, name: heading })
        const settled = jobPolls(afterSeq)
        expect(settled).toBeGreaterThanOrEqual(2)
        await act(() => vi.advanceTimersByTimeAsync(5000))
        expect(jobPolls(afterSeq)).toBe(settled)
    })

    it("words a failure by its code, and says no review was recorded for a cancelled one", async () => {
        const user = renderWithTimers()
        serveRequest()
        serveJobScript([jobAt("failed", 2, { failure_code: "provider_timeout", audit_event_id: null })])
        await pressRequest(user)
        expect(await screen.findByText("Machine review did not finish: the reviewer took too long.")).toBeInTheDocument()
    })

    it("moves focus to the result heading when the job ends, and refreshes the inspection", async () => {
        const user = renderWithTimers()
        serveRequest()
        serveJobScript([jobAt("running", 1), jobAt("succeeded", 4)])
        let inspections = 0
        server.use(http.get(INSPECTION, () => { inspections += 1; return HttpResponse.json(fx.inspection({ submission_id: 7 })) }))
        await pressRequest(user)
        await act(() => vi.advanceTimersByTimeAsync(2100))
        const heading = await screen.findByRole("heading", { level: 3, name: "Machine review finished" })
        await waitFor(() => expect(heading).toHaveFocus())
        await waitFor(() => expect(inspections).toBeGreaterThanOrEqual(2))
    })

    it("announces in one polite status region whose text changes with the step, not with the poll", async () => {
        const user = renderWithTimers()
        serveRequest()
        serveJobScript([jobAt("running", 1), jobAt("running", 1), jobAt("running", 2)])
        await pressRequest(user)
        const region = await screen.findByRole("status", { name: "machine review progress" })
        expect(region).toHaveAttribute("aria-live", "polite")
        await waitFor(() => expect(region).toHaveTextContent("Step 1"))
        const first = region.textContent
        await act(() => vi.advanceTimersByTimeAsync(2100))
        expect(screen.getByRole("status", { name: "machine review progress" })).toBe(region)
        expect(region.textContent).toBe(first)
        await act(() => vi.advanceTimersByTimeAsync(2100))
        await waitFor(() => expect(region).toHaveTextContent("Step 2"))
        expect(region.textContent).not.toBe(first)
    })

    it("attaches to the running review when one is already open, without showing an id", async () => {
        const user = renderWithTimers()
        let conflicted = false
        server.use(
            http.post(JOBS, () => {
                conflicted = true
                return HttpResponse.json(
                    { detail: "This submission already has a machine review queued or running.", code: "machine_review_job_conflict", context: { open_job: { status: "running", step_count: 2 } } },
                    { status: 409 },
                )
            }),
            http.get(JOBS, () =>
                HttpResponse.json(fx.jobPage(conflicted ? [fx.job({ status: "running", step_count: 2, finished_at: null, audit_event_id: null })] : [])),
            ),
        )
        serveJobScript([jobAt("running", 2)])
        await pressRequest(user)
        expect(await screen.findByText("A review was already running, so this page is showing that one.")).toBeInTheDocument()
        expect(await screen.findByText("Step 2: reading the submission")).toBeInTheDocument()
        expect(pageText()).not.toContain(String(fx.JOB_ID))
    })

    it("shows the progress of a review that is already running when the page opens", async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true })
        serveHeaderRoutes()
        serveInspection()
        server.use(http.get(JOBS, () => HttpResponse.json(fx.jobPage([fx.job({ status: "running", step_count: 2, finished_at: null, audit_event_id: null })]))))
        serveJobScript([jobAt("running", 2)])
        renderPage("7", curator)
        expect(await screen.findByText("Step 2: reading the submission")).toBeInTheDocument()
        expect(screen.getByRole("button", { name: REQUEST_BUTTON })).toHaveAttribute("aria-disabled", "true")
    })

    it("cancels a running review it was asked for", async () => {
        const user = renderWithTimers({ ...curator, id: 3 })
        serveRequest()
        serveJobScript([jobAt("running", 1)])
        let cancelled = 0
        server.use(http.post(`${JOB}/cancel`, () => { cancelled += 1; return HttpResponse.json(fx.job({ status: "running", cancel_requested: true })) }))
        await pressRequest(user)
        await user.click(await screen.findByRole("button", { name: "Cancel this review" }))
        await waitFor(() => expect(cancelled).toBe(1))
    })

    it("does not offer Cancel to a curator who did not ask for the review", async () => {
        const user = renderWithTimers({ ...curator, id: 99 })
        serveRequest()
        serveJobScript([jobAt("running", 1)])
        await pressRequest(user)
        const cancel = await screen.findByRole("button", { name: "Cancel this review" })
        expect(cancel).toHaveAttribute("aria-disabled", "true")
        expect(cancel).toHaveAccessibleDescription(/Only the person who requested this review, or an administrator/)
    })

    it("explains a switched-off curator request on the button, and an admin is not held by it", async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true })
        serveHeaderRoutes()
        serveInspection()
        server.use(http.get(PROVIDERS, () => HttpResponse.json(fx.providers({ fake: "curator_requests_disabled", local: "curator_requests_disabled", cloud: "curator_requests_disabled", claude_code: "curator_requests_disabled", codex: "curator_requests_disabled" }))))
        renderPage("7", curator)
        const button = await screen.findByRole("button", { name: REQUEST_BUTTON })
        await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"))
        await waitFor(() => expect(button).toHaveAccessibleDescription("curator requests are switched off"))
        cleanup()
        server.resetHandlers()
        serveHeaderRoutes()
        serveInspection()
        renderPage("7", admin)
        const adminButton = await screen.findByRole("button", { name: REQUEST_BUTTON })
        await waitFor(() => expect(adminButton).not.toHaveAttribute("aria-disabled"))
    })

    it("lets the caller choose when more than one reviewer is available, and disables the others with the reason", async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true })
        serveHeaderRoutes()
        serveInspection()
        server.use(http.get(PROVIDERS, () => HttpResponse.json(fx.providers({ local: "ok" }))))
        renderPage("7", curator)
        const select = await screen.findByRole("combobox", { name: "Reviewer" })
        const options = within(select).getAllByRole("option")
        expect(options.map((o) => [o.textContent, (o as HTMLOptionElement).disabled])).toEqual([
            ["test reviewer", false],
            ["local model", false],
            ["cloud model (not set up in this deployment)", true],
            ["Claude Code (no remote runner is connected)", true],
            ["Codex (no remote runner is connected)", true],
        ])
    })

    it("tells the caller that an outside reviewer receives the submission", async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true })
        serveHeaderRoutes()
        serveInspection()
        server.use(http.get(PROVIDERS, () => HttpResponse.json(fx.providers({ fake: "ok", claude_code: "ok" }))))
        renderPage("7", admin)
        const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
        const select = await screen.findByRole("combobox", { name: "Reviewer" })
        expect(screen.queryByText("This review sends the submission to an outside service.")).toBeNull()
        await user.selectOptions(select, "claude_code")
        expect(screen.getByText("This review sends the submission to an outside service.")).toBeInTheDocument()
    })
})

describe("the advice line", () => {
    function advice(runs: Record<string, unknown>[], jobs: Record<string, unknown>[] = []) {
        serveHeaderRoutes()
        server.use(http.get(JOBS, () => HttpResponse.json(fx.jobPage(jobs))))
        serveInspection(fx.inspection({ runs }))
        renderPage("7", curator)
    }

    it.each([
        ["machine_screened_pass", "Machine reviewer recommends approval (advisory)"],
        ["machine_screened_warning", "Machine reviewer recommends approval after reading its notes (advisory)"],
        ["machine_screened_needs_attention", "Machine reviewer does not recommend approval yet (advisory)"],
    ])("says %s in the archive's own words", async (status, line) => {
        advice([fx.run({ status })])
        expect(await screen.findByTestId("recommendation")).toHaveTextContent(line)
    })

    it("words a failed review by the failure code of the job that recorded it", async () => {
        advice(
            [fx.run({ status: "machine_review_failed", audit_event_id: 7001 })],
            [fx.job({ audit_event_id: 7001, status: "failed", failure_code: "invalid_output" }), fx.job({ id: 2, audit_event_id: 7000, failure_code: null })],
        )
        await waitFor(() =>
            expect(screen.getByTestId("recommendation")).toHaveTextContent("Machine review did not finish: the reviewer's answer could not be used"),
        )
    })

    it("says the latest review could not be read when its stored result is unreadable", async () => {
        advice([fx.run({ parsed: false, status: null, summary: null, audit_event_id: 7001 })])
        expect(await screen.findByTestId("recommendation")).toHaveTextContent("The latest machine review could not be read.")
    })

    it("shows nothing when nothing was reviewed", async () => {
        advice([])
        await screen.findByRole("heading", { level: 2, name: "Machine review" })
        expect(screen.queryByTestId("recommendation")).toBeNull()
    })
})

describe("the reviewer's words", () => {
    it("sit only inside blocks labelled as written by the machine reviewer", async () => {
        serveHeaderRoutes()
        serveInspection()
        renderPage("7", admin)
        await recordCard(`Kinetics ${fx.KINETICS_REF}`)

        const groups = screen.getAllByRole("group", { name: "Written by the machine reviewer" })
        const inside = (text: string) => groups.some((g) => g.textContent?.includes(text))
        for (const text of [
            "REVIEWER_SUMMARY overall the kinetics look unreliable",
            "REVIEWER_MESSAGE the rate constant is far above the collision limit",
            "REVIEWER_ACTION re-check the A factor units",
            "kinetics.a",
            "CITED_TYPE_VERBATIM",
            "CITED_REF_VERBATIM",
            "MAPPING_WARNING one finding named two records",
        ]) {
            expect(inside(text), `${text} is in a labelled block`).toBe(true)
        }

        // Outside every labelled block, none of it appears.
        const outside = (needle: string) =>
            screen.queryAllByText((_, el) => el !== null && el.children.length === 0 && (el.textContent ?? "").includes(needle) && !groups.some((g) => g.contains(el)))
        for (const needle of ["REVIEWER_SUMMARY", "REVIEWER_MESSAGE", "REVIEWER_ACTION", "CITED_TYPE_VERBATIM", "MAPPING_WARNING"]) {
            expect(outside(needle), `${needle} outside a labelled block`).toHaveLength(0)
        }
    })

    it("are text, not markup", async () => {
        serveHeaderRoutes()
        serveInspection(fx.inspection({ runs: [fx.run({ summary: "<b>bold</b> **star**" })] }))
        renderPage("7", admin)
        const group = (await screen.findAllByRole("group", { name: "Written by the machine reviewer" })).find((g) => g.textContent?.includes("star"))
        expect(group?.textContent).toContain("<b>bold</b> **star**")
        expect(group?.querySelector("b")).toBeNull()
    })
})

describe("the submission list shows an open review", () => {
    it("says queued, or running with the step", async () => {
        serveHeaderRoutes()
        server.use(
            http.get("/api/v1/submissions/machine-review-overview", () =>
                HttpResponse.json(
                    fx.overviewPage([
                        fx.overviewRow({ id: 1, public_ref: "sub_one", machine_review: { ...(fx.overviewRow().machine_review as object), open_job: fx.openJob({ status: "queued", step_count: 0, started_at: null }) } }),
                        fx.overviewRow({ id: 2, public_ref: "sub_two", machine_review: { ...(fx.overviewRow().machine_review as object), open_job: fx.openJob({ status: "running", step_count: 2 }) } }),
                        fx.overviewRow({ id: 3, public_ref: "sub_three" }),
                    ]),
                ),
            ),
        )
        renderPage(undefined, curator)
        expect(await screen.findByText("review queued")).toBeInTheDocument()
        expect(screen.getByText("review running (step 2)")).toBeInTheDocument()
        expect(screen.getAllByText(/review (queued|running)/)).toHaveLength(2)
    })
})
