import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { GLOSSARY, type GlossaryKey } from "../domain/glossary"
import "../term.css"

const CLOSE_DELAY_MS = 150
const EDGE_GAP_PX = 8

/**
 * A word with an explanation, as a button. Give a glossary `term`, or a free-text `note` with a `label` for the
 * button's accessible name (its visible text is then usually just a mark). The explanation is always in the DOM and
 * linked by `aria-describedby`; `popoverId` lets the caller point a field at the same text. It opens on hover, focus
 * and tap, closes on Esc with focus kept on the button, and stays open while the pointer is over it.
 */
export type TermProps = (
    | { term: GlossaryKey; note?: undefined; label?: undefined }
    | { note: string; label: string; term?: undefined }
) & { children?: ReactNode; popoverId?: string }

export function Term({ term, note, label, children, popoverId: givenId }: TermProps) {
    const entry = term === undefined ? undefined : GLOSSARY[term]
    const generatedId = useId()
    const popoverId = givenId ?? generatedId
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

    // A tap outside closes it, and Esc closes it wherever focus is, including after a hover-only open.
    useEffect(() => {
        if (!open) return
        function onPointerDown(event: PointerEvent) {
            if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
        }
        function onKeyDown(event: globalThis.KeyboardEvent) {
            if (event.key === "Escape") {
                cancelClose()
                setOpen(false)
            }
        }
        document.addEventListener("pointerdown", onPointerDown)
        document.addEventListener("keydown", onKeyDown)
        return () => {
            document.removeEventListener("pointerdown", onPointerDown)
            document.removeEventListener("keydown", onKeyDown)
        }
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

    return (
        <span
            ref={wrapRef}
            className="term-wrap"
            onMouseEnter={show}
            onMouseLeave={hideSoon}
            onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) hideSoon()
            }}
        >
            <button
                type="button"
                className="term"
                aria-describedby={popoverId}
                aria-label={label}
                onFocus={show}
                onClick={(event) => {
                    // Enter or Space (detail 0) toggles; a pointer tap opens.
                    if (event.detail === 0) setOpen((wasOpen) => !wasOpen)
                    else show()
                }}
            >
                {children ?? entry?.term}
            </button>
            <span ref={popoverRef} id={popoverId} role="tooltip" className="term-popover t-preserve-case" data-open={open ? "true" : "false"}>
                {note ?? entry?.definition}
            </span>
        </span>
    )
}
