import { Link } from "react-router-dom"
import type { EffectiveSetting, RoleCalculationSettings } from "../api/effectiveSettingsSchema"
import {
    absentReasonText,
    describeSetting,
    LEVEL_ROLES,
    levelRoleLabel,
    type SettingDisplay,
} from "../domain/effectiveSettings"
import "../settings-evidence.css"

// ---------------------------------------------------------------------------
// What a calculation used for its DFT grid and SCF convergence, and how the archive knows. Two renderings of one
// description (`domain/effectiveSettings.ts`):
//
// - `EffectiveSettingsSummary`: the calculation page, above its "Parsed parameters" table.
// - `SettingsBehindValue`: a thermo or statmech record, one row per level (geometry / frequency / energy) with the
//   calculation the record links for it.
//
// An assumed value is the program's documented default, not something anyone observed, so it is drawn differently from
// every observation: dashed outline, italic value, the word "assumed" in the label. Colour is never the only signal.
// Every string comes from a closed table; no depositor-typed text is printed.
// ---------------------------------------------------------------------------

function StateBadge({ display }: { display: SettingDisplay }) {
    return (
        <span
            className={`settings-state settings-state--${display.conflicting ? "conflict" : display.kind}`}
            data-settings-state={display.kind}
            title={display.stateMeaning}
            {...(display.conflicting ? { "data-settings-conflict": "" } : {})}
        >
            {display.stateLabel}
        </span>
    )
}

/** The value and its evidence for one setting, as stacked lines (used in table cells and the summary). */
function SettingCell({ summary }: { summary: EffectiveSetting | undefined }) {
    // A setting the server did not list is simply not stated; it is never filled from anywhere else.
    const display = describeSetting(summary ?? { setting: "", state: "not_stated" })
    return (
        <div className="settings-cell">
            {display.valueText && (
                <span
                    className={
                        display.valueIsPlaceholder
                            ? "settings-placeholder"
                            : display.assumed
                              ? "settings-value settings-value--assumed"
                              : "settings-value"
                    }
                >
                    {display.valueText}
                </span>
            )}
            {display.reportedTexts.length > 0 && (
                <span className="settings-reported">{display.reportedTexts.join("; ")}</span>
            )}
            <span className="settings-badges">
                <StateBadge display={display} />
                {display.sourceText && <span className="settings-source">{display.sourceText}</span>}
            </span>
        </div>
    )
}

// The calculation's own type, from a closed list (a type outside it is not printed).
const CALCULATION_TYPE_TAGS = new Set(["opt", "freq", "sp", "composite", "scan", "irc", "neb", "imported"])

function calculationTypeTag(type: string | null | undefined): string | null {
    return type && CALCULATION_TYPE_TAGS.has(type) ? type : null
}

/** What the short pill labels mean. Assumed values are computed when the page is read and never stored. */
function SettingsLegend() {
    return (
        <p className="note">
            Reported: the output log prints the value the program used. Requested: the input asked for it. Assumed
            default: the program&apos;s documented default, shown when nothing reports or requests a value (computed
            when the page is read, never stored, and not an observation). Not stated: nothing says.
        </p>
    )
}

const SETTING_ORDER = ["grid", "scf_convergence"] as const

function byName(settings: readonly EffectiveSetting[] | null | undefined, name: string): EffectiveSetting | undefined {
    return settings?.find((item) => item.setting === name)
}

/** The calculation page's summary of what was used for the grid and the SCF convergence. */
export function EffectiveSettingsSummary({ settings }: { settings: readonly EffectiveSetting[] | null | undefined }) {
    if (!settings || settings.length === 0) return null
    return (
        <div className="settings-summary">
            <h4 className="model-block-heading">What was used</h4>
            <div className="table-scroll table-scroll--compact">
                <table className="data-table" aria-label="Effective settings">
                    <thead><tr><th scope="col">Setting</th><th scope="col">Value and evidence</th></tr></thead>
                    <tbody>
                        {SETTING_ORDER.map((name) => {
                            const summary = byName(settings, name)
                            if (!summary) return null
                            const display = describeSetting(summary)
                            return (
                                <tr key={name}>
                                    <td data-label="Setting">{display.settingLabel}</td>
                                    <td data-label="Value and evidence"><SettingCell summary={summary} /></td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
            <SettingsLegend />
        </div>
    )
}

/**
 * The settings behind a thermochemistry or statmech record: one row per level (geometry, frequencies, energy) with the
 * calculation THIS record links for it and that calculation's grid and SCF convergence. A level with no linked
 * calculation says so; it is never filled from another record.
 */
export function SettingsBehindValue({ block, idSuffix = "", recordRef }: {
    block: readonly RoleCalculationSettings[] | null | undefined
    recordRef: string
    idSuffix?: string
}) {
    if (!block || block.length === 0) return null
    const headingId = `settings-${recordRef}${idSuffix}`
    return (
        <section aria-labelledby={headingId} className="settings-behind">
            <h4 className="model-block-heading" id={headingId}>Settings behind this value</h4>
            <div className="table-scroll table-scroll--compact">
                <table className="data-table" aria-label="Settings behind this value">
                    <thead>
                        <tr>
                            <th scope="col">Level</th>
                            <th scope="col">Calculation</th>
                            <th scope="col">DFT grid</th>
                            <th scope="col">SCF convergence</th>
                        </tr>
                    </thead>
                    <tbody>
                        {LEVEL_ROLES.map((role) => block.find((item) => item.role === role) ?? { role, absent_reason: "not_shown" }).map((item) => (
                            <tr key={item.role} data-level-role={item.role}>
                                <td data-label="Level">{levelRoleLabel(item.role)}</td>
                                {item.calculation_ref ? (
                                    <>
                                        <td data-label="Calculation">
                                            <Link className="data" to={`/calculations/${item.calculation_ref}`}>{item.calculation_ref}</Link>
                                            {calculationTypeTag(item.calculation_type) && (
                                                <span className="settings-calc-type">{calculationTypeTag(item.calculation_type)}</span>
                                            )}
                                        </td>
                                        <td data-label="DFT grid"><SettingCell summary={byName(item.settings, "grid")} /></td>
                                        <td data-label="SCF convergence"><SettingCell summary={byName(item.settings, "scf_convergence")} /></td>
                                    </>
                                ) : (
                                    <td colSpan={3} data-label="Calculation" className="settings-absent">{absentReasonText(item.absent_reason)}</td>
                                )}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="note">Each row is the calculation this record links for that level.</p>
            <SettingsLegend />
        </section>
    )
}
