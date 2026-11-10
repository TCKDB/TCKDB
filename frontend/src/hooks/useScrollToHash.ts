import { useEffect } from "react"
import { useLocation } from "react-router-dom"

const SETTLE_MS = 8000

/**
 * Brings the element a `#fragment` names into view and focuses it, including one that mounts a moment after
 * navigation (a record card inside a tab that loads its list). Stops at the first hit, after the reader moves
 * focus or scrolls by key or pointer, or after a few seconds.
 */
export function useScrollToHash() {
    const { hash, pathname } = useLocation()
    useEffect(() => {
        if (!hash) return
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
    }, [hash, pathname])
}
