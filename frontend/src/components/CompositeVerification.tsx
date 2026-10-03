import "../composite-verification.css"
import type { CompositeEnergyVerification } from "../api/scientificSchemas"
import { verificationView } from "../domain/compositeVerification"

/**
 * How far a composite energy has been checked (ADR 0021, P7a): one badge,
 * shared by every record page's Energy fact and the composite calculation
 * page's result, so the same verification reads identically everywhere.
 * All wording and the contradiction rule live in
 * `domain/compositeVerification.ts`; this only draws them.
 *
 * - confirmed states: the accent `.value-pill`;
 * - neutral states (reported by the program, cannot be checked): the muted
 *   pill;
 * - a CONTRADICTION: a bordered warning block that says "Contradiction"
 *   before the headline. Never a pill, never muted.
 *
 * `difference_hartree`/`tolerance_hartree` are shown only when the server
 * sent them (an absent figure is not printed as zero).
 */
export function CompositeVerificationBadge({ verification }: { verification: CompositeEnergyVerification }) {
    const view = verificationView(verification)
    const figures = view.difference !== null
        ? `Difference ${view.difference}${view.tolerance !== null ? `, tolerance ${view.tolerance}` : ""}`
        : null
    const contradiction = view.tone === "contradiction"
    return (
        <div
            className={`composite-verification${contradiction ? " composite-verification--contradiction" : ""}`}
            data-verification-state={view.state}
            data-verification-tone={view.tone}
        >
            {contradiction ? (
                <>
                    <span className="composite-verification-lead">Contradiction</span>
                    <span className="composite-verification-headline">{view.headline}</span>
                </>
            ) : (
                <span className={view.tone === "confirmed" ? "value-pill" : "value-pill value-pill--muted"}>{view.headline}</span>
            )}
            {view.detail && <div className="note">{view.detail}</div>}
            {figures && <p className="composite-verification-figures">{figures}</p>}
        </div>
    )
}

/** Beside a headline energy whose verification is a contradiction: a short warning that links to the
 *  verification block on the same page, so the number is never read without the evidence against it. */
export function ContradictionMarker({ targetId }: { targetId: string }) {
    return (
        <a className="composite-verification-marker" href={`#${targetId}`} data-contradiction-marker="">
            Contradiction: see verification
        </a>
    )
}

/** The badge for a record's energy level: shown whenever the server sent a
 *  verification, and, when the energy is a composite but the server sent
 *  none, an honest muted "not recorded" rather than silence. Nothing at all
 *  for a non-composite energy. */
export function EnergyVerification({ energySource, verification }: {
    energySource: string | null | undefined
    verification: CompositeEnergyVerification | null | undefined
}) {
    if (verification) return <CompositeVerificationBadge verification={verification} />
    if (energySource === "composite") {
        return <div className="composite-verification"><span className="value-pill value-pill--muted">Verification not recorded</span></div>
    }
    return null
}
