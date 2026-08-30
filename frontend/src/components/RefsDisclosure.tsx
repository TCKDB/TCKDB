import { useState, type CSSProperties } from "react"
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

// Visually-hidden but still in the accessibility tree — the exact
// clip-rect technique `conformer-group.css`'s mobile `.stage-table thead`
// rule already uses elsewhere in this app. Defined as an inline style,
// not a CSS class: `RefsDisclosure.tsx` has no stylesheet of its own to
// put a class in, and its two real-world consumers load different,
// mutually off-limits stylesheets (`species-entry.css` for the entry
// page; `geometry-detail.css`, scoped to `.geometry-page`, for the
// geometry page — see the `data-copy-unavailable` note below) — an
// inline style is the one styling surface guaranteed to apply on both
// without either page having to add a rule for a class it does not know
// about.
const VISUALLY_HIDDEN_STYLE: CSSProperties = {
    position: "absolute",
    width: "1px",
    height: "1px",
    padding: 0,
    margin: "-1px",
    overflow: "hidden",
    clip: "rect(0, 0, 0, 0)",
    whiteSpace: "nowrap",
    border: 0,
}

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
 * denied) now flip to an "unavailable" state.
 *
 * That state is announced through a SEPARATE `role="status"` region, not
 * by rewriting `aria-label` per state. `aria-label` overrides an
 * element's accessible name outright — MEASURED, an earlier version of
 * this component set `aria-label` to `Copy ${label} ${srLabel}` with no
 * state in it, so the name read "Copy SMILES reference" in idle,
 * "Copied", AND "Unavailable" alike: a screen-reader user got no signal
 * a click did anything, let alone that it failed. "Unavailable" is a
 * FAILURE notice — shipping it silent is worse than the no-op it
 * replaced, which is exactly what this diff's own history says it set
 * out to fix. The house rule this follows (see `LazySection` in
 * `pages/CalculationDetailPage.tsx`): a `role="status"`/`aria-live`
 * region holds a short MESSAGE, never a payload — "Copied" or "Copy
 * unavailable", never the copied value itself, so assistive tech is not
 * made to read out (`role="status"` is `aria-atomic`) a blob of text the
 * user did not ask to hear. `aria-label` itself stays fixed on purpose:
 * rewriting an element's own name out from under a screen-reader user
 * mid-interaction is its own accessibility hazard (VoiceOver/NVDA do not
 * reliably re-announce a name change the way they do a live region), so
 * "what does this button do" and "what just happened" are kept as two
 * separate answers rather than overloading one string with both.
 */
export function CopyButton({ value, label, srLabel = "reference" }: { value: string; label: string; srLabel?: string }) {
    const [state, setState] = useState<CopyState>("idle")
    const statusMessage = state === "copied" ? "Copied" : state === "unavailable" ? "Copy unavailable" : ""
    return <>
        <button
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
        <span role="status" style={VISUALLY_HIDDEN_STYLE}>{statusMessage}</span>
    </>
}

// FLAGGED GAP, not fixed here (out of scope on this branch — see the
// worktree brief): `[data-copy-unavailable="true"]` has a rule only in
// `geometry-detail.css:83` (`.geometry-page .copy-button[...]`, a warm
// amber `#a15c00`), a stylesheet the species-entry page never loads.
// MEASURED: the SAME "unavailable" state renders amber on the geometry
// page and idle grey (no visual change at all) on the entry page — this
// shared component grew a state one of its two consumers cannot render.
// `species-entry.css` is owned by a parallel branch
// (`frontend/evidence-in-prose`) and is off-limits here; route this gap
// there rather than editing it from this branch.
