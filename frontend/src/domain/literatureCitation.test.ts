import { describe, expect, it } from "vitest"
import { doiHref, publicCitation } from "./literatureCitation"

const lit = { literature_ref: "lit_abc", title: "A title", doi: "10.1063/1.3382344" }

describe("publicCitation DOI", () => {
    it("is returned for a registry-resolved record", () => {
        expect(publicCitation({ ...lit, title_source: "resolved_metadata" }).doi).toBe("10.1063/1.3382344")
        expect(doiHref("10.1063/1.3382344")).toBe("https://doi.org/10.1063/1.3382344")
    })

    it.each(["depositor_supplied", null, undefined])("is withheld when title_source is %s", (title_source) => {
        expect(publicCitation({ ...lit, title_source }).doi).toBeNull()
    })

    it("is withheld when a resolved record's DOI is not DOI-shaped", () => {
        for (const doi of ["10.1/x", "10.1000/a b", "10.1000/<script>", "doi:10.1000/x", ""]) {
            expect(publicCitation({ ...lit, doi, title_source: "resolved_metadata" }).doi).toBeNull()
        }
    })

    it("keeps parentheses that real DOIs carry", () => {
        const doi = "10.1016/S0009-2614(89)87395-6"
        expect(publicCitation({ ...lit, doi, title_source: "resolved_metadata" }).doi).toBe(doi)
    })
})
