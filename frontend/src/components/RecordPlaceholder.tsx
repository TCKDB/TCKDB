import type { ReactNode } from "react"
import { PageShell } from "./PageShell"

/** A loading, error or not-found state, inside the page shell so the page does not shift when it resolves. */
export function RecordPlaceholder({ busy, alert, children }: { busy?: boolean; alert?: boolean; children: ReactNode }) {
    return (
        <PageShell contents="none">
            <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
                {children}
            </section>
        </PageShell>
    )
}
