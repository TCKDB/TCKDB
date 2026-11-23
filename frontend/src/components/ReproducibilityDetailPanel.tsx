import { useCallback, useEffect, useRef, useState } from "react"
import "../auth.css"
import "../admin.css"
import "../trust-assessment.css"
import { AuthApiError } from "../api/authApi"
import { CurationResponseError, evaluateReproducibility, loadLatestReproducibility } from "../api/curationApi"
import { rubricLabel } from "../domain/madeUnder"
import { meaningOf, tokenWords } from "../domain/apiVocabulary"
import { LEVEL_ORDER, checkSentence, levelWords, refusalSentence, warningSentence } from "../domain/curationWording"
import { useAnnounce } from "../hooks/useAnnounce"
import type { ReproducibilityAssessment, ReproducibilityCheck, ReproducibilityWarning } from "../types/reproducibilityAssessment"

/**
 * The latest reproducibility assessment of one record, for curators and admins, with a button that evaluates it again.
 * Each evaluation appends a new assessment and never edits an earlier one. The row carries internal ids and an evidence
 * payload; only the named fields below are shown (grade, ref, date, rubric, check sentences, warning sentences with a
 * shortened checksum and the integrity event ref), so nothing else can reach the page.
 */

const isoDate = (value: string) => value.slice(0, 10)
const NOT_APPLICABLE = "not_applicable"

function ChecksByLevel({ checks, showLevel }: { checks: ReproducibilityCheck[]; showLevel: boolean }) {
    const rank = (level: string) => LEVEL_ORDER.indexOf(level)
    if (!showLevel) {
        return (
            <ul className="trust-checks">
                {[...checks].sort((a, b) => rank(a.level) - rank(b.level)).map((c) => (
                    <li key={c.name}>
                        {checkSentence(c.name)}
                        <span className="note"> {levelWords(c.level)}.</span>
                    </li>
                ))}
            </ul>
        )
    }
    const levels = [...new Set(checks.map((c) => c.level))].sort((a, b) => rank(a) - rank(b))
    return (
        <>
            {levels.map((level) => (
                <div className="repro-level" key={level}>
                    <p className="repro-level-name">{levelWords(level)}</p>
                    <ul className="trust-checks">
                        {checks.filter((c) => c.level === level).map((c) => <li key={c.name}>{checkSentence(c.name)}</li>)}
                    </ul>
                </div>
            ))}
        </>
    )
}

function warningDetail(warning: ReproducibilityWarning): string | null {
    const parts: string[] = []
    const sha = warning.evidence.sha256
    if (typeof sha === "string" && /^[0-9a-f]{64}$/.test(sha)) parts.push(`checksum ${sha.slice(0, 12)}`)
    const event = warning.evidence.integrity_event_ref
    if (typeof event === "string" && /^[A-Za-z0-9_]{1,64}$/.test(event)) parts.push(`integrity event ${event}`)
    return parts.length > 0 ? parts.join(", ") : null
}

