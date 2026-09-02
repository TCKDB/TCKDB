import { useState } from "react"
import "../energy-display.css"
import {
    ENERGY_DISPLAY_UNITS,
    energyUnitLabel,
    formatEnergyForDisplay,
    type EnergyDisplayUnit,
} from "../domain/energyUnits"

/**
 * A hartree-valued electronic energy, shown with a unit toggle -- the
 * energy-page equivalent of `GeometryDetailPage`'s Å/bohr toggle and
 * `ThermoCpChart`'s J/cal toggle (see `domain/energyUnits.ts`'s module
 * docstring for the shared rule the three follow).
 *
 * `valueHartree` is always the archive's own stored number. Switching
 * the unit never re-derives from a previously-converted value -- every
 * render recomputes straight from `valueHartree` via
 * `formatEnergyForDisplay`, so there is nothing here that can
 * accumulate rounding error across toggles, and switching away and back
 * to hartree always reproduces the exact same formatted string.
 *
 * `size` controls only the CSS class applied to the value -- "headline"
 * is for the calculation page's promoted answer (largest weight on the
 * page); "inline" is for a value shown alongside other evidence (e.g.
 * a per-observation single-point row on the species entry page).
 *
 * This used to end with a fixed sentence ("Always stored in hartree; the
 * other units here are display conversions only, computed directly from
 * that stored value and never from one another.") on the SP tab -- removed
 * because the interface already demonstrates it: every displayed value
 * carries its unit (`formatEnergyForDisplay` has no value-only mode, see
 * `energyUnits.ts`/`energyUnits.test.ts`, which this component still
 * relies on unchanged), and the toggle's `aria-pressed` state names which
 * unit is active. The GUARANTEE the sentence described is unchanged, only
 * the restating of it in prose is gone.
 */
export function EnergyDisplay({ valueHartree, label, size = "inline" }: {
    valueHartree: number | null | undefined
    label?: string
    size?: "headline" | "inline"
}) {
    const [unit, setUnit] = useState<EnergyDisplayUnit>("hartree")

    if (valueHartree === null || valueHartree === undefined) {
        return <span className="energy-display-absent">not recorded</span>
    }

    const formatted = formatEnergyForDisplay(valueHartree, unit)

    return (
        <div className={size === "headline" ? "energy-display energy-display--headline" : "energy-display"}>
            {label && <span className="energy-display-label">{label}</span>}
            {/* The unit is always part of this string (see
                `formatEnergyForDisplay`) -- there is no code path here that
                renders the bare number without it. */}
            <span
                className={size === "headline" ? "energy-display-value energy-display-value--headline" : "energy-display-value"}
                data-testid="energy-display-value"
            >
                {formatted}
            </span>
            <fieldset className="energy-toggle">
                <legend>Units</legend>
                {ENERGY_DISPLAY_UNITS.map((candidate) => (
                    <button
                        key={candidate}
                        type="button"
                        aria-pressed={unit === candidate}
                        onClick={() => setUnit(candidate)}
                    >
                        {energyUnitLabel(candidate)}
                    </button>
                ))}
            </fieldset>
        </div>
    )
}
