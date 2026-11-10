import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react"
import { GLOSSARY, type GlossaryKey } from "../domain/glossary"
import "../term.css"

const CLOSE_DELAY_MS = 150
const EDGE_GAP_PX = 8

/**
 * A glossary word as a button. The explanation is always in the DOM and linked by `aria-describedby`;
 * it opens on hover, focus and tap, closes on Esc with focus kept on the button, and stays open while the pointer is over it.
 */
export function Term({ term, children }: { term: GlossaryKey; children?: ReactNode }) {
    const entry = GLOSSARY[term]
    const popoverId = useId()
    const wrapRef = useRef<HTMLSpanElement>(null)
    const popoverRef = useRef<HTMLSpanElement>(null)
    const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
    const [open, setOpen] = useState(false)

    function cancelClose() {
        if (closeTimer.current !== null) clearTimeout(closeTimer.current)
        closeTimer.current = null
    }
    function show() {
        cancelClose()
        setOpen(true)
    }
    function hideSoon() {
        cancelClose()
        closeTimer.current = setTimeout(() => setOpen(false), CLOSE_DELAY_MS)
    }

    useEffect(() => cancelClose, [])

    // A tap outside closes it; the pointer path has no blur to rely on.
    useEffect(() => {
        if (!open) return
        function onPointerDown(event: PointerEvent) {
            if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
        }
        document.addEventListener("pointerdown", onPointerDown)
        return () => document.removeEventListener("pointerdown", onPointerDown)
    }, [open])

    // Keeps the popover inside the viewport.
    useLayoutEffect(() => {
        const popover = popoverRef.current
        if (!popover) return
        popover.style.transform = ""
        if (!open) return
        const rect = popover.getBoundingClientRect()
        const viewport = document.documentElement.clientWidth
        let shift = 0
        if (rect.right > viewport - EDGE_GAP_PX) shift = viewport - EDGE_GAP_PX - rect.right
        if (rect.left + shift < EDGE_GAP_PX) shift = EDGE_GAP_PX - rect.left
        if (shift !== 0) popover.style.transform = `translateX(${shift}px)`
    }, [open])

    function onKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
        if (event.key !== "Escape" || !open) return
        event.stopPropagation()
        cancelClose()
        setOpen(false)
    }

    return (
        <span
            ref={wrapRef}
            className="term-wrap"
            onMouseEnter={show}
            onMouseLeave={hideSoon}
            onKeyDown={onKeyDown}
            onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) hideSoon()
            }}
        >
            <button
                type="button"
                className="term"
                aria-describedby={popoverId}
                onFocus={show}
                onClick={(event) => {
                    // Enter or Space (detail 0) toggles; a pointer tap opens.
                    if (event.detail === 0) setOpen((wasOpen) => !wasOpen)
                    else show()
                }}
            >
                {children ?? entry.term}
            </button>
            <span ref={popoverRef} id={popoverId} role="tooltip" className="term-popover t-preserve-case" data-open={open ? "true" : "false"}>
                {entry.definition}
            </span>
        </span>
    )
}
