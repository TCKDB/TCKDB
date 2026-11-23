import { useCallback, useEffect, useId, useRef, useState } from "react"
import type { FormEvent } from "react"
import "../auth.css"
import "../admin.css"
import "../structure-findings.css"
import { AuthApiError } from "../api/authApi"
import { CurationResponseError, appendStructureFinding, listStructureFindings } from "../api/curationApi"
import {
    FORM_ERROR,
    FINDING_KIND,
    FINDING_VERDICT,
    SOURCE_ROLE,
    appliedEffect,
    authorityWords,
    kindWords,
    refusalSentence,
    roleWords,
    scopeWords,
    verdictWords,
} from "../domain/curationWording"
import { useAnnounce } from "../hooks/useAnnounce"
import { useIsCurator } from "../hooks/useIsCurator"
import type { StructureEvidenceFinding } from "../types/structureEvidenceFinding"
import { DepositorNote } from "./DepositorNote"
import { SectionHeading } from "./PageSections"
import { RecordLink } from "./RecordLink"

/**
 * Structure-evidence findings about one record, for curators and admins: what has been ruled, which ruling corrects which,
 * and a form to record one. Renders nothing, and asks for nothing, for anyone else; the server enforces the role too.
 * `subjects` is every record the page can speak about (a transition state entry has several); one subject shows no chooser.
 */

export type FindingSubject = { ref: string; scope: "geometry" | "calculation" | "determination"; label: string }

const PAGE_SIZE = 20
const MAX_RATIONALE = 2000
const isoDate = (value: string) => value.slice(0, 10)

export function StructureFindingsPanel({ subjects, idBase }: { subjects: FindingSubject[]; idBase: string }) {
    const isCurator = useIsCurator()
    if (!isCurator || subjects.length === 0) return null
    return <FindingsSection subjects={subjects} idBase={idBase} />
}

