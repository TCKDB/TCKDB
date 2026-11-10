import { describe, expect, it, vi } from "vitest"
import { renderHook, waitFor } from "@testing-library/react"
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

    it("lets go of its requests and stops asking for pages when it unmounts, and ignores a late answer", async () => {
        let control: WalkControl | undefined
        let finish: (value: string) => void = () => {}
        const unsubscribe = vi.fn()
        const load = (_ref: string, given: WalkControl) => {
            control = given
            given.subscriptions.push({ unsubscribe } as never)
            return new Promise<string>((resolve) => { finish = resolve })
        }
        const { result, unmount } = renderHook(() => useWalked("a", load))
        expect(control?.abandoned()).toBe(false)
        unmount()
        expect(control?.abandoned()).toBe(true)
        expect(unsubscribe).toHaveBeenCalledTimes(1)
        finish("late")
        await Promise.resolve()
        expect(result.current.status).toBe("loading")
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
