import { useState } from "react"
import { loadEntryConformerPage, type ConformerPage } from "../api/speciesEntryApi"
import type { PageRequest } from "../domain/paging"
import { usePagedList } from "./usePagedList"

export type ConformerPagesState = ConformerPage & {
    /** A page other than the one shown is being fetched. */
    busy: boolean
    /** The requested page could not be loaded; the previous page stays on screen. */
    failed: boolean
    goTo: (request: PageRequest) => void
}

/**
 * Pages the entry's conformer groups. `initial` is the first page the entry
 * page already loaded, shown without a second fetch; moving past it fetches
 * the named page. While a page is loading, or if it fails, the page already on
 * screen stays (with `busy` / `failed` set) rather than emptying the picker,
 * because an empty conformer list would read as "this entry has no conformers".
 */
export function useConformerPages(entryRef: string, initial: ConformerPage): ConformerPagesState {
    const { state, listKey, goTo } = usePagedList(entryRef, loadEntryConformerPage, (page) => page.meta)
    const onFirstPage = listKey === entryRef
    const [shown, setShown] = useState<{ key: string; page: ConformerPage }>({ key: entryRef, page: initial })
    if (state.status === "ready" && !onFirstPage && shown.key !== listKey) {
        setShown({ key: listKey, page: state.record })
    }
    if (onFirstPage) {
        if (shown.key !== entryRef) setShown({ key: entryRef, page: initial })
        return { ...initial, busy: false, failed: false, goTo }
    }
    const settled = state.status === "ready" || state.status === "retrying" || state.status === "loading"
    return {
        ...shown.page,
        busy: state.status === "loading" || state.status === "retrying",
        failed: !settled,
        goTo,
    }
}
