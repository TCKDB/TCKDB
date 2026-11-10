import "../record-blocks.css"
import type { LedeText } from "../domain/ledeText"

/** One sentence under a record's title. Its text type admits only codes and numbers, never depositor text. */
export function Lede({ children }: { children: LedeText }) {
    return <p className="record-lede">{children}</p>
}
