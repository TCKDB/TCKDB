import { useState } from "react"
import { ArtifactDownloadError, fetchArtifact, saveBlob } from "../api/artifactDownloadApi"
import { useAuth } from "../hooks/useAuth"

/**
 * Downloads one artifact's bytes, from the calculation page's Artifacts
 * table.
 *
 * Shown only to a signed-in reader. That is not a permission check --
 * `require_auth` on the route is -- it is a promise not to offer an
 * action that cannot work: raw artifact bytes are gated on every
 * deployment with no opt-out (ADR 0004), so a download control offered
 * to an anonymous visitor is a button whose only outcome is a 401.
 *
 * While the archive's sign-in state is still unknown (the initial
 * `/auth/me` probe, or a transport failure) this renders nothing rather
 * than a "sign in" prompt: the visitor may well be signed in, and
 * offering the prompt asserts they are not.
 */
export function ArtifactDownloadButton({ sha256, artifactRef, filename, kind }: {
    sha256: string
    artifactRef?: string | null
    filename?: string | null
    kind?: string | null
}) {
    const { state } = useAuth()
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState<string | null>(null)

    if (state.status !== "signed-in") {
        if (state.status === "signed-out") {
            return <span className="artifact-download-note">Sign in to download</span>
        }
        return null
    }

    async function handleClick() {
        setError(null)
        setBusy(true)
        try {
            const { blob, filename: name } = await fetchArtifact({ sha256, artifactRef, filename, kind })
            saveBlob(blob, name)
        } catch (caught) {
            setError(
                caught instanceof ArtifactDownloadError
                    ? caught.message
                    : "The download failed. Check your connection and try again.",
            )
        } finally {
            setBusy(false)
        }
    }

    return (
        <>
            <button
                type="button"
                className="artifact-download"
                disabled={busy}
                onClick={() => { void handleClick() }}
            >
                {busy ? "Downloading…" : "Download"}
            </button>
            {error && <p className="auth-error artifact-download-error" role="alert">{error}</p>}
        </>
    )
}
