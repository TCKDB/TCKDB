import { useEffect, useState } from "react"
import { formatWaitSeconds } from "../domain/rateLimitFormat"

/**
 * A live countdown, ticking down once a second from an initial
 * `retryAfterSeconds`. Purely a DISPLAY convenience -- it does not drive
 * the actual retry (that already runs independently, inside
 * `requestScientificJson`'s own `setTimeout`); this only has to look
 * honest to a reader watching the number for however long the wait is,
 * which can be up to a minute (`rate_limit_anon_read_per_minute`,
 * `backend/app/api/config.py`). Floors at 1 second rather than counting
 * down to 0 or negative -- the real retry firing (and the page moving on
 * to `ready` or `rate-limited`) is what actually changes the page, not
 * this clock reaching zero, so it never claims to reach zero itself.
 *
 * `retryAfterSeconds` is only ever set ONCE per wait -- `requestScientificJson`
 * calls its `onRateLimited` callback (which is what ultimately produces
 * this prop, via `requestCache.ts`'s `onWaiting`) at most once per pending
 * request -- so this intentionally seeds local state from the prop only
 * on mount and never resets it from a later prop change; a genuinely NEW
 * wait means a fresh subscription and therefore a fresh mount of whatever
 * renders this.
 */
export function RetryCountdown({ retryAfterSeconds }: { retryAfterSeconds: number }) {
    const [remaining, setRemaining] = useState(retryAfterSeconds)

    useEffect(() => {
        const interval = setInterval(() => {
            setRemaining((seconds) => Math.max(1, seconds - 1))
        }, 1000)
        return () => clearInterval(interval)
    }, [])

    return <span>{formatWaitSeconds(remaining)}</span>
}
