import { useCallback, useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link, Navigate } from "react-router-dom"
import "../auth.css"
import { AuthApiError, createApiKey, listApiKeys, revokeApiKey } from "../api/authApi"
import { CopyButton } from "../components/RefsDisclosure"
import "../refs-disclosure.css"
import { useAuth } from "../hooks/useAuth"
import type { ApiKeyCreateResponse, ApiKeyMetadata } from "../types/auth"

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "never")

/** Strips the plaintext `key` field before the result is stored anywhere
 * other than the one-time `revealedKey` state -- see this file's own
 * docstring on why `keysState` must never receive it. */
function toMetadata(created: ApiKeyCreateResponse): ApiKeyMetadata {
    return {
        id: created.id,
        label: created.label,
        created_at: created.created_at,
        last_used_at: created.last_used_at,
        revoked_at: created.revoked_at,
    }
}

type KeysState =
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; keys: ApiKeyMetadata[] }

/**
 * `/account` -- the one page this PR adds behind a "you need a session"
 * gate (never an existing page, per the brief: reads stay public
 * everywhere else). Lists the signed-in user's own API keys, with create
 * and revoke.
 *
 * The plaintext key `POST /auth/api-keys` returns is shown exactly once
 * (`backend/app/api/routes/auth.py`'s own docstring) and this component
 * treats that literally: `revealedKey` is local `useState`, never written
 * to `localStorage`/`sessionStorage`/any persistence, and is cleared
 * on every subsequent list re-fetch (`loadKeys`) -- a revoke, a manual
 * refresh, or simply mounting again after navigating away and back all
 * go through `loadKeys`, so the plaintext cannot survive any of them.
 * `keysState` itself never receives the raw `key` field at all: the
 * metadata appended locally after a successful create strips it before
 * the value ever reaches the array the table renders from.
 */
export default function AccountPage() {
    const { state } = useAuth()
    const [keysState, setKeysState] = useState<KeysState>({ status: "loading" })
    const [revealedKey, setRevealedKey] = useState<ApiKeyCreateResponse | null>(null)
    const [label, setLabel] = useState("")
    const [creating, setCreating] = useState(false)
    const [createError, setCreateError] = useState<string | null>(null)
    const [revokingId, setRevokingId] = useState<number | null>(null)
    const [revokeError, setRevokeError] = useState<string | null>(null)

    const loadKeys = useCallback(async () => {
        setRevealedKey(null)
        setKeysState({ status: "loading" })
        try {
            const keys = await listApiKeys()
            setKeysState({ status: "ready", keys })
        } catch (caught) {
            setKeysState({
                status: "error",
                message: caught instanceof AuthApiError ? caught.message : "Could not load API keys.",
            })
        }
    }, [])

    useEffect(() => {
        if (state.status === "signed-in") void loadKeys()
    }, [state.status, loadKeys])

    // Neither the initial `/auth/me` probe nor a signed-out visitor has
    // anything to show here -- this page exists only for a signed-in
    // reader. `state: "from"` lets `LoginPage` send them straight back
    // here once they do sign in.
    if (state.status === "loading") return null
    if (state.status === "unreachable") {
        // Deliberately not a redirect. The session may well be live; the
        // archive just did not answer. Sending this visitor to the login
        // form would tell them they are signed out, which nobody has said.
        return (
            <section className="account-page">
                <h1>Account</h1>
                <p role="alert">
                    Could not reach the archive, so your sign-in state is unknown. Reload once it is back.
                </p>
            </section>
        )
    }
    if (state.status === "signed-out") return <Navigate to="/login" replace state={{ from: "/account" }} />

    const user = state.user

    async function handleCreate(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        setCreateError(null)
        setCreating(true)
        try {
            const created = await createApiKey({ label: label.trim() === "" ? undefined : label.trim() })
            setRevealedKey(created)
            setKeysState((previous) => (
                previous.status === "ready" ? { status: "ready", keys: [...previous.keys, toMetadata(created)] } : previous
            ))
            setLabel("")
        } catch (caught) {
            setCreateError(caught instanceof AuthApiError ? caught.message : "Could not create an API key.")
        } finally {
            setCreating(false)
        }
    }

    async function handleRevoke(keyId: number) {
        setRevokeError(null)
        setRevokingId(keyId)
        try {
            await revokeApiKey(keyId)
            await loadKeys()
        } catch (caught) {
            setRevokeError(caught instanceof AuthApiError ? caught.message : "Could not revoke this API key.")
        } finally {
            setRevokingId(null)
        }
    }

    return (
        <section className="account-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Account</span>
            </nav>
            <h1>Your account</h1>

            <dl className="kv-list">
                <div><dt>Name</dt><dd>{user.full_name || user.username}</dd></div>
                <div><dt>Email</dt><dd>{user.email ?? "not set"}</dd></div>
                <div><dt>Role</dt><dd>{user.role}</dd></div>
            </dl>

            <h2>API keys</h2>
            <p className="account-page-intro">
                API keys authenticate automated uploads (an ARC run, a script), not browser sessions. Each key's
                plain value is shown once, immediately after you create it, and never again.
            </p>

            {revealedKey && (
                <div className="api-key-reveal" role="status">
                    <p>
                        This is the only time this key's value is shown. Copy it now; TCKDB does not store or
                        display it again.
                    </p>
                    <div className="api-key-reveal-value">
                        <code>{revealedKey.key}</code>
                        <CopyButton value={revealedKey.key} label="API key" />
                    </div>
                </div>
            )}

            <form className="api-key-create" onSubmit={handleCreate}>
                {createError && <p className="auth-error" role="alert">{createError}</p>}
                <div className="auth-field">
                    <label htmlFor="api-key-label">Label</label>
                    <input
                        id="api-key-label"
                        value={label}
                        onChange={(event) => setLabel(event.target.value)}
                        placeholder="e.g. ARC on Zeus"
                    />
                </div>
                <button type="submit" className="auth-submit" aria-busy={creating} disabled={creating}>
                    Create API key
                </button>
            </form>

            {revokeError && <p className="auth-error" role="alert">{revokeError}</p>}

            {keysState.status === "loading" && <p>Loading API keys…</p>}
            {keysState.status === "error" && <p className="auth-error" role="alert">{keysState.message}</p>}
            {keysState.status === "ready" && (
                keysState.keys.length === 0 ? (
                    <p>No API keys yet.</p>
                ) : (
                    <div className="table-scroll">
                        <table className="data-table" aria-label="Your API keys">
                            <thead>
                                <tr>
                                    <th scope="col">Label</th>
                                    <th scope="col">Created</th>
                                    <th scope="col">Last used</th>
                                    <th scope="col">Revoked</th>
                                    <th scope="col">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                {keysState.keys.map((apiKey) => (
                                    <tr key={apiKey.id}>
                                        <td data-label="Label">{apiKey.label ?? "untitled"}</td>
                                        <td data-label="Created">{isoDate(apiKey.created_at)}</td>
                                        <td data-label="Last used">{isoDate(apiKey.last_used_at)}</td>
                                        <td data-label="Revoked">{isoDate(apiKey.revoked_at)}</td>
                                        <td data-label="Action">
                                            {apiKey.revoked_at === null ? (
                                                <button
                                                    type="button"
                                                    className="api-key-revoke"
                                                    disabled={revokingId === apiKey.id}
                                                    aria-busy={revokingId === apiKey.id}
                                                    onClick={() => handleRevoke(apiKey.id)}
                                                >
                                                    Revoke
                                                </button>
                                            ) : (
                                                "revoked"
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )
            )}
        </section>
    )
}
