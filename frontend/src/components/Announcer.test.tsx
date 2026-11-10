import { afterEach, describe, expect, it } from "vitest"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { Announcer } from "./Announcer"
import { announce } from "../hooks/useAnnounce"

afterEach(cleanup)

function region() {
    return document.querySelector(".announcer") as HTMLElement
}

describe("Announcer", () => {
    it("is one polite, atomic live region that is not role=status", () => {
        render(<Announcer />)
        expect(region()).toHaveAttribute("aria-live", "polite")
        expect(region()).toHaveAttribute("aria-atomic", "true")
        expect(region()).not.toHaveAttribute("role")
    })

    it("shows an announced message", async () => {
        render(<Announcer />)
        act(() => announce("3 matches"))
        await waitFor(() => expect(region()).toHaveTextContent("3 matches"))
    })

    it("clears before setting, so a repeated message is read again", async () => {
        render(<Announcer />)
        const seen: string[] = []
        const observer = new MutationObserver(() => seen.push(region().textContent ?? ""))
        observer.observe(region(), { childList: true, characterData: true, subtree: true })
        act(() => announce("No exact match"))
        await waitFor(() => expect(region()).toHaveTextContent("No exact match"))
        act(() => announce("No exact match"))
        await waitFor(() => expect(seen.filter((text) => text === "").length).toBeGreaterThanOrEqual(1))
        await waitFor(() => expect(region()).toHaveTextContent("No exact match"))
        // Set, cleared, set again: two separate insertions of the same text.
        expect(seen.filter((text) => text === "No exact match")).toHaveLength(2)
        observer.disconnect()
    })
})
