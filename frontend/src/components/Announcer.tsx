import { useAnnouncement } from "../hooks/useAnnounce"
import "../announcer.css"

/** One visually hidden polite live region; not `role="status"`, which pages query for their own messages. */
export function Announcer() {
    const text = useAnnouncement()
    return <div className="announcer" aria-live="polite" aria-atomic="true">{text}</div>
}
