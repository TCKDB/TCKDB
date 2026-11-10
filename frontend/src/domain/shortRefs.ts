
const MIN_REF_PREFIX = 13

/** Each drawn ref cut to the shortest prefix no other drawn ref shares (at least 13 characters), with a visible "…". */
export function shortRefs(refs: readonly string[]): string[] {
    return refs.map((ref) => {
        let length = Math.min(MIN_REF_PREFIX, ref.length)
        while (length < ref.length && refs.some((other) => other !== ref && other.startsWith(ref.slice(0, length)))) length += 1
        return length < ref.length ? `${ref.slice(0, length)}…` : ref
    })
}
