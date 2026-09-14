import { describe, expect, it } from "vitest"
import { artifactDownloadName } from "./artifactDownloadName"

describe("artifactDownloadName", () => {
    it("leads with what the file IS, not what it was called", () => {
        /**
         * MEASURED on the hosted instance 2026-09-14: 317 artifacts of
         * kind `output_log` are all named `input.log`, because `g16
         * input.gjf` writes `input.log` -- Gaussian names the output
         * after the JOB, not after its role. Leading with the recorded
         * name produced a folder of downloads whose every entry began
         * "input" when most of them were outputs.
         */
        expect(artifactDownloadName("art_7k2p9x", "input.log", "d", "output_log"))
            .toBe("output_log_art_7k2p9x.log")
    })

    it("takes the extension from the recorded name, not from the kind", () => {
        // `output_log` is a role; `.log` is a format. The same kind could
        // perfectly well arrive as `.out`.
        expect(artifactDownloadName("art_a", "job.out", "d", "output_log"))
            .toBe("output_log_art_a.out")
        expect(artifactDownloadName("art_a", "geom.xyz", "d", "input"))
            .toBe("input_art_a.xyz")
    })

    it("still disambiguates two artifacts of the same kind", () => {
        expect(artifactDownloadName("art_aaa", "input.log", "d", "output_log"))
            .not.toBe(artifactDownloadName("art_bbb", "input.log", "d", "output_log"))
    })

    it("falls back to the recorded stem when no kind is known", () => {
        // The measurement above is about THIS corpus. A deployment whose
        // filenames do identify something keeps working.
        expect(artifactDownloadName("art_a", "myjob.log", "d")).toBe("myjob_art_a.log")
        expect(artifactDownloadName("art_a", "myjob.log", "d", null)).toBe("myjob_art_a.log")
    })

    it("falls back through kind and filename to the digest", () => {
        expect(artifactDownloadName(null, "input.log", "d", "output_log")).toBe("output_log.log")
        expect(artifactDownloadName(null, "input.log", "d")).toBe("input.log")
        expect(artifactDownloadName("art_a", null, "digest")).toBe("digest")
        expect(artifactDownloadName(null, null, "digest")).toBe("digest")
        expect(artifactDownloadName(null, "   ", "digest")).toBe("digest")
    })

    it.each([
        "../../.ssh/authorized_keys",
        "/etc/passwd",
        "..\\..\\windows\\system32",
        "sub/dir/input.log",
    ])("a stored filename cannot propose a path: %s", (hostile) => {
        const produced = artifactDownloadName("art_a", hostile, "digest", "output_log")
        expect(produced).not.toContain("/")
        expect(produced).not.toContain("\\")
        expect(produced).not.toContain("..")
    })

    it("a hostile kind cannot propose a path either", () => {
        // The kind is a server-supplied enum today, but it reaches the
        // name the same way the filename does.
        const produced = artifactDownloadName("art_a", "input.log", "digest", "../../evil")
        expect(produced).not.toContain("/")
        expect(produced).not.toContain("..")
    })

    it("cannot produce a hidden file", () => {
        expect(artifactDownloadName(null, ".bashrc", "digest").startsWith(".")).toBe(false)
    })
})
