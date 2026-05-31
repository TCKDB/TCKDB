import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { fetchMachineReviewInspection } from "../api/machineReviewInspection"
import {
    overallHighestSeverity,
    type MachineReviewStatus,
} from "../types/machineReviewInspection"

/**
 * Admin-only Submission Machine-Review Inspection panel.
 *
 * Renders the response of
 * ``GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection``.
 * This is a PRIVATE diagnostic view, not public trust / human review /
 * certification / moderation. Access is enforced by the backend
 * (``require_admin``); this page is expected to be mounted behind the admin
 * shell. See ``backend/docs/specs/machine_review_admin_ui_mock.md``.
 *
 * Future joins (submission status, uploader, human-review status, evidence
 * label/completeness, curator workflow_state) are intentionally NOT shown:
 * the endpoint does not return them and this slice adds no backend joins.
 */

// Status badge text + disclaimer. The disclaimers are deliberately surfaced in
// the UI (legend below the table), never docs-only, so an admin can never read
// a machine status as a human-review verdict.
const STATUS_META: Record<
    MachineReviewStatus,
    { label: string; color: string; disclaimer: string }
> = {
    not_run: {
        label: "not run",
        color: "#6b7280",
        disclaimer: "No mapped machine review for this record.",
    },
    machine_screened_pass: {
        label: "screened: pass",
        color: "#15803d",
        disclaimer: "Machine found no obvious issue; not human-approved.",
    },
    machine_screened_warning: {
        label: "screened: warning",
        color: "#b45309",
        disclaimer: "Advisory warning; not rejected.",
    },
    machine_screened_needs_attention: {
        label: "screened: needs attention",
        color: "#b91c1c",
        disclaimer: "Needs human attention; record is not hidden.",
    },
    machine_review_failed: {
        label: "review failed",
        color: "#6b7280",
        disclaimer: "Reviewer failed; not a record failure.",
    },
}

const OBSERVATION_PROMPTS = [
    "Was the finding useful?",
    "Was the status misleading?",
    "Was this a false positive?",
    "Was mapping correct?",
    "Should this be curator-facing?",
] as const

function StatusBadge({ status }: { status: MachineReviewStatus }) {
    const meta = STATUS_META[status]
    return (
        <span
            title={meta.disclaimer}
            style={{
                background: meta.color,
                color: "white",
                borderRadius: "4px",
                padding: "1px 6px",
                fontSize: "0.8rem",
                whiteSpace: "nowrap",
            }}
        >
            {meta.label}
        </span>
    )
}

function dash(value: unknown): string {
    return value === null || value === undefined || value === "" ? "—" : String(value)
}

