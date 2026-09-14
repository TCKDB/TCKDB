import { describe, expect, it } from "vitest"
import { artifactDownloadName } from "./artifactDownloadName"

describe("artifactDownloadName", () => {
    it("leads with the name, disambiguates with the ref, ends with the extension", () => {
        expect(artifactDownloadName("art_7k2p9x", "input.log", "a".repeat(64)))
            .toBe("input_art_7k2p9x.log")
    })

    it("keeps the extension last so the file opens in the right thing", () => {
        // `input.log_art_7k2p9x` sorts and disambiguates just as well and
        // is no longer a `.log`.
        expect(artifactDownloadName("art_a", "input.log", "d")).toMatch(/\.log$/)
        expect(artifactDownloadName("art_a", "job.out.log", "d")).toBe("job.out_art_a.log")
        expect(artifactDownloadName("art_a", "geom.xyz", "d")).toMatch(/\.xyz$/)
    })

    it("stops two calculations' input.log from colliding", () => {
        expect(artifactDownloadName("art_aaa", "input.log", "d"))
            .not.toBe(artifactDownloadName("art_bbb", "input.log", "d"))
    })

    it("a name with no extension just gets the ref", () => {
        expect(artifactDownloadName("art_a", "OUTPUT", "d")).toBe("OUTPUT_art_a")
    })

    it("falls back through filename to digest", () => {
        expect(artifactDownloadName(null, "input.log", "d")).toBe("input.log")
        expect(artifactDownloadName("art_a", null, "digest")).toBe("digest")
        expect(artifactDownloadName(null, null, "digest")).toBe("digest")
        expect(artifactDownloadName(null, "   ", "digest")).toBe("digest")
        expect(artifactDownloadName(null, "///", "digest")).toBe("digest")
    })

    it.each([
        "../../.ssh/authorized_keys",
        "/etc/passwd",
        "..\\..\\windows\\system32",
        "sub/dir/input.log",
    ])("a stored filename cannot propose a path: %s", (hostile) => {
        /**
         * A browser save honours the name it is given, so any separator
         * that survived here would be a name this app chose to propose.
         */
        const produced = artifactDownloadName("art_a", hostile, "digest")
        expect(produced).not.toContain("/")
        expect(produced).not.toContain("\\")
        expect(produced).not.toContain("..")
    })

    it("cannot produce a hidden file", () => {
        expect(artifactDownloadName(null, ".bashrc", "digest").startsWith(".")).toBe(false)
    })
})