function FindingsSection({ subjects, idBase }: { subjects: FindingSubject[]; idBase: string }) {
    const headingId = `findings-heading-${idBase}`
    const [subjectKey, setSubjectKey] = useState(`${subjects[0].scope}:${subjects[0].ref}`)
    const subject = subjects.find((s) => `${s.scope}:${s.ref}` === subjectKey) ?? subjects[0]
    const [findings, setFindings] = useState<StructureEvidenceFinding[]>([])
    const [loaded, setLoaded] = useState(false)
    const [hasMore, setHasMore] = useState(false)
    const [loadError, setLoadError] = useState<string | null>(null)
    const [reload, setReload] = useState(0)
    const [moreSkip, setMoreSkip] = useState<number | null>(null)
    const [loadingMore, setLoadingMore] = useState(false)
    const focusFrom = useRef<number | null>(null)
    const wantRef = useRef<string | null>(null)
    const announceList = useRef(false)
    const afterRetry = useRef(false)
    const resultRef = useRef<HTMLDivElement>(null)
    const retryRef = useRef<HTMLButtonElement>(null)
    const [retrying, setRetrying] = useState(false)
    const listRef = useRef<HTMLUListElement>(null)
    const controller = useRef<AbortController | null>(null)
    const announce = useAnnounce()

    useEffect(() => {
        controller.current?.abort()
        const abort = new AbortController()
        controller.current = abort
        const skip = moreSkip ?? 0
        const want = skip === 0 ? wantRef.current : null
        const load = async () => {
            let page = await listStructureFindings({ subjectRef: subject.ref, scope: subject.scope, skip, limit: PAGE_SIZE, signal: abort.signal })
            let all = page.findings
            // A newly recorded finding sits at the end of an oldest-first list; read on until it is on screen.
            while (want && page.findings.length === PAGE_SIZE && !all.some((f) => f.finding_ref === want) && all.length < PAGE_SIZE * 25) {
                page = await listStructureFindings({ subjectRef: subject.ref, scope: subject.scope, skip: all.length, limit: PAGE_SIZE, signal: abort.signal })
                all = [...all, ...page.findings]
            }
            return { all, more: page.findings.length === PAGE_SIZE }
        }
        load()
            .then(({ all, more }) => {
                if (abort.signal.aborted) return
                wantRef.current = null
                setFindings((previous) => (skip === 0 ? all : [...previous, ...all]))
                setHasMore(more)
                setLoadError(null)
                setLoaded(true)
                setLoadingMore(false)
                setRetrying(false)
                if (skip > 0 && all.length > 0) {
                    focusFrom.current = skip
                    announce(`${all.length} more findings shown.`)
                }
                if (announceList.current) {
                    announceList.current = false
                    announce(all.length === 0 ? `No findings have been recorded about this ${scopeWords(subject.scope)}.` : `${all.length}${more ? " or more" : ""} ${all.length === 1 ? "finding" : "findings"} about ${subject.label}.`)
                }
            })
            .catch((error: unknown) => {
                if (abort.signal.aborted) return
                wantRef.current = null
                setLoadError(refusalSentence(error instanceof AuthApiError ? error : {}))
                setLoaded(true)
                setLoadingMore(false)
                setRetrying(false)
            })
        return () => abort.abort()
    }, [subject.ref, subject.scope, subject.label, reload, moreSkip, announce])

    useEffect(() => {
        if (focusFrom.current === null) return
        listRef.current?.querySelectorAll<HTMLElement>("li.structure-finding")[focusFrom.current]?.focus()
        focusFrom.current = null
    }, [findings])

    useEffect(() => {
        if (!afterRetry.current || !loaded || retrying) return
        afterRetry.current = false
        if (loadError) {
            retryRef.current?.focus()
            announce(loadError)
        } else resultRef.current?.focus()
    }, [loaded, retrying, loadError, announce])

    function chooseSubject(key: string) {
        announceList.current = true
        setSubjectKey(key)
        setFindings([])
        setLoaded(false)
        setHasMore(false)
        setLoadError(null)
        setMoreSkip(null)
    }

    const refresh = useCallback((recordedRef?: string) => {
        wantRef.current = recordedRef ?? null
        setMoreSkip(null)
        setReload((n) => n + 1)
    }, [])

    const retry = useCallback(() => {
        if (retrying) return
        afterRetry.current = true
        setRetrying(true)
        refresh()
    }, [refresh, retrying])

    const loadedRefs = new Set(findings.map((f) => f.finding_ref))

    const inForce = findings.filter((f) => f.in_force)

    return (
        <section className="ledger-section structure-findings" aria-labelledby={headingId}>
            <SectionHeading id={headingId}>Structure findings</SectionHeading>
            <p className="note">Only curators and admins see this section. A finding is a curator&rsquo;s ruling on whether the evidence behind a structure holds. Findings are never edited; a correction is a later adjudication.</p>

            {subjects.length > 1 && (
                <div className="auth-field structure-findings-subject">
                    <label htmlFor={`findings-subject-${idBase}`}>Findings about</label>
                    <select id={`findings-subject-${idBase}`} className="admin-role-select" value={subjectKey} onChange={(e) => chooseSubject(e.target.value)}>
                        {subjects.map((s) => <option key={`${s.scope}:${s.ref}`} value={`${s.scope}:${s.ref}`}>{s.label}</option>)}
                    </select>
                    <p className="auth-field-hint">{subject.label}</p>
                </div>
            )}

            {(!loaded || retrying) && !loadError && <p className="note">Loading findings…</p>}
            {loadError && (
                <div className="structure-findings-error">
                    <p className="auth-error" role="alert">{loadError}</p>
                    <button type="button" className="admin-role-select" ref={retryRef} aria-disabled={retrying} aria-busy={retrying} onClick={retry}>
                        {retrying ? "Retrying…" : "Retry"}
                    </button>
                </div>
            )}
            <div className="structure-findings-result" ref={resultRef} tabIndex={-1}>
                {loaded && !loadError && findings.length === 0 && (
                    <p className="empty-projection">No findings have been recorded about this {scopeWords(subject.scope)}.</p>
                )}
                {findings.length > 0 && (
                    <ul className="structure-findings-list" ref={listRef}>
                        {findings.map((finding) => <FindingItem key={finding.finding_ref} finding={finding} loadedRefs={loadedRefs} />)}
                    </ul>
                )}
            </div>
            {hasMore && !loadError && (
                <button
                    type="button"
                    className="admin-role-select structure-findings-more"
                    aria-busy={loadingMore}
                    onClick={() => {
                        if (loadingMore) return
                        setLoadingMore(true)
                        setMoreSkip(findings.length)
                    }}
                >
                    {loadingMore ? "Loading more findings…" : "Show more"}
                </button>
            )}

            <FindingForm key={subjectKey} subject={subject} hasMore={hasMore} inForce={inForce} listState={loadError ? "error" : loaded ? "ready" : "loading"} idBase={idBase} onRecorded={(created) => refresh(created)} />
        </section>
    )
}