export function ReproducibilityDetailPanel({ recordType, recordId, recordLabel, idBase }: {
    recordType: string
    recordId: number
    recordLabel: string
    idBase: string
}) {
    const announce = useAnnounce()
    const [assessment, setAssessment] = useState<ReproducibilityAssessment | null>(null)
    const [loaded, setLoaded] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [evaluating, setEvaluating] = useState(false)
    const mounted = useRef(true)
    const evaluatingRef = useRef(false)

    useEffect(() => {
        mounted.current = true
        const abort = new AbortController()
        loadLatestReproducibility(recordType, recordId, abort.signal)
            .then((row) => {
                if (abort.signal.aborted) return
                setAssessment(row)
                setError(null)
                setLoaded(true)
            })
            .catch((e: unknown) => {
                if (abort.signal.aborted) return
                setError(refusalSentence(e instanceof AuthApiError ? e : {}))
                setLoaded(true)
            })
        return () => {
            mounted.current = false
            abort.abort()
        }
    }, [recordType, recordId])

    const evaluate = useCallback(async () => {
        if (evaluatingRef.current) return
        evaluatingRef.current = true
        setEvaluating(true)
        setError(null)
        let saved = false
        try {
            const created = await evaluateReproducibility(recordType, recordId)
            saved = true
            if (!mounted.current) return
            setAssessment(created)
            const latest = await loadLatestReproducibility(recordType, recordId)
            if (!mounted.current) return
            setAssessment(latest)
            announce("Reproducibility assessment updated.")
        } catch (e) {
            if (!mounted.current) return
            if (saved) {
                setError("The assessment was saved, but the latest one could not be reloaded. Reload the page to see it.")
            } else if (e instanceof CurationResponseError) {
                setError(e.message)
            } else {
                setError(refusalSentence(e instanceof AuthApiError ? e : {}))
            }
        } finally {
            evaluatingRef.current = false
            if (mounted.current) setEvaluating(false)
        }
    }, [recordType, recordId, announce])

    const headingId = `repro-heading-${idBase}`
    const passed = assessment?.passed.filter((c) => c.outcome !== NOT_APPLICABLE) ?? []
    const notApplicable = assessment?.passed.filter((c) => c.outcome === NOT_APPLICABLE) ?? []
    const missing = assessment?.missing ?? []
    const warnings = assessment?.warnings ?? []

    return (
        <section className="repro-panel" aria-label={`Reproducibility of ${recordLabel}`} data-component="reproducibility-detail">
            <h3 className="repro-heading" id={headingId}>Reproducibility</h3>
            {!loaded && <p className="note">Loading the latest assessment…</p>}
            {loaded && !assessment && !error && <p className="note">Not assessed yet.</p>}
            {assessment && (
                <>
                    <p className="trust-assessment-line">
                        <span className="value-pill">{tokenWords(assessment.grade)}</span>
                        {meaningOf("reproducibilityGrade", assessment.grade) && <span className="note">{meaningOf("reproducibilityGrade", assessment.grade)}</span>}
                    </p>
                    <p className="note">
                        Assessment {assessment.assessment_ref}. Assessed {isoDate(assessment.assessed_at)}, {assessment.assessor_kind === "curator" ? "by a curator" : "by TCKDB's rubric"}.
                    </p>
                    <p className="note">Rubric {rubricLabel(assessment.rubric_name)}, version {assessment.rubric_version}.</p>

                    {warnings.length > 0 && (
                        <div role="group" aria-label="Warnings" className="repro-group">
                            <h4 className="repro-group-title">Warnings</h4>
                            <ul className="trust-checks">
                                {warnings.map((w, index) => {
                                    const detail = warningDetail(w)
                                    return <li key={`${w.code}-${index}`}>{warningSentence(w.code)}{detail && <span className="note"> ({detail})</span>}</li>
                                })}
                            </ul>
                        </div>
                    )}
                    <div role="group" aria-label="Missing" className="repro-group">
                        <h4 className="repro-group-title">Missing</h4>
                        {missing.length > 0 ? <ChecksByLevel checks={missing} showLevel /> : <p className="note">Nothing is missing.</p>}
                    </div>
                    <div role="group" aria-label="Passed" className="repro-group">
                        <h4 className="repro-group-title">Passed</h4>
                        {passed.length > 0 ? <ChecksByLevel checks={passed} showLevel={false} /> : <p className="note">No check has passed.</p>}
                    </div>
                    {notApplicable.length > 0 && (
                        <div role="group" aria-label="Does not apply" className="repro-group">
                            <h4 className="repro-group-title">Does not apply</h4>
                            <ChecksByLevel checks={notApplicable} showLevel={false} />
                        </div>
                    )}
                </>
            )}
            {error && <p className="auth-error" role="alert">{error}</p>}
            {loaded && (
                <div className="repro-actions">
                    <button type="button" className="auth-submit" aria-disabled={evaluating} aria-busy={evaluating} onClick={() => void evaluate()}>
                        {assessment ? "Evaluate again" : "Evaluate"}
                    </button>
                    <p className="auth-field-hint">Each evaluation adds a new assessment and never edits an earlier one. It can take a moment when the record has stored files to check.</p>
                    <p className="note" role="status">{evaluating ? "Evaluating…" : ""}</p>
                </div>
            )}
        </section>
    )
}
