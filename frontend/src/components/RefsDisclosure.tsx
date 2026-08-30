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

export function CopyButton({ value, label }: { value: string; label: string }) {
    const [copied, setCopied] = useState(false)
    return <button
        type="button"
        className="copy-button"
        data-copied={copied}
        aria-label={`Copy ${label} reference`}
        onClick={() => {
            if (!navigator.clipboard) return
            navigator.clipboard.writeText(value)
                .then(() => {
                    setCopied(true)
                    setTimeout(() => setCopied(false), 1500)
                })
                .catch(() => {
                    // Clipboard access can be denied or unavailable; the ref
                    // text stays selectable on the page either way.
                })
        }}
    >{copied ? "Copied" : "Copy"}</button>
}