function FindingRef({ to: target, loadedRefs }: { to: string; loadedRefs: Set<string> }) {
    return loadedRefs.has(target) ? <a href={`#finding-${target}`}>{target}</a> : <code>{target}</code>
}

function FindingItem({ finding, loadedRefs }: { finding: StructureEvidenceFinding; loadedRefs: Set<string> }) {
    const effect = finding.in_force ? appliedEffect(finding.applied_kind, finding.applied_role, finding.verdict) : null
    const depositor = finding.authority === "producer_assertion"
    return (
        <li id={`finding-${finding.finding_ref}`} className="structure-finding" tabIndex={-1}>
            <p className="structure-finding-ref">Finding <code>{finding.finding_ref}</code></p>
            <p className="structure-finding-head">
                <span className="structure-finding-kind">{kindWords(finding.kind)}</span>
                {finding.role && <span> for the {roleWords(finding.role)} role</span>}
                <span className="structure-finding-verdict">{verdictWords(finding.verdict)}</span>
            </p>
            <p className="structure-finding-state">
                {finding.in_force
                    ? <span className="value-pill">In force</span>
                    : finding.settled_by_finding_ref
                        ? <span className="value-pill value-pill--muted">Settled by <FindingRef to={finding.settled_by_finding_ref} loadedRefs={loadedRefs} /></span>
                        : <span className="value-pill value-pill--muted">Not in force</span>}
                {finding.supersedes_finding_ref && (
                    <span>Corrects <FindingRef to={finding.supersedes_finding_ref} loadedRefs={loadedRefs} /></span>
                )}
                <span>{authorityWords(finding.authority)}</span>
                <span>Recorded {isoDate(finding.created_at)}</span>
            </p>
            {effect && <p className="note">{effect}</p>}
            {finding.source_calculation_ref && (
                <p className="note">
                    Evidence from <RecordLink kind="calculation" to={`/calculations/${finding.source_calculation_ref}`} label={finding.source_calculation_ref} />
                </p>
            )}
            {finding.literature_ref && <p className="note">Cited paper (reference {finding.literature_ref})</p>}
            {finding.removed_adjudications.map((removed) => (
                <p className="note structure-finding-removed" key={removed.finding_ref}>
                    The ruling {removed.finding_ref}, which settled this finding, was removed on {isoDate(removed.deleted_at)} when the
                    calculation {removed.deleted_calculation_ref} it relied on was deleted.{" "}
                    {finding.in_force
                        ? "This finding is in force again."
                        : `It has since been settled${finding.settled_by_finding_ref ? ` by ${finding.settled_by_finding_ref}` : ""}.`}
                </p>
            ))}
            <DepositorNote
                inline
                label={depositor ? "Depositor's rationale" : "Curator's rationale"}
                origin={depositor ? "Written by the depositor who made this assertion." : "Written by the curator who recorded this finding."}
            >
                {finding.rationale}
            </DepositorNote>
        </li>
    )
}

type Errors = Partial<Record<"role" | "verdict" | "target" | "rationale" | "source" | "literature", string>>

