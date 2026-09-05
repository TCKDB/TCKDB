/**
 * "about 1 second" / "about 30 seconds" -- spelled out, never abbreviated
 * to "Ns". Review feedback on the pre-fix copy ("Wait about 2s") called
 * that operator vocabulary: a reader is not expected to know seconds are
 * abbreviated "s" the way a log line would; "about 30 seconds" reads as
 * an instruction, "30s" reads as internal shorthand. Split into its own
 * module (rather than living in `components/RetryCountdown.tsx`) because
 * a file that exports both a component and a plain function trips
 * `react-refresh/only-export-components`.
 */
export function formatWaitSeconds(seconds: number): string {
    const whole = Math.max(1, Math.round(seconds))
    return `about ${whole} second${whole === 1 ? "" : "s"}`
}
