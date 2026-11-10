import { useCallback, useSyncExternalStore } from "react"

export const PHONE_QUERY = "(max-width: 680px)"
export const NARROW_QUERY = "(max-width: 62rem)"

/** Whether a media query matches now, and again when it changes. False where `matchMedia` is missing. */
export function useMediaQuery(query: string): boolean {
    const subscribe = useCallback((onChange: () => void) => {
        if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {}
        const list = window.matchMedia(query)
        list.addEventListener("change", onChange)
        return () => list.removeEventListener("change", onChange)
    }, [query])
    const getSnapshot = useCallback(
        () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches,
        [query],
    )
    return useSyncExternalStore(subscribe, getSnapshot, () => false)
}
