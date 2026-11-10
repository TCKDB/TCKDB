import { describe, expect, it } from "vitest"
import { artifactDownloadName } from "./artifactDownloadName"

describe("artifactDownloadName", () => {
    it("is built from the kind and the ref, never the recorded stem", () => {
        expect(artifactDownloadName("art_7k2p9x", "input.log", "d", "output_log")).toBe("output_log_art_7k2p9x.log")
        expect(artifactDownloadName("art_7k2p9x", "PISS.log", "d", "output_log")).toBe("output_log_art_7k2p9x.log")
    })

    it("keeps an extension only from the known set", () => {
        expect(artifactDownloadName("art_a", "job.out", "d", "output_log")).toBe("output_log_art_a.out")
        expect(artifactDownloadName("art_a", "geom.XYZ", "d", "input")).toBe("input_art_a.xyz")
        expect(artifactDownloadName("art_a", "job.PISS", "d", "output_log")).toBe("output_log_art_a")
    })

    it("still disambiguates two artifacts of the same kind", () => {
        expect(artifactDownloadName("art_aaa", "input.log", "d", "output_log"))
            .not.toBe(artifactDownloadName("art_bbb", "input.log", "d", "output_log"))
    })

    it("falls back through ref to the digest, without using the filename stem", () => {
        expect(artifactDownloadName("art_a", "myjob.log", "d")).toBe("artifact_art_a.log")
        expect(artifactDownloadName(null, "input.log", "d", "output_log")).toBe("output_log.log")
        expect(artifactDownloadName(null, "input.log", "digest")).toBe("digest")
        expect(artifactDownloadName(null, null, "digest")).toBe("digest")
    })

    it.each(["../../.ssh/authorized_keys", "/etc/passwd", "..\\..\\windows\\system32", "sub/dir/input.log"])(
        "a stored filename cannot propose a path: %s",
        (hostile) => {
            const produced = artifactDownloadName("art_a", hostile, "digest", "output_log")
            expect(produced).not.toContain("/")
            expect(produced).not.toContain("\\")
            expect(produced).not.toContain("..")
        },
    )

    it("a hostile kind cannot propose a path either", () => {
        const produced = artifactDownloadName("art_a", "input.log", "digest", "../../evil")
        expect(produced).not.toContain("/")
        expect(produced).not.toContain("..")
    })

    it("cannot produce a hidden file", () => {
        expect(artifactDownloadName("art_a", ".bashrc", "digest", "..x").startsWith(".")).toBe(false)
    })
})
