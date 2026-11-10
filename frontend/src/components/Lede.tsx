import type { ReactNode } from "react"
import "../record-blocks.css"

/** One sentence under a record's title, built by the caller from codes and values, never from depositor text. */
export function Lede({ children }: { children: ReactNode }) {
    return <p className="record-lede">{children}</p>
}
