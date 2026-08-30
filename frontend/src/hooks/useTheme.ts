import { useCallback, useEffect, useState } from "react"

/**
 * Three-way theme preference, matching the toggle in `AppShell.tsx`.
 * "system" defers to the OS (`theme.css`'s `@media (prefers-color-scheme:
 * dark)` block) rather than pinning either palette.
 */
export type ThemePreference = "light" | "dark" | "system"

const STORAGE_KEY = "tckdb-theme"

function isThemePreference(value: unknown): value is ThemePreference {
    return value === "light" || value === "dark" || value === "system"
}

/**
 * Reads the stored preference. Wrapped in try/catch because `localStorage`
 * throws outright in some privacy contexts (Safari private browsing with
 * "block all cookies", some locked-down enterprise profiles, sandboxed
 * iframes) rather than merely being empty -- an uncaught read here would
 * crash the whole app before it rendered anything. Falls back to "system",
 * the same default `index.html`'s pre-paint script uses when it finds
 * nothing stored, so a reader who can't persist a choice still gets a
 * consistent (OS-following) theme rather than an undefined one.
 */
function readStoredPreference(): ThemePreference {
    try {
        const stored = localStorage.getItem(STORAGE_KEY)
        if (isThemePreference(stored)) return stored
    } catch {
        // Storage unavailable -- fall through to the "system" default.
    }
    return "system"
}

/**
 * Stamps (or clears) `data-theme` on the root element. This is the ONLY
 * thing that selects a palette: `theme.css`'s three blocks all key off
 * either the absence of `[data-theme]` (bare `:root`, light) or its
 * presence (`[data-theme="light"]` / `[data-theme="dark"]`). "system"
 * removes the attribute so `@media (prefers-color-scheme: dark)` decides,
 * exactly like an unstamped first paint.
 */
function applyPreference(preference: ThemePreference): void {
    const root = document.documentElement
    if (preference === "system") root.removeAttribute("data-theme")
    else root.setAttribute("data-theme", preference)
}

/**
 * Three-way theme state (light/dark/system), persisted to `localStorage`
 * and applied by stamping `data-theme` on `<html>`. The FIRST paint is
 * handled separately by an inline script in `index.html` (this hook can't
 * run before React mounts, so it would otherwise flash the wrong theme
 * for one frame on every load) -- this hook takes over from there so
 * later changes (the toggle, a live OS preference change while "system"
 * is selected) keep applying.
 */
export function useTheme(): {
    preference: ThemePreference
    setPreference: (preference: ThemePreference) => void
} {
    const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference)

    useEffect(() => {
        applyPreference(preference)
    }, [preference])

    const setPreference = useCallback((next: ThemePreference) => {
        setPreferenceState(next)
        try {
            localStorage.setItem(STORAGE_KEY, next)
        } catch {
            // Nothing further to do -- the in-memory preference above still
            // applies for the rest of this session, it just won't survive
            // a reload. Silently accepting that is the correct behaviour
            // here (matches readStoredPreference's fallback), not an error
            // worth surfacing to the reader over a theme toggle.
        }
    }, [])

    return { preference, setPreference }
}
