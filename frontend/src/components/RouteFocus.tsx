import { useEffect, useRef } from "react"
import { useLocation } from "react-router-dom"
import { pageKey } from "../domain/pageKey"

const SETTLE_LIMIT_MS = 10_000

function focusHeading(): boolean {
    const main = document.getElementById("main-content")
    if (!main) return false
    const heading = main.querySelector<HTMLElement>("h1")
    const target = heading ?? main
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1")
    target.focus({ preventScroll: false })
    return document.activeElement === target
}

/**
 * Moves focus to the new page's `<h1>` (falling back to `<main>`) after a navigation.
 * Pages load lazily and replace their loading heading, so it keeps following the heading until the reader acts.
 */
export function RouteFocus() {
    const { pathname } = useLocation()
    const key = pageKey(pathname)
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
            if (lost || (active instanceof HTMLElement && active.tagName === "H1" && active !== main.querySelector("h1"))) focusHeading()
        }
        const observer = new MutationObserver(refocus)
        observer.observe(main, { childList: true, subtree: true })
        const timer = window.setTimeout(stop, SETTLE_LIMIT_MS)
        window.addEventListener("keydown", stop, true)
        window.addEventListener("pointerdown", stop, true)
        focusHeading()
        return stop
    }, [key])

    return null
}
