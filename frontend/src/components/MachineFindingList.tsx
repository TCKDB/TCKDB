import { Link } from "react-router-dom"
import { wordsFor } from "../domain/machineReviewWords"
import { severityClass } from "../types/curatorTask"
import { NewTabNote } from "./RecordLocationCell"

export type FindingView = {
    severity: string
    category: string
    message: string
    evidence_keys: string[]
    recommended_action: string | null
    curator_task_id?: number | null
}

/**
 * The findings of one review. Everything the reviewer wrote is quoted and labelled as the reviewer's, so none of it reads
 * as this archive's own statement. Severity is a word as well as a class.
 *
 * `taskHref` turns a finding's curator task into a link; leave it out where the viewer has no queue to open.
 */
export function MachineFindingList({
    findings,
    taskHref,
}: {
    findings: FindingView[]
    taskHref?: (taskId: number) => string
}) {
    if (findings.length === 0) return <p className="admin-hint">This review raised no findings.</p>
    return (
        <ul className="admin-findings">
            {findings.map((f, i) => {
                const severity = wordsFor("severity", f.severity)
                const category = wordsFor("category", f.category)
                return (
                    <li key={i}>
                        <p>
                            <span className={severityClass(f.severity) ?? undefined}>
                                {severity.known ? severity.label : <code>{f.severity}</code>}
                            </span>{" "}
                            · {category.known ? category.label : <code>{f.category}</code>}
                        </p>
                        <p className="admin-hint">The reviewer wrote:</p>
                        <blockquote>{f.message}</blockquote>
                        {f.evidence_keys.length > 0 && (
                            <p>
                                Evidence:{" "}
                                {f.evidence_keys.map((key, k) => (
                                    <span key={key}>
                                        {k > 0 ? ", " : ""}
                                        <code>{key}</code>
                                    </span>
                                ))}
                            </p>
                        )}
                        {f.recommended_action !== null && (
                            <p>The reviewer suggests: {f.recommended_action}</p>
                        )}
                        {taskHref !== undefined && f.curator_task_id != null && (
                            <p>
                                <Link to={taskHref(f.curator_task_id)} target="_blank" rel="noopener noreferrer">
                                    Open the curator task for this finding
                                    <NewTabNote />
                                </Link>
                            </p>
                        )}
                    </li>
                )
            })}
        </ul>
    )
}
