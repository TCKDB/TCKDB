import { PageShell } from "./PageShell"

export function LoadingPage() {
    return <PageShell contents="none"><p aria-live="polite">Loading archive…</p></PageShell>
}
