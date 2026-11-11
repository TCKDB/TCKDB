/**
 * What to call a downloaded artifact: `<kind>_<artifact_ref><ext>`, for example
 * `output_log_art_7k2p9x.log`. The name says what the file IS, then which
 * record it is, then its format.
 *
 * The recorded filename is depositor-typed (and on this corpus it identifies
 * nothing: 317 `output_log` artifacts are all called `input.log`). Only its
 * extension is used, and only when it is one of a known set; its stem never reaches the saved name or the anchor's
 * `download` attribute. This differs from `artifact_download_name` in
 * `clients/python/src/tckdb_client/cli.py`, which still uses the recorded stem.
 */
const UNSAFE = /[^A-Za-z0-9_-]+/g

// The only extensions a download may carry. The recorded filename is a string
// the producer typed and the browser briefly puts the name in an anchor's
// `download` attribute, so its stem is never used and its extension counts only
// when it is one of these.
const KNOWN_EXTENSIONS = new Set([
    ".log", ".out", ".gjf", ".com", ".inp", ".in", ".xyz", ".json", ".yml", ".yaml", ".txt", ".chk", ".fchk",
    ".cube", ".molden", ".hess", ".dat", ".csv", ".zip", ".gz", ".tgz", ".tar",
])

function sanitiseSegment(value: string): string {
    return value.replace(UNSAFE, "_").replace(/^[_-]+/, "").replace(/[_-]+$/, "")
}

export function artifactDownloadName(
    artifactRef: string | null | undefined,
    filename: string | null | undefined,
    sha256: string,
    kind?: string | null,
): string {
    const safeRef = artifactRef ? sanitiseSegment(artifactRef) : ""
    const safeKind = kind ? sanitiseSegment(kind) : ""

    // Extension from the recorded name, only from the known set.
    const lastDot = filename ? filename.trim().lastIndexOf(".") : -1
    const candidate = filename && lastDot > 0 ? filename.trim().slice(lastDot).toLowerCase() : ""
    const ext = KNOWN_EXTENSIONS.has(candidate) ? candidate : ""

    // Built from the kind and the ref only: TCKDB's own words.
    if (safeKind && safeRef) return `${safeKind}_${safeRef}${ext}`
    if (safeKind) return `${safeKind}${ext}`
    if (safeRef) return `artifact_${safeRef}${ext}`
    // The digest is always a correct answer; it is only ever the least useful one.
    return sha256
}
