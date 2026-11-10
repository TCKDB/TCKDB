import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
import { documentTitle, useDocumentTitle } from "./useDocumentTitle"

afterEach(cleanup)

function Page({ kind, name }: { kind: string; name?: string | null }) {
    useDocumentTitle(kind, name)
    return null
}

describe("useDocumentTitle", () => {
    it("writes '<name> · <kind> · TCKDB' once the name is known and '<kind> · TCKDB' before", () => {
        const { rerender } = render(<Page kind="Species" />)
        expect(document.title).toBe("Species · TCKDB")
        rerender(<Page kind="Species" name="CH3" />)
        expect(document.title).toBe("CH3 · Species · TCKDB")
    })

    it("treats a blank name as unknown", () => {
        expect(documentTitle("Geometry", "  ")).toBe("Geometry · TCKDB")
        expect(documentTitle("Geometry", null)).toBe("Geometry · TCKDB")
    })
})
