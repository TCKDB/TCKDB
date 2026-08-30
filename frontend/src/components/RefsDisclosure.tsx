import { useState } from "react"
import { Link } from "react-router-dom"

export type RefEntry = { label: string; value: string; to?: string }

/**
 * Public refs stay visible and copyable, but demoted: collapsed by default
 * behind one "References (N)" toggle per record, not shown as an always-
 * open strip. The count sits on the `<summary>` itself so a reader knows
 * there is something worth opening without opening it -- "References"
 * alone would make them open every card to find out. The ref stays in the
 * DOM either way (Ctrl+F-findable, selectable) once opened; nothing here
 * ever substitutes a label for the ref itself. See the design review:
 * "it's good we have them exposed... but it makes it look messy."
 *
 * Caller's responsibility: if this disclosure sits on a record among
 * several otherwise-identical siblings (e.g. three thermo deposits that
 * differ only by ref), keep that record's own primary identifier visible
 * OUTSIDE this component -- collapsing every ref would make the siblings
 * indistinguishable at rest.
 */
export function RefsDisclosure({ refs, label = "References" }: { refs: RefEntry[]; label?: string }) {
    return (
        <details className="refs-disclosure">
            <summary>{label} ({refs.length})</summary>
            <div className="refs-disclosure-body">
                {refs.map((ref) => <RefRow key={ref.label} {...ref} />)}
            </div>
        </details>
    )
}

function RefRow({ label, value, to }: RefEntry) {
    return <div className="ref-item">
        <span className="ref-item-label">{label}</span>
        {to ? <Link to={to}>{value}</Link> : <span className="ref-item-value">{value}</span>}
        <CopyButton value={value} label={label} />
    </div>
}

type CopyState = "idle" | "copied" | "unavailable"

/**
 * `srLabel` lets a caller describe what's being copied when "reference"
 * is not honest — a raw XYZ block is a text blob, not a ref — while every
 * existing call site (which passes only `value`/`label`) keeps its exact
 * previous aria-label by relying on the default.
 *
 * `navigator.clipboard` is undefined outside a secure context (plain
 * `http://`, not `localhost`) — previously this silently did nothing on
 * click, which a reader has no way to distinguish from "it worked, I just
 * didn't notice". Both that case and a rejected `writeText` (permission
 * denied) now flip to an "unavailable" state the button itself displays,
 * rather than staying indistinguishable from success.
 */
export function CopyButton({ value, label, srLabel = "reference" }: { value: string; label: string; srLabel?: string }) {
    const [state, setState] = useState<CopyState>("idle")
    return <button
        type="button"
        className="copy-button"
        data-copied={state === "copied"}
        data-copy-unavailable={state === "unavailable"}
        aria-label={`Copy ${label} ${srLabel}`}
        onClick={() => {
            if (!navigator.clipboard) {
                setState("unavailable")
                setTimeout(() => setState("idle"), 2000)
                return
            }
            navigator.clipboard.writeText(value)
                .then(() => {
                    setState("copied")
                    setTimeout(() => setState("idle"), 1500)
                })
                .catch(() => {
                    // Clipboard access can be denied even in a secure
                    // context; the value stays selectable on the page
                    // either way, but the button now says so instead of
                    // quietly doing nothing.
                    setState("unavailable")
                    setTimeout(() => setState("idle"), 2000)
                })
        }}
    >{state === "copied" ? "Copied" : state === "unavailable" ? "Unavailable" : "Copy"}</button>
}
