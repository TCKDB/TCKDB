/** The line under a usage section's heading: the total, and the 50-row cap only when it applies. Empty when nothing is listed. */
export function usageIntro(total: number, listed: number, noun: string, nounPlural: string): string | undefined {
    if (listed === 0) return undefined
    const count = `${total} ${total === 1 ? noun : nounPlural}`
    return total > 50 ? `${count}; first 50 listed.` : `${count}.`
}
