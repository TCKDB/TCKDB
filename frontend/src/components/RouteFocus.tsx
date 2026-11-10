import { useEffect, useRef } from "react"
import { useLocation } from "react-router-dom"
import { pageKey } from "../domain/pageKey"

const SETTLE_LIMIT_MS = 10_000

/** The element a `#fragment` names, when it is on the page. */
function hashTarget(hash: string): HTMLElement | null {
    if (!hash) return null
    let id = hash.replace(/^#/, "")
    try { id = decodeURIComponent(id) } catch { /* use the raw fragment */ }
    const main = document.getElementById("main-content")
    const element = id ? document.getElementById(id) : null
    return element && main?.contains(element) && element !== main ? element : null
}

function focusHeading(hash: string): boolean {
    const main = document.getElementById("main-content")
    if (!main) return false
    const fragment = hashTarget(hash)
    const heading = main.querySelector<HTMLElement>("h1")
    const target = fragment ?? heading ?? main
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1")
    target.focus({ preventScroll: false })
    if (fragment) fragment.scrollIntoView?.()
    return document.activeElement === target
}

/**
 * Moves focus to the new page's `<h1>` (falling back to `<main>`) after a navigation.
 * Pages load lazily and replace their loading heading, so it keeps following the heading until the reader acts.
 */
export function RouteFocus() {
    const { pathname, hash } = useLocation()
    const key = pageKey(pathname)
    const hashRef = useRef(hash)
    // Declared before the navigation effect below so it has the new hash when that effect runs.
    useEffect(() => { hashRef.current = hash }, [hash])
    const previousKey = useRef<string | null>(null)

    useEffect(() => {
        const previous = previousKey.current
        previousKey.current = key
        if (previous === null || previous === key) return

        const main = document.getElementById("main-content")
        if (!main) return
        let stopped = false
        const stop = () => {
            stopped = true
            observer.disconnect()
            window.clearTimeout(timer)
            window.removeEventListener("keydown", stop, true)
            window.removeEventListener("pointerdown", stop, true)
        }
        const refocus = () => {
            if (stopped) return
            const active = document.activeElement
            const lost = !active || active === document.body || !main.contains(active)
            const fragment = hashTarget(hashRef.current)
            const onFallback = active === main || (active instanceof HTMLElement && active.tagName === "H1")
            if (fragment && active !== fragment && (lost || onFallback)) {
                focusHeading(hashRef.current)
                return
            }
            if (lost || (active instanceof HTMLElement && active.tagName === "H1" && active !== main.querySelector("h1"))) focusHeading(hashRef.current)
        }
        const observer = new MutationObserver(refocus)
        observer.observe(main, { childList: true, subtree: true })
        const timer = window.setTimeout(stop, SETTLE_LIMIT_MS)
        window.addEventListener("keydown", stop, true)
        window.addEventListener("pointerdown", stop, true)
        focusHeading(hashRef.current)
        return stop
    }, [key])

    return null
}
