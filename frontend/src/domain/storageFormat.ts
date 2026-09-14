/**
 * Byte formatting for the admin storage readout.
 *
 * Binary units, not decimal: the number being rendered is an object-store
 * refusal's `attempted_bytes`, and every tool an operator would compare it
 * against -- `du`, `df -h`, MinIO's own console -- reports powers of 1024.
 * Rendering 1048576 as "1.0 MB" when `du` calls it "1.0M" invites an
 * operator to conclude the two disagree.
 */
const UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const

export function formatBytes(bytes: number): string {
    // A negative or non-finite size is not a size. Saying "unknown" is
    // honest; rendering "-1.0 KiB" would present a broken value as a
    // measurement, which is the failure mode this whole surface exists to
    // avoid elsewhere.
    if (!Number.isFinite(bytes) || bytes < 0) return "unknown"
    // Whole bytes below the first threshold: "900 B" is exact and "0.9 KiB"
    // is both rounder and longer.
    if (bytes < 1024) return `${Math.round(bytes)} B`

    let value = bytes
    let unit = 0
    while (value >= 1024 && unit < UNITS.length - 1) {
        value /= 1024
        unit += 1
    }
    return `${value.toFixed(1)} ${UNITS[unit]}`
}
