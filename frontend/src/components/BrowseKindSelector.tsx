import { BROWSE_KINDS, BROWSE_KIND_LABELS } from "../api/browseApi"
import type { BrowseKind } from "../api/browseApi"

/**
 * A native radiogroup, not a button row -- `role="radio"`/ARIA on plain
 * `<button>`s would just re-implement what `<input type="radio">` already
 * gives for free (arrow-key movement, a single tab stop, `aria-checked`
 * wired by the browser). Selecting a different kind NAVIGATES `BrowsePage`
 * to that kind's own path (`BROWSE_KIND_PATHS`, `api/browseApi.ts`) --
 * each browse kind is its own URL now, not a `?kind=` query parameter on
 * one shared URL.
 */
export function BrowseKindSelector({ kind, onSelect }: { kind: BrowseKind; onSelect: (kind: BrowseKind) => void }) {
    return (
        <fieldset className="browse-kind-selector">
            <legend>What to browse</legend>
            <div className="browse-kind-options">
                {BROWSE_KINDS.map((option) => (
                    <label className="browse-kind-option" data-selected={option === kind} key={option}>
                        <input
                            checked={option === kind}
                            name="browse-kind"
                            onChange={() => onSelect(option)}
                            type="radio"
                            value={option}
                        />
                        {BROWSE_KIND_LABELS[option]}
                    </label>
                ))}
            </div>
        </fieldset>
    )
}
