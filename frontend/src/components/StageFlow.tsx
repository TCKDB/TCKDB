import { Fragment, type ReactNode } from "react"
import "../calculation-detail.css"

/**
 * A generic left-to-right (column, at narrow widths) chain of boxes joined
 * by arrow connectors -- the SAME markup/class shape
 * `CalculationDetailPage.tsx`'s own `OptimisationStageStrip` renders
 * (`.opt-stage-flow` / `.opt-stage-box` / `.opt-stage-connector`,
 * `calculation-detail.css`, whose `.opt-stage-*` rules are unscoped, not
 * nested under `.calc-page` -- confirmed by grep before reusing them here),
 * extracted here so a second caller with a
 * DIFFERENT stage vocabulary -- `ReactionTransitionStatesSection.tsx`'s
 * guess -> optimized -> validated entry-status strip, three boxes instead
 * of that page's two -- reuses the same primitive instead of hand-rolling
 * a near-duplicate. `CalculationDetailPage.tsx` itself is untouched: this
 * is a new, independent consumer of the existing CSS classes, not a
 * refactor of that page's own component.
 */
export interface StageFlowBox {
    key: string
    label: string
    content: ReactNode
    selected: boolean
    testId?: string
}

export function StageFlow({ boxes, ariaLabel, testId = "stage-flow" }: { boxes: StageFlowBox[]; ariaLabel?: string; testId?: string }) {
    return (
        <div className="opt-stage-flow" data-testid={testId} aria-label={ariaLabel}>
            {boxes.map((box, index) => (
                <Fragment key={box.key}>
                    {index > 0 && <span className="opt-stage-connector" aria-hidden="true">→</span>}
                    <div
                        className={`card opt-stage-box${box.selected ? " card--selected" : ""}`}
                        data-testid={box.testId}
                    >
                        <span className="opt-stage-box-label">{box.label}</span>
                        {box.content}
                    </div>
                </Fragment>
            ))}
        </div>
    )
}
