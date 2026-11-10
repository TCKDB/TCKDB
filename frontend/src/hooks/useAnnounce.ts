import { useSyncExternalStore } from "react"

type Listener = () => void

let message = ""
let pendingTimer: ReturnType<typeof setTimeout> | null = null
const listeners = new Set<Listener>()

function emit() {
    for (const listener of listeners) listener()
}

/** Clears the live region, then sets the message on the next tick so a repeated message is read again. */
export function announce(next: string) {
    if (pendingTimer !== null) clearTimeout(pendingTimer)
    message = ""
    emit()
    pendingTimer = setTimeout(() => {
        pendingTimer = null
        message = next
        emit()
    }, 50)
}

function subscribe(listener: Listener) {
    listeners.add(listener)
    return () => { listeners.delete(listener) }
}

/** The current polite-region text; read by `Announcer`. */
export function useAnnouncement(): string {
    return useSyncExternalStore(subscribe, () => message, () => "")
}

/** A stable `announce` function; a no-op when no `Announcer` is mounted. */
export function useAnnounce() {
    return announce
}
