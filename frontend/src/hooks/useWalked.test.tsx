import { describe, expect, it, vi } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { WalkControl } from "../api/entryNumbersApi"
import { ScientificRateLimitError } from "../api/scientificTransport"
import { useWalked } from "./useWalked"

describe("useWalked", () => {
    it("loads, then reports the data for the ref it was asked for", async () => {
        const load = vi.fn(async (ref: string) => `data for ${ref}`)
        const { result } = renderHook(() => useWalked("a", load))
        expect(result.current.status).toBe("loading")
        await waitFor(() => expect(result.current).toEqual({ ref: "a", status: "ready", data: "data for a" }))
    })

    it("lets go of its requests and stops asking for pages when it unmounts", () => {
        let control: WalkControl | undefined
        const unsubscribe = vi.fn()
        const load = (_ref: string, given: WalkControl) => {
            control = given
            given.subscriptions.push({ unsubscribe } as never)
            return new Promise<string>(() => {})
        }
        const { unmount } = renderHook(() => useWalked("a", load))
        expect(control?.abandoned()).toBe(false)
        unmount()
        expect(control?.abandoned()).toBe(true)
        expect(unsubscribe).toHaveBeenCalledTimes(1)
    })

    it("never shows the old ref's data for a new ref, even when the old load finishes last", async () => {
        const resolvers = new Map<string, (value: string) => void>()
        const load = (ref: string) => new Promise<string>((resolve) => { resolvers.set(ref, resolve) })
        const { result, rerender } = renderHook(({ ref }) => useWalked(ref, load), { initialProps: { ref: "a" } })
        rerender({ ref: "b" })
        // The new ref reads as loading at once, not as the old ref's state.
        expect(result.current).toEqual({ ref: "b", status: "loading" })
        await act(async () => { resolvers.get("a")?.("data for a") })
        expect(result.current).toEqual({ ref: "b", status: "loading" })
        // The abandoned load for "a" left nothing behind: asking for "a" again starts from loading.
        rerender({ ref: "a" })
        expect(result.current).toEqual({ ref: "a", status: "loading" })
        rerender({ ref: "b" })
        await act(async () => { resolvers.get("b")?.("data for b") })
        expect(result.current).toEqual({ ref: "b", status: "ready", data: "data for b" })
    })

    it("does not let a ready state for one ref stand in for the next ref while it loads", async () => {
        const load = (ref: string) => (ref === "a" ? Promise.resolve("data for a") : new Promise<string>(() => {}))
        const { result, rerender } = renderHook(({ ref }) => useWalked(ref, load), { initialProps: { ref: "a" } })
        await waitFor(() => expect(result.current.status).toBe("ready"))
        rerender({ ref: "b" })
        expect(result.current).toEqual({ ref: "b", status: "loading" })
    })

    it("shows a rate-limit wait while it lasts and the failure kinds apart", async () => {
        const waiting = renderHook(() => useWalked("a", (_ref, control) => {
            control.onWaiting(30)
            return new Promise<string>(() => {})
        }))
        await waitFor(() => expect(waiting.result.current).toEqual({ ref: "a", status: "retrying", retryAfterSeconds: 30 }))
        const limited = renderHook(() => useWalked("b", async () => { throw new ScientificRateLimitError(45) }))
        await waitFor(() => expect(limited.result.current).toEqual({ ref: "b", status: "rate-limited", retryAfterSeconds: 45 }))
        const failed = renderHook(() => useWalked("c", async () => { throw new Error("boom") }))
        await waitFor(() => expect(failed.result.current.status).toBe("failed"))
    })
})
