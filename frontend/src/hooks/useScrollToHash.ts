import { useEffect } from "react"
import { useLocation } from "react-router-dom"
import { usePageSections } from "./usePageSections"

const SETTLE_MS = 8000

/** Fired before a programmatic scroll so the contents list does not read it as the reader's own scrolling. */
export const SUPPRESS_SCROLL_SPY_EVENT = "page-toc:suppress-scroll-spy"

/**
 * Brings the element a `#fragment` names into view and focuses it, including one that mounts a moment after
 * navigation (a record card inside a tab that loads its list). Closed disclosures around the target are opened first.
 * Ids the contents list owns are left to it. Stops at the first hit, once the reader presses a key or the pointer, or
 * after a few seconds. Re-runs for every navigation (`location.key`), so following the same link twice works.
 */
export function useScrollToHash() {
    const { hash, key } = useLocation()
    const sections = usePageSections()
    const owned = sections.some((section) => `#${section.id}` === hash)
    useEffect(() => {
        if (!hash || owned) return
        const id = decodeURIComponent(hash.slice(1))
        let done = false
        const stop = () => {
            done = true
            observer.disconnect()
            window.clearTimeout(timer)
            window.removeEventListener("keydown", stop, true)
            window.removeEventListener("pointerdown", stop, true)
        }
        const reach = () => {
            if (done) return
            const target = document.getElementById(id)
            if (!target) return
            for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) {
                if (ancestor instanceof HTMLDetailsElement && !ancestor.open) ancestor.open = true
            }
            window.dispatchEvent(new Event(SUPPRESS_SCROLL_SPY_EVENT))
            if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1")
            target.focus({ preventScroll: true })
            target.scrollIntoView?.({ block: "start" })
            stop()
        }
        const observer = new MutationObserver(reach)
        observer.observe(document.body, { childList: true, subtree: true })
        const timer = window.setTimeout(stop, SETTLE_MS)
        window.addEventListener("keydown", stop, true)
        window.addEventListener("pointerdown", stop, true)
        reach()
        return stop
    }, [hash, key, owned])
}
