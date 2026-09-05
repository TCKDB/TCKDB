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

    // Review follow-up (SHOULD-FIX #1): the wait itself needs to be
    // observable so a caller can render "the archive is busy, retrying in
    // Ns" instead of a plain, indefinite "Loading …" for however long the
    // wait runs (up to a minute -- `rate_limit_anon_read_per_minute`,
    // `backend/app/api/config.py`). `onRateLimited` is that hook: called
    // synchronously, exactly once, the INSTANT the 429 is seen -- before
    // the wait starts, never after.
    it("calls onRateLimited synchronously, once, with the wait time, before the retry -- never on a non-429", async () => {
        vi.useFakeTimers()
        let attempt = 0
        server.use(
            http.get("/api/v1/scientific/probe", () => {
                attempt += 1
                if (attempt === 1) {
                    return HttpResponse.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "12" } })
                }
                return HttpResponse.json({ ok: true })
            }),
        )
        const onRateLimited = vi.fn()
        const pending = requestScientificJson("/api/v1/scientific/probe", undefined, onRateLimited)

        // The callback must fire before the retry delay elapses -- it
        // announces the wait, it does not wait for it.
        await vi.advanceTimersByTimeAsync(0)
        expect(onRateLimited).toHaveBeenCalledExactlyOnceWith(12)

        await vi.advanceTimersByTimeAsync(12000)
        await expect(pending).resolves.toEqual({ ok: true })
        expect(onRateLimited).toHaveBeenCalledOnce() // never called again on the successful retry

        const onRateLimitedForOk = vi.fn()
        server.use(http.get("/api/v1/scientific/probe", () => HttpResponse.json({ ok: true })))
        await requestScientificJson("/api/v1/scientific/probe", undefined, onRateLimitedForOk)
        expect(onRateLimitedForOk).not.toHaveBeenCalled()
    })

    // Nit fix: `Retry-After` is legally EITHER an integer-seconds count
    // (what the live API sends) OR an HTTP-date (RFC 9110 §10.2.3) -- the
    // date form used to silently fall back to the 5s default because
    // `Number.parseInt` on a string starting with a weekday name is `NaN`.
    it("parses an HTTP-date Retry-After, not just the integer-seconds form", async () => {
        vi.useFakeTimers()
        const now = new Date("2026-01-01T00:00:00.000Z")
        vi.setSystemTime(now)
        const retryAt = new Date(now.getTime() + 20_000) // 20s from "now"
        let attempt = 0
        server.use(
            http.get("/api/v1/scientific/probe", () => {
                attempt += 1
                if (attempt === 1) {
                    return HttpResponse.json(
                        { code: "rate_limited" },
                        { status: 429, headers: { "Retry-After": retryAt.toUTCString() } },
                    )
                }
                return HttpResponse.json({ ok: true })
            }),
        )
        const onRateLimited = vi.fn()
        const pending = requestScientificJson("/api/v1/scientific/probe", undefined, onRateLimited)
        await vi.advanceTimersByTimeAsync(0)
        // Allow a 1s rounding slop either way (Math.ceil on a wall-clock diff).
        expect(onRateLimited).toHaveBeenCalledTimes(1)
        expect(onRateLimited.mock.calls[0][0]).toBeGreaterThanOrEqual(19)
        expect(onRateLimited.mock.calls[0][0]).toBeLessThanOrEqual(21)

        await vi.advanceTimersByTimeAsync(21_000)
        await expect(pending).resolves.toEqual({ ok: true })
    })
})
