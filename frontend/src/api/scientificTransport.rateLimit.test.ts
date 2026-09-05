import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { requestScientificJson, ScientificApiError, ScientificRateLimitError } from "./scientificTransport"

// ---------------------------------------------------------------------------
// The archive's anonymous-read budget (`rate_limit_anon_read_per_minute`,
// `backend/app/api/config.py:85`) answers an over-budget request with 429
// and an integer-seconds `Retry-After` header
// (`backend/app/api/rate_limit.py:391`). Before this file, NOTHING in the
// frontend read status 429 specially (`grep -rn 429 frontend/src` found
// nothing) -- every caller's generic catch-all rendered "unavailable" for
// what was actually a transient, self-clearing throttle. That is bug #2
// from the owner's report: pressing Back on a species-entry page triggered
// a burst of requests, tripped the limit, and the page stuck on "Entry
// unavailable ... Try again later" until the owner waited out the window
// and refreshed by hand.
//
// `requestScientificJson` now does that wait automatically: on a 429 it
// reads `Retry-After`, sleeps that long, and retries ONCE. Only a second
// consecutive 429 (the archive still over budget a full window later)
// surfaces to the caller, as the distinct `ScientificRateLimitError`
// (never a plain `ScientificApiError` an existing catch-all would collapse
// into "unavailable").
// ---------------------------------------------------------------------------

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); vi.useRealTimers() })
afterAll(() => server.close())

describe("requestScientificJson: 429 handling", () => {
    it("retries once after Retry-After and returns the retry's data, never surfacing the transient 429", async () => {
        vi.useFakeTimers()
        let attempt = 0
        server.use(
            http.get("/api/v1/scientific/probe", () => {
                attempt += 1
                if (attempt === 1) {
                    return HttpResponse.json(
                        { code: "rate_limited", detail: "rate limited" },
                        { status: 429, headers: { "Retry-After": "7" } },
                    )
                }
                return HttpResponse.json({ ok: true, attempt })
            }),
        )

        const pending = requestScientificJson("/api/v1/scientific/probe")
        // Nothing resolves before the retry delay elapses -- proves the
        // wait is real, not a same-tick retry that happened to work.
        let settled = false
        void pending.then(() => { settled = true })
        await vi.advanceTimersByTimeAsync(1000)
        expect(settled).toBe(false)

        await vi.advanceTimersByTimeAsync(7000)
        await expect(pending).resolves.toEqual({ ok: true, attempt: 2 })
        expect(attempt).toBe(2)
    })

    it("surfaces a distinct ScientificRateLimitError, with the wait time, when the retry ALSO comes back 429", async () => {
        vi.useFakeTimers()
        let attempt = 0
        server.use(
            http.get("/api/v1/scientific/probe", () => {
                attempt += 1
                const retryAfter = attempt === 1 ? "4" : "9"
                return HttpResponse.json(
                    { code: "rate_limited", detail: "rate limited" },
                    { status: 429, headers: { "Retry-After": retryAfter } },
                )
            }),
        )

        const pending = requestScientificJson("/api/v1/scientific/probe")
        const assertion = expect(pending).rejects.toBeInstanceOf(ScientificRateLimitError)
        await vi.advanceTimersByTimeAsync(4000)
        await assertion
        expect(attempt).toBe(2)

        // A fresh call sees the SECOND response's own Retry-After (9s), not
        // the first's (4s) -- each attempt reports its own wait honestly.
        await pending.catch((error: unknown) => {
            expect(error).toBeInstanceOf(ScientificRateLimitError)
            expect((error as ScientificRateLimitError).retryAfterSeconds).toBe(9)
            expect((error as ScientificRateLimitError).status).toBe(429)
            expect((error as ScientificRateLimitError).message).toMatch(/retry in about 9s/i)
        })
    })

    it("never retries a non-429 failure, and a plain 503 still classifies as an ordinary ScientificApiError", async () => {
        server.use(http.get("/api/v1/scientific/probe", () => (
            HttpResponse.json({ detail: "down" }, { status: 503 })
        )))
        await expect(requestScientificJson("/api/v1/scientific/probe")).rejects.toEqual(
            expect.objectContaining({ status: 503, message: "down" }),
        )
        // Not the rate-limit subclass -- a 503 is a real outage, not a budget throttle.
        await expect(requestScientificJson("/api/v1/scientific/probe")).rejects.not.toBeInstanceOf(ScientificRateLimitError)
        await expect(requestScientificJson("/api/v1/scientific/probe")).rejects.toBeInstanceOf(ScientificApiError)
    })
})
