import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { ThemeToggle } from "./ThemeToggle"

const STORAGE_KEY = "tckdb-theme"

/**
 * A minimal in-memory `Storage`. This jsdom/Node combination does not
 * provide a real `window.localStorage` at all (confirmed directly:
 * `typeof window.localStorage` is `"undefined"` here, matching the
 * `ExperimentalWarning: localStorage is not available` this suite prints
 * on every run) — every `localStorage.getItem`/`setItem` call in
 * `hooks/useTheme.ts` would otherwise hit its `try/catch` on EVERY test,
 * silently exercising only the "storage unavailable" branch and never
 * proving persistence actually works when storage IS available (which it
 * is, in every real browser this app ships to). Installed fresh via
 * `Object.defineProperty` per test, same technique
 * `RefsDisclosure.test.tsx` uses for `navigator.clipboard`.
 */
class MemoryStorage implements Storage {
    private store = new Map<string, string>()
    get length() { return this.store.size }
    clear() { this.store.clear() }
    getItem(key: string) { return this.store.has(key) ? this.store.get(key)! : null }
    key(index: number) { return Array.from(this.store.keys())[index] ?? null }
    removeItem(key: string) { this.store.delete(key) }
    setItem(key: string, value: string) { this.store.set(key, String(value)) }
}

/**
 * `ThemeToggle` is the reader-facing half of the theming pass (see
 * `hooks/useTheme.ts` and `theme.css`). The two things worth testing
 * directly, neither of which `theme.css.test.ts`'s static-text checks can
 * see: that a click actually stamps `data-theme` on `<html>` (the one
 * attribute every rule in `theme.css` keys off), and that a
 * `localStorage` failure — thrown, not merely empty; see `useTheme.ts`'s
 * docstring on private-browsing contexts — never crashes the toggle.
 */
describe("ThemeToggle", () => {
    beforeEach(() => {
        document.documentElement.removeAttribute("data-theme")
        Object.defineProperty(window, "localStorage", { value: new MemoryStorage(), configurable: true })
    })

    afterEach(() => {
        cleanup()
        document.documentElement.removeAttribute("data-theme")
    })

    it("renders a radiogroup of exactly Light/Dark/System, none pressed until a stored choice exists", () => {
        render(<ThemeToggle />)
        expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeInTheDocument()

        const light = screen.getByRole("radio", { name: "Light" })
        const dark = screen.getByRole("radio", { name: "Dark" })
        const system = screen.getByRole("radio", { name: "System" })

        expect(light).toHaveAttribute("aria-checked", "false")
        expect(dark).toHaveAttribute("aria-checked", "false")
        // No stored preference -> "system" is the active default.
        expect(system).toHaveAttribute("aria-checked", "true")
    })

    it("clicking Dark stamps data-theme=\"dark\" on the root element and persists it", () => {
        render(<ThemeToggle />)
        fireEvent.click(screen.getByRole("radio", { name: "Dark" }))

        expect(document.documentElement.getAttribute("data-theme")).toBe("dark")
        expect(window.localStorage.getItem(STORAGE_KEY)).toBe("dark")
        expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true")
        expect(screen.getByRole("radio", { name: "Light" })).toHaveAttribute("aria-checked", "false")
    })

    it("clicking Light after Dark switches data-theme to \"light\" (an explicit light choice beats a dark OS default)", () => {
        render(<ThemeToggle />)
        fireEvent.click(screen.getByRole("radio", { name: "Dark" }))
        fireEvent.click(screen.getByRole("radio", { name: "Light" }))

        expect(document.documentElement.getAttribute("data-theme")).toBe("light")
        expect(window.localStorage.getItem(STORAGE_KEY)).toBe("light")
    })

    it("clicking System after Dark clears data-theme entirely, deferring to prefers-color-scheme", () => {
        render(<ThemeToggle />)
        fireEvent.click(screen.getByRole("radio", { name: "Dark" }))
        fireEvent.click(screen.getByRole("radio", { name: "System" }))

        expect(document.documentElement.hasAttribute("data-theme")).toBe(false)
        expect(window.localStorage.getItem(STORAGE_KEY)).toBe("system")
    })

    it("picks up a previously stored explicit choice on mount", () => {
        window.localStorage.setItem(STORAGE_KEY, "dark")
        render(<ThemeToggle />)

        expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true")
    })

    it("still applies the theme when localStorage.setItem throws (private-browsing style failure)", () => {
        vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
            throw new DOMException("denied")
        })

        render(<ThemeToggle />)
        expect(() => fireEvent.click(screen.getByRole("radio", { name: "Dark" }))).not.toThrow()

        // The in-memory choice still applies for the rest of this session
        // even though it could not be persisted.
        expect(document.documentElement.getAttribute("data-theme")).toBe("dark")
        expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true")
    })
})
