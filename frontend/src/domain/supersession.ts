type Supersession = { superseded_by: string; current: string; chain_length: number }

/** The refs a correction notice names, labelled for a References list; the notice itself shows words. */
export function supersessionRefs(supersession: Supersession | null | undefined): { label: string; value: string }[] {
    if (!supersession) return []
    const refs = [{ label: "Replaced by", value: supersession.superseded_by }]
    if (supersession.chain_length > 1 && supersession.current !== supersession.superseded_by) {
        refs.push({ label: "Current record in this chain", value: supersession.current })
    }
    return refs
}
