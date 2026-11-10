import { afterEach, describe, expect, it } from "vitest"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import { resetAllRequestCaches } from "../api/requestCache"
import { parsePagedRef } from "../domain/paging"
import { usePagedList } from "./usePagedList"

afterEach(() => {
    cleanup()
    resetAllRequestCaches()
})

const calls: string[] = []

async function loadPage(pagedRefValue: string) {
    calls.push(pagedRefValue)
    const { request } = parsePagedRef(pagedRefValue)
    const offset = request?.offset ?? 0
    return { pagination: { offset, limit: 50, returned: 50, total: 200 } }
}

describe("usePagedList", () => {
    it("starts on the first page, names the page it moves to, and reports the window it was given", async () => {
        const { result } = renderHook(() => usePagedList("subject_a", loadPage))
        await waitFor(() => expect(result.current.state.status).toBe("ready"))
        expect(result.current.listKey).toBe("subject_a")
        act(() => result.current.goTo({ offset: 50, limit: 50 }))
        await waitFor(() => expect(result.current.state.status).toBe("ready"))
        expect(result.current.listKey).toBe("subject_a#50:50")
        expect(result.current.meta?.pagination.offset).toBe(50)
    })

    it("returns to the first page when the subject changes, so one subject's page is never applied to another", async () => {
        const { result, rerender } = renderHook(({ subject }) => usePagedList(subject, loadPage), { initialProps: { subject: "subject_a" } })
        await waitFor(() => expect(result.current.state.status).toBe("ready"))
        act(() => result.current.goTo({ offset: 100, limit: 50 }))
        await waitFor(() => expect(result.current.listKey).toBe("subject_a#100:50"))
        rerender({ subject: "subject_b" })
        expect(result.current.listKey).toBe("subject_b")
        await waitFor(() => expect(result.current.state.status).toBe("ready"))
        expect(calls).toContain("subject_b")
        expect(calls).not.toContain("subject_b#100:50")
    })
})
