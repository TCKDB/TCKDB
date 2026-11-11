import { useEffect, useState, type RefObject } from "react"

/** True while the element's content is wider than the element. False where ResizeObserver is missing. */
export function useOverflows(ref: RefObject<HTMLElement | null>): boolean {
    const [overflows, setOverflows] = useState(false)
    useEffect(() => {
        const element = ref.current
        if (!element || typeof ResizeObserver === "undefined") return
        const measure = () => setOverflows(element.scrollWidth > element.clientWidth + 1)
        measure()
        const observer = new ResizeObserver(measure)
        observer.observe(element)
        return () => observer.disconnect()
    }, [ref])
    return overflows
}
