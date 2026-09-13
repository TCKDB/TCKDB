import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link, useLocation, useNavigate } from "react-router-dom"
import "../auth.css"
import { AuthApiError } from "../api/authApi"
import { useAuth } from "../hooks/useAuth"

type Mode = "sign-in" | "register"

const MODES: { value: Mode; label: string }[] = [
    { value: "sign-in", label: "Sign in" },
    { value: "register", label: "Create account" },
]

/**
 * One page, two modes (owner's call is implicit here, not the backend's:
 * `/auth/register` and `/auth/login` are separate endpoints, but a reader
 * arriving at this page has one goal -- get signed in -- and a mode
 * toggle states that up front the same way `IdentifierSearch`'s
 * Species/Reactions switch does, rather than sending a reader who picked
 * the wrong one of two separate pages hunting for a link to the other).
 * A returning visitor already has an account; a first-time contributor
 * does not -- both belong on the same page because neither knows which
 * they are until they try.
 *
 * Redirects to `/account` once `useAuth()` reports signed-in, whether
 * that happened via this page's own submit or (StrictMode double-render
 * aside) because a session was already live when this page mounted --
 * there is nothing for a signed-in reader to do here.
 */
export default function LoginPage() {
    const { state, login, register } = useAuth()
    const navigate = useNavigate()
    const location = useLocation()
    const [mode, setMode] = useState<Mode>("sign-in")
    const [username, setUsername] = useState("")
    const [password, setPassword] = useState("")
    const [email, setEmail] = useState("")
    const [fullName, setFullName] = useState("")
    const [error, setError] = useState<string | null>(null)
    const [submitting, setSubmitting] = useState(false)

    useEffect(() => {
        if (state.status === "signed-in") {
            const from = (location.state as { from?: string } | null)?.from ?? "/account"
            navigate(from, { replace: true })
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [state.status])

    function selectMode(next: Mode) {
        if (next === mode) return
        setMode(next)
        setError(null)
    }

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        setError(null)
        setSubmitting(true)
        try {
            if (mode === "sign-in") {
                await login(username, password)
            } else {
                await register({
                    username,
                    password,
                    email: email.trim() === "" ? undefined : email.trim(),
                    full_name: fullName.trim() === "" ? undefined : fullName.trim(),
                })
            }
            // `useAuth()`'s state update above drives the redirect effect;
            // nothing further to do here on success.
        } catch (caught) {
            setError(caught instanceof AuthApiError ? caught.message : "Could not reach the archive. Try again.")
        } finally {
            setSubmitting(false)
        }
    }

    return (
        <section className="auth-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">{mode === "sign-in" ? "Sign in" : "Create account"}</span>
            </nav>
            <h1>{mode === "sign-in" ? "Sign in" : "Create an account"}</h1>
            <p className="auth-page-intro">
                {mode === "sign-in"
                    ? "Sign in to manage the API keys your uploads authenticate with."
                    : "An account lets you mint API keys for automated uploads. Public reads never require one."}
            </p>

            <div className="auth-mode-toggle" role="radiogroup" aria-label="Sign in or create an account">
                {MODES.map(({ value, label }) => (
                    <button
                        key={value}
                        type="button"
                        role="radio"
                        aria-checked={mode === value}
                        className="auth-mode-option"
                        data-active={mode === value}
                        onClick={() => selectMode(value)}
                    >
                        {label}
                    </button>
                ))}
            </div>

            <form className="auth-form" onSubmit={handleSubmit} noValidate>
                {error && <p className="auth-error" role="alert">{error}</p>}

                <div className="auth-field">
                    <label htmlFor="auth-username">Username</label>
                    <input
                        id="auth-username"
                        autoComplete="username"
                        value={username}
                        onChange={(event) => setUsername(event.target.value)}
                        required
                    />
                </div>

                {mode === "register" && (
                    <div className="auth-field">
                        <label htmlFor="auth-full-name">Full name</label>
                        <input
                            id="auth-full-name"
                            autoComplete="name"
                            value={fullName}
                            onChange={(event) => setFullName(event.target.value)}
                        />
                        <p className="auth-field-hint">Optional. Shown instead of your username wherever this archive displays who you are.</p>
                    </div>
                )}

                {mode === "register" && (
                    <div className="auth-field">
                        <label htmlFor="auth-email">Email</label>
                        <input
                            id="auth-email"
                            type="email"
                            autoComplete="email"
                            value={email}
                            onChange={(event) => setEmail(event.target.value)}
                        />
                        <p className="auth-field-hint">Optional.</p>
                    </div>
                )}

                <div className="auth-field">
                    <label htmlFor="auth-password">Password</label>
                    <input
                        id="auth-password"
                        type="password"
                        autoComplete={mode === "sign-in" ? "current-password" : "new-password"}
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        minLength={mode === "register" ? 8 : undefined}
                        required
                    />
                    {mode === "register" && <p className="auth-field-hint">At least 8 characters.</p>}
                </div>

                <button type="submit" className="auth-submit" aria-busy={submitting} disabled={submitting}>
                    {mode === "sign-in" ? "Sign in" : "Create account"}
                </button>
            </form>
        </section>
    )
}
