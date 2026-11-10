import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { useTransitionStateSiblings } from "./useTransitionStateSiblings"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup() })
afterAll(() => server.close())

describe("useTransitionStateSiblings", () => {
    it("returns to the first page when the entry or reaction changes, so a page position is never applied to another entry", async () => {
        const seen: string[] = []
        server.use(http.get("/api/v1/scientific/transition-states/search", ({ request }) => {
            const params = new URL(request.url).searchParams
            const offset = Number(params.get("offset") ?? 0)
            seen.push(`${params.get("reaction_ref")}@${offset}`)
            return HttpResponse.json({
                records: [],
                pagination: { offset, limit: 50, returned: 0, total: 120 },
            })
        }))
        const { result, rerender } = renderHook(
            ({ entry }) => useTransitionStateSiblings("rxn_a", entry),
            { initialProps: { entry: "tse_one" } },
        )
        await waitFor(() => expect(result.current.status).toBe("loaded"))
        act(() => result.current.goTo({ offset: 100, limit: 50 }))
        await waitFor(() => expect(seen).toContain("rxn_a@100"))
        rerender({ entry: "tse_two" })
        await waitFor(() => expect(seen.at(-1)).toBe("rxn_a@0"))
        expect(seen.filter((call) => call === "rxn_a@100")).toHaveLength(1)
    })
})