function FindingForm({ subject, hasMore, inForce, listState, idBase, onRecorded }: {
    subject: FindingSubject
    hasMore: boolean
    inForce: StructureEvidenceFinding[]
    listState: "loading" | "ready" | "error"
    idBase: string
    onRecorded: (findingRef?: string) => void
}) {
    const uid = useId()
    const announce = useAnnounce()
    const [kind, setKind] = useState("identity_incompatibility")
    const [role, setRole] = useState("")
    const [verdict, setVerdict] = useState("invalidates")
    const [target, setTarget] = useState("")
    const [rationale, setRationale] = useState("")
    const [source, setSource] = useState("")
    const [literature, setLiterature] = useState("")
    const [errors, setErrors] = useState<Errors>({})
    const [busy, setBusy] = useState(false)
    const [refusal, setRefusal] = useState<string | null>(null)
    const [recorded, setRecorded] = useState<string | null>(null)
    const statusRef = useRef<HTMLParagraphElement>(null)
    const formRef = useRef<HTMLFormElement>(null)
    const [focusInvalid, setFocusInvalid] = useState(0)

    useEffect(() => {
        if (focusInvalid > 0) formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
    }, [focusInvalid])

    useEffect(() => {
        if (recorded) statusRef.current?.focus()
    }, [recorded])

    const id = (name: string) => `${idBase}-${uid}-${name}`
    const fieldProps = (name: keyof Errors, hint?: string) => {
        const described = [hint ? id(hint) : null, errors[name] ? id(`${name}-error`) : null].filter(Boolean).join(" ")
        return {
            ...(errors[name] ? { "aria-invalid": true as const } : {}),
            ...(described ? { "aria-describedby": described } : {}),
        }
    }
    const fieldError = (name: keyof Errors) => errors[name] && <p className="auth-error structure-findings-field-error" id={id(`${name}-error`)}>{errors[name]}</p>

    function validate(): Errors {
        const found: Errors = {}
        const text = rationale.trim()
        if (kind === "role_invalidation" && !role) found.role = FORM_ERROR.role
        if (kind === "adjudication" && !target) found.target = FORM_ERROR.target
        if (kind !== "adjudication" && verdict === "does_not_invalidate") found.verdict = FORM_ERROR.dismissal
        if (!text) found.rationale = FORM_ERROR.rationale
        else if (text.length > MAX_RATIONALE) found.rationale = FORM_ERROR.rationaleLong
        if (source.trim() && !source.trim().startsWith("calc_")) found.source = FORM_ERROR.calculation
        if (literature.trim() && !literature.trim().startsWith("lit_")) found.literature = FORM_ERROR.literature
        return found
    }

    async function submit(event: FormEvent) {
        event.preventDefault()
        if (busy) return
        setRecorded(null)
        setRefusal(null)
        const found = validate()
        setErrors(found)
        const problems = Object.keys(found).length
        if (problems > 0) {
            announce(problems === 1 ? "One field needs fixing." : `${problems} fields need fixing.`)
            setFocusInvalid((n) => n + 1)
            return
        }
        setBusy(true)
        try {
            const created = await appendStructureFinding({
                scope: subject.scope,
                subjectRef: subject.ref,
                kind,
                verdict,
                rationale,
                role: kind === "role_invalidation" ? role : "",
                sourceCalculationRef: source,
                literatureRef: literature,
                supersedesFindingRef: kind === "adjudication" ? target : "",
            })
            const message = `Finding ${created.finding_ref} recorded.`
            setRecorded(message)
            announce(message)
            setKind("identity_incompatibility")
            setRole("")
            setVerdict("invalidates")
            setTarget("")
            setRationale("")
            setSource("")
            setLiterature("")
            onRecorded(created.finding_ref)
        } catch (error) {
            if (error instanceof CurationResponseError) {
                setRecorded(null)
                setRefusal(error.message)
                onRecorded()
            } else {
                setRefusal(refusalSentence(error instanceof AuthApiError ? error : {}))
            }
        } finally {
            setBusy(false)
        }
    }

    return (
        <form ref={formRef} className="admin-resolve structure-findings-form" aria-label="Record a finding" onSubmit={(e) => void submit(e)} noValidate>
            <h3 className="t-heading-2">Record a finding</h3>
            <div className="auth-field">
                <label htmlFor={id("kind")}>Kind</label>
                <select id={id("kind")} className="admin-role-select" value={kind} onChange={(e) => setKind(e.target.value)}>
                    {Object.entries(FINDING_KIND).map(([token, label]) => <option key={token} value={token}>{label}</option>)}
                </select>
            </div>
            {kind === "role_invalidation" && (
                <div className="auth-field">
                    <label htmlFor={id("role")}>Role</label>
                    <select id={id("role")} className="admin-role-select" value={role} onChange={(e) => setRole(e.target.value)} required {...fieldProps("role")}>
                        <option value="">Choose a role</option>
                        {Object.entries(SOURCE_ROLE).map(([token, label]) => <option key={token} value={token}>{label}</option>)}
                    </select>
                    {fieldError("role")}
                </div>
            )}
            {kind === "adjudication" && (
                <div className="auth-field">
                    <label htmlFor={id("target")}>Corrects finding</label>
                    <select id={id("target")} className="admin-role-select" value={target} onChange={(e) => setTarget(e.target.value)} required {...fieldProps("target", hasMore && listState === "ready" ? "target-hint" : undefined)}>
                        <option value="">Choose a finding in force</option>
                        {inForce.map((f) => <option key={f.finding_ref} value={f.finding_ref}>{kindWords(f.kind)}, {isoDate(f.created_at)} ({f.finding_ref})</option>)}
                    </select>
                    {hasMore && listState === "ready" && <p className="auth-field-hint" id={id("target-hint")}>Only the findings shown above can be chosen. Use Show more first to load the rest.</p>}
                    {listState === "ready" && inForce.length === 0 && <p className="auth-field-hint">No finding about this {scopeWords(subject.scope)} is in force, so there is nothing to correct.</p>}
                    {listState === "error" && <p className="auth-field-hint">The findings could not be loaded, so the list above is incomplete. Use Retry above.</p>}
                    {fieldError("target")}
                </div>
            )}
            <div className="auth-field">
                <label htmlFor={id("verdict")}>Verdict</label>
                <select id={id("verdict")} className="admin-role-select" value={verdict} onChange={(e) => setVerdict(e.target.value)} {...fieldProps("verdict")}>
                    {Object.entries(FINDING_VERDICT).map(([token, label]) => <option key={token} value={token}>{label}</option>)}
                </select>
                {fieldError("verdict")}
            </div>
            <div className="auth-field">
                <label htmlFor={id("rationale")}>Rationale (required)</label>
                <textarea id={id("rationale")} aria-required="true" className="structure-findings-textarea" rows={4} value={rationale} onChange={(e) => setRationale(e.target.value)} {...fieldProps("rationale", "rationale-hint")} />
                <p className="auth-field-hint" id={id("rationale-hint")} aria-live="off">{rationale.trim().length} / {MAX_RATIONALE}</p>
                {fieldError("rationale")}
            </div>
            <div className="auth-field">
                <label htmlFor={id("source")}>Source calculation (optional)</label>
                <input id={id("source")} className="structure-findings-input" type="text" value={source} onChange={(e) => setSource(e.target.value)} autoComplete="off" spellCheck={false} {...fieldProps("source")} />
                {fieldError("source")}
            </div>
            <div className="auth-field">
                <label htmlFor={id("literature")}>Literature (optional)</label>
                <input id={id("literature")} className="structure-findings-input" type="text" value={literature} onChange={(e) => setLiterature(e.target.value)} autoComplete="off" spellCheck={false} {...fieldProps("literature", "literature-hint")} />
                <p className="auth-field-hint" id={id("literature-hint")}>The cited paper must have a DOI or an ISBN.</p>
                {fieldError("literature")}
            </div>
            <button type="submit" className="auth-submit" aria-disabled={busy} aria-busy={busy}>Record finding</button>
            {refusal && <p className="auth-error" role="alert">{refusal}</p>}
            {recorded && <p className="structure-findings-status" role="status" tabIndex={-1} ref={statusRef}>{recorded}</p>}
        </form>
    )
}
