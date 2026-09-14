import { describe, expect, it } from "vitest"
import { SCHEME_KIND_LABELS, schemeKindLabel } from "./correctionSchemeFormat"

describe("schemeKindLabel: titles a scheme from the controlled scheme_kind vocabulary, never depositor free text", () => {
    it("returns the friendly label for every kind this archive's vocabulary recognises", () => {
        for (const kind of Object.keys(SCHEME_KIND_LABELS)) {
            expect(schemeKindLabel(kind)).toBe(SCHEME_KIND_LABELS[kind])
        }
    })

    /**
     * A future scheme kind this archive's vocabulary has not yet labelled
     * falls back to a transcription of the kind itself (`words()`), never
     * to a depositor-supplied `name` -- there is no `name` parameter on
     * this function at all, which is itself the fix: the OLD call site
     * (`LevelOfTheoryPage.tsx`, before this change) had a `?? scheme.name`
     * fallback here; this function has nowhere for that string to enter.
     */
    it("falls back to a transcription of an unmapped kind, never to a caller-supplied string", () => {
        expect(schemeKindLabel("some_future_kind")).toBe("some future kind")
    })
})
