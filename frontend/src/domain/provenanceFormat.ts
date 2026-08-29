/**
 * Provenance-label formatting rules, ported rule-for-rule from
 * `backend/app/api/landing.py` (`softwareLabel`, `words`) — see
 * `chemistryFormat.ts` for why this is a port of behaviour rather than of
 * code shared across a Python/JS boundary that cannot literally share code.
 */

/**
 * A software release prints its own name in some deployments ("Gaussian
 * 16") and not in others ("16"). Concatenating unconditionally gives the
 * second case a stutter ("Gaussian Gaussian 16") — the exact live defect
 * this brief was written against (`CalculationDetailPage.tsx:239,244`) —
 * so the name is prepended only when the version does not already open
 * with it. Ported from `landing.py:2218-2230`.
 */
export function softwareLabel(release: { software?: string | null; version?: string | null } | null | undefined): string | null {
    if (!release) return null
    const name = release.software
    const version = release.version
    if (!name) return version || null
    if (!version) return name
    return version.indexOf(name) === 0 ? version : `${name} ${version}`
}

/**
 * A workflow tool release is the same `{name, version}` pair under two
 * different key names, so it borrows `softwareLabel` rather than repeating
 * it and drifting from it. Ported from `landing.py`'s `toolRelease`.
 */
export function toolReleaseLabel(release: { workflow_tool?: string | null; version?: string | null } | null | undefined): string | null {
    if (!release) return null
    return softwareLabel({ software: release.workflow_tool, version: release.version })
}

/**
 * Anything else the API spells with underscores. This is a transcription,
 * never a translation: `asymmetric_top` becomes "asymmetric top" and stops
 * there. Inventing an expansion for a token this function has never seen
 * would be inventing chemistry -- the enum->prose tables (`CALCULATION_WORDS`
 * etc.) stay separate and page-specific for exactly that reason. Ported
 * from `landing.py:2206-2209`.
 */
export function words(token: string | null | undefined): string | null {
    if (token === null || token === undefined || token === "") return null
    return String(token).replaceAll("_", " ")
}
