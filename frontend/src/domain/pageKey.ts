const ENTRY_SECTION_PATH = /^(\/species-entries\/[^/]+)\/[^/]+\/?$/

/** The part of the URL that identifies a page: the query, the hash and the species-entry tab do not. */
export function pageKey(pathname: string): string {
    return pathname.replace(ENTRY_SECTION_PATH, "$1").replace(/(.)\/$/, "$1")
}
