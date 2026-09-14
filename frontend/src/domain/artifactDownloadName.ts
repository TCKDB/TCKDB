/**
 * What to call a downloaded artifact.
 *
 * `<stem>_<artifact_ref><suffix>` -- `input_art_7k2p9x.log`. The original
 * name leads so a downloads folder sorts by what the files are; the ref
 * keeps two calculations' `input.log` apart; the extension stays last so
 * the file opens in the right thing. The ref goes BEFORE the suffix for
 * that last reason -- `input.log_art_7k2p9x` disambiguates just as well
 * and is no longer a `.log`.
 *
 * **The filename is stored data, not a fact about the viewer's machine.**
 * A browser save honours the name it is given, so a recorded filename of
 * `../../thing` would be a name this app chose to propose. Only the
 * basename survives -- POSIX and Windows separators both -- and then only
 * characters that cannot mean anything to a path. Stricter than stripping
 * separators on purpose: it also removes the leading dot that would
 * otherwise produce a hidden file.
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
): string {
    const safe = filename ? sanitiseSegment(filename.trim()) : ""
    if (safe && artifactRef) {
        const ref = sanitiseSegment(artifactRef)
        if (ref) {
            const lastDot = safe.lastIndexOf(".")
            const hasSuffix = lastDot > 0
            const stem = hasSuffix ? safe.slice(0, lastDot) : safe
            const suffix = hasSuffix ? safe.slice(lastDot) : ""
            return `${stem}_${ref}${suffix}`
        }
    }
    // The digest is always a correct answer; it is only ever the least
    // useful one.
    return safe || sha256
}
