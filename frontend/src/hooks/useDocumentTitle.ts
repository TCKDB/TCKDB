import { useEffect } from "react"

const SITE = "TCKDB"

/** The page title: "<name> · <kind> · TCKDB" once the name is known, "<kind> · TCKDB" before that. */
export function documentTitle(kind: string, name?: string | null): string {
    const trimmed = name?.trim()
    return trimmed ? `${trimmed} · ${kind} · ${SITE}` : `${kind} · ${SITE}`
}

export function useDocumentTitle(kind: string, name?: string | null) {
    useEffect(() => {
        document.title = documentTitle(kind, name)
    }, [kind, name])
}
