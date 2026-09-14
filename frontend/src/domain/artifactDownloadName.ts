/**
 * What to call a downloaded artifact.
 *
 * `<kind>_<artifact_ref><ext>` -- `output_log_art_7k2p9x.log`. The name
 * says what the file IS, then which record it is, then its format.
 *
 * Why not the recorded filename
 * -----------------------------
 * Because on this corpus it identifies nothing. MEASURED on the hosted
 * instance 2026-09-14: 317 artifacts of kind `output_log` are all called
 * `input.log`, and 246 of kind `input` are all called `input.gjf`. That
 * is Gaussian's own convention -- `g16 input.gjf` writes `input.log`, so
 * the output is named after the JOB, not after its role -- and ARC names
 * every job file `input.gjf`.
 *
 * So the recorded filename was both non-identifying (563 files, two
 * distinct names) and actively misleading: a folder of downloads whose
 * every entry began `input` when most of them were outputs. The archive
 * still records the true filename and shows it in the Filename column;
 * nothing is lost, it just stops being the thing a local copy is named
 * after.
 *
 * The original extension is kept, because it is the one part of the
 * recorded name that carries information a reader needs: it decides what
 * opens the file.
 *
 * **The filename is stored data, not a fact about the viewer's machine.**
 * A browser save honours the name it is given, so a recorded filename of
 * `../../thing` would be a name this app chose to propose. Every segment
 * is reduced to characters that cannot mean anything to a path.
 *
 * This duplicates `artifact_download_name` in
 * `clients/python/src/tckdb_client/cli.py`. Two languages, one rule: a
 * file downloaded from the website and the same file downloaded with
 * `tckdb download artifact` should land under the same name, and if the
 * two ever disagree this comment is where to start.
 */
const UNSAFE = /[^A-Za-z0-9._-]+/g

function sanitiseSegment(value: string): string {
    // Basename first: this is what discards any directory component,
    // traversal or otherwise, before anything else looks at the value.
    const afterPosix = value.split("/").pop() ?? ""
    const afterWindows = afterPosix.split("\\").pop() ?? ""
    return afterWindows.replace(UNSAFE, "_").replace(/^[._]+/, "").replace(/[._]+$/, "")
}

export function artifactDownloadName(
    artifactRef: string | null | undefined,
    filename: string | null | undefined,
    sha256: string,
    kind?: string | null,
): string {
    const safeName = filename ? sanitiseSegment(filename.trim()) : ""
    const safeRef = artifactRef ? sanitiseSegment(artifactRef) : ""
    const safeKind = kind ? sanitiseSegment(kind) : ""

    // The extension is taken from the RECORDED name, never from the kind:
    // `output_log` is a role, `.log` is a format, and an artifact of that
    // kind could perfectly well be a `.out`.
    const lastDot = safeName.lastIndexOf(".")
    const ext = lastDot > 0 ? safeName.slice(lastDot) : ""
    const stem = lastDot > 0 ? safeName.slice(0, lastDot) : safeName

    // Kind leads when the archive knows it. Falling back to the recorded
    // stem keeps this working for any deployment whose filenames DO
    // identify something -- the measurement above is about this corpus,
    // not about artifacts in general.
    const lead = safeKind || stem
    if (lead && safeRef) return `${lead}_${safeRef}${ext}`
    if (lead) return `${lead}${ext}`
    // The digest is always a correct answer; it is only ever the least
    // useful one.
    return safeName || sha256
}