function MachineReviewInspectionPage() {
    const [inputValue, setInputValue] = useState("")
    const [submissionId, setSubmissionId] = useState<number | null>(null)
    const [showRaw, setShowRaw] = useState(false)
    // Maintainer observations are LOCAL ONLY in this slice — not persisted, not
    // sent anywhere. They are a manual-evaluation scratchpad (see spec §9).
    const [checks, setChecks] = useState<Record<string, boolean>>({})
    const [note, setNote] = useState("")

    const query = useQuery({
        queryKey: ["machine-review-inspection", submissionId],
        queryFn: () => fetchMachineReviewInspection(submissionId as number),
        enabled: submissionId !== null,
    })

    function onSubmit(e: React.FormEvent) {
        e.preventDefault()
        const parsed = Number(inputValue)
        if (Number.isInteger(parsed) && parsed > 0) {
            setSubmissionId(parsed)
        }
    }

    const data = query.data

    return (
        <div style={{ maxWidth: "1000px" }}>
            <h2>Submission Machine-Review Inspection</h2>
            <p style={{ color: "#6b7280", marginTop: "-8px" }}>
                Admin-only diagnostic view. This is <strong>not</strong> public trust,
                human review, certification, or moderation.
            </p>

            <form onSubmit={onSubmit} style={{ marginBottom: "16px" }}>
                <label>
                    submission_id:{" "}
                    <input
                        type="number"
                        min={1}
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        placeholder="e.g. 123"
                    />
                </label>{" "}
                <button type="submit">Inspect</button>
            </form>

            {submissionId === null && <p>Enter a submission id to inspect.</p>}
            {query.isLoading && <p>Loading…</p>}
            {query.isError && (
                <p style={{ color: "#b91c1c" }}>
                    Failed to load inspection for submission {submissionId}:{" "}
                    {(query.error as Error).message}
                </p>
            )}

            {data && (
                <>
                    {/* Submission header */}
                    <section>
                        <h3>Submission header</h3>
                        <table>
                            <tbody>
                                <tr>
                                    <td>submission_id</td>
                                    <td>{data.submission_id}</td>
                                </tr>
                                <tr>
                                    <td>status</td>
                                    <td>— (not returned by endpoint)</td>
                                </tr>
                                <tr>
                                    <td>uploaded by</td>
                                    <td>— (not returned by endpoint)</td>
                                </tr>
                                <tr>
                                    <td>submitted at</td>
                                    <td>— (not returned by endpoint)</td>
                                </tr>
                                <tr>
                                    <td>record links / audit events</td>
                                    <td>— (not returned by endpoint)</td>
                                </tr>
                            </tbody>
                        </table>
                    </section>

                    {/* Run summary */}
                    <section>
                        <h3>Machine-review run summary</h3>
                        <p style={{ color: "#6b7280", marginTop: "-6px" }}>
                            Diagnostic only — describes how the projection landed, not a
                            verdict about the submission.
                        </p>
                        <ul>
                            <li>records with mapped findings: {data.record_summaries.length}</li>
                            <li>unmapped findings: {data.unmapped_findings_count}</li>
                            <li>mapping warnings: {data.mapping_warnings.length}</li>
                            <li>parse warnings: {data.parse_warnings.length}</li>
                            <li>
                                source audit events:{" "}
                                {data.source_audit_event_ids.length > 0
                                    ? data.source_audit_event_ids.join(", ")
                                    : "none"}
                            </li>
                            <li>
                                overall highest severity:{" "}
                                {dash(overallHighestSeverity(data))}
                            </li>
                        </ul>
                    </section>

                    {/* Record summaries table */}
                    <section>
                        <h3>Record summaries</h3>
                        {data.record_summaries.length === 0 ? (
                            <p>No records received mapped machine-review findings.</p>
                        ) : (
                            <table border={1} cellPadding={4} style={{ borderCollapse: "collapse" }}>
                                <thead>
                                    <tr>
                                        <th>record_type</th>
                                        <th>record_ref</th>
                                        <th>record_id</th>
                                        <th>status</th>
                                        <th>highest_severity</th>
                                        <th>findings_count</th>
                                        <th>model</th>
                                        <th>provider</th>
                                        <th>reviewed_at</th>
                                        <th>all_record_reviews_count</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.record_summaries.map((r, i) => (
                                        <tr key={`${r.record_type}:${r.record_ref ?? r.record_id ?? i}`}>
                                            <td>{r.record_type}</td>
                                            <td>{dash(r.record_ref)}</td>
                                            <td>{dash(r.record_id)}</td>
                                            <td>
                                                <StatusBadge status={r.latest_summary.status} />
                                            </td>
                                            <td>{dash(r.latest_summary.highest_severity)}</td>
                                            <td>{r.latest_summary.findings_count}</td>
                                            <td>{dash(r.latest_summary.model)}</td>
                                            <td>{dash(r.latest_summary.provider)}</td>
                                            <td>{dash(r.latest_summary.reviewed_at)}</td>
                                            <td>{r.all_record_reviews_count}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}

                        {/* Badge legend with disclaimers — visible, not docs-only. */}
                        <h4>Status legend</h4>
                        <ul>
                            {(Object.keys(STATUS_META) as MachineReviewStatus[]).map((s) => (
                                <li key={s}>
                                    <StatusBadge status={s} /> <code>{s}</code> —{" "}
                                    {STATUS_META[s].disclaimer}
                                </li>
                            ))}
                        </ul>
                    </section>

                    {/* Diagnostics */}
                    <section>
                        <h3>Diagnostics</h3>
                        <p style={{ color: "#6b7280", marginTop: "-6px" }}>
                            mapping warnings = projection/mapping problems · parse warnings =
                            provider/payload problems · unmapped findings do not apply to
                            records.
                        </p>
                        <h4>mapping warnings</h4>
                        {data.mapping_warnings.length === 0 ? (
                            <p>No mapping warnings.</p>
                        ) : (
                            <ul>
                                {data.mapping_warnings.map((w, i) => (
                                    <li key={i}>{w}</li>
                                ))}
                            </ul>
                        )}
                        <h4>parse warnings</h4>
                        {data.parse_warnings.length === 0 ? (
                            <p>No parse warnings.</p>
                        ) : (
                            <ul>
                                {data.parse_warnings.map((w, i) => (
                                    <li key={i}>{w}</li>
                                ))}
                            </ul>
                        )}
                        <p>unmapped findings (do not apply to records): {data.unmapped_findings_count}</p>
                        <p>
                            source audit events:{" "}
                            {data.source_audit_event_ids.length > 0
                                ? data.source_audit_event_ids.join(", ")
                                : "none"}
                        </p>
                    </section>

                    {/* Raw JSON drawer */}
                    <section>
                        <h3>Raw admin diagnostic response</h3>
                        <button type="button" onClick={() => setShowRaw((v) => !v)}>
                            {showRaw ? "Hide" : "Show"} raw JSON
                        </button>
                        {showRaw && (
                            <pre
                                data-testid="raw-json"
                                style={{
                                    background: "#f3f4f6",
                                    padding: "8px",
                                    overflowX: "auto",
                                }}
                            >
                                {JSON.stringify(data, null, 2)}
                            </pre>
                        )}
                    </section>

                    {/* Maintainer observations (local, non-persisted) */}
                    <section>
                        <h3>Maintainer observations</h3>
                        <p style={{ color: "#6b7280", marginTop: "-6px" }}>
                            Local scratchpad — <strong>not persisted</strong> and not sent
                            anywhere in this slice.
                        </p>
                        <ul style={{ listStyle: "none", paddingLeft: 0 }}>
                            {OBSERVATION_PROMPTS.map((prompt) => (
                                <li key={prompt}>
                                    <label>
                                        <input
                                            type="checkbox"
                                            checked={checks[prompt] ?? false}
                                            onChange={(e) =>
                                                setChecks((c) => ({
                                                    ...c,
                                                    [prompt]: e.target.checked,
                                                }))
                                            }
                                        />{" "}
                                        {prompt}
                                    </label>
                                </li>
                            ))}
                        </ul>
                        <textarea
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            placeholder="Free-text observation (not persisted)…"
                            rows={4}
                            style={{ width: "100%" }}
                        />
                    </section>
                </>
            )}
        </div>
    )
}

export default MachineReviewInspectionPage
