import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render } from "@testing-library/react"
import { refWithBreaks } from "./refBreaks"

afterEach(cleanup)

describe("refWithBreaks", () => {
    it("round-trips the exact text content -- no character added or dropped", () => {
        const { container } = render(<span>{refWithBreaks("tse_aq5ktxlu27nvul3hmdwpuyuz4e")}</span>)
        expect(container.textContent).toBe("tse_aq5ktxlu27nvul3hmdwpuyuz4e")
    })

    it("inserts a <wbr> immediately after every underscore", () => {
        const { container } = render(<span>{refWithBreaks("lot_ab12_cd34")}</span>)
        const wbrs = container.querySelectorAll("wbr")
        expect(wbrs).toHaveLength(2)
        // Mutation check: each <wbr> must sit right after an "_" text node,
        // not merely exist anywhere -- two <wbr>s at the START would pass a
        // bare count assertion while breaking nothing useful.
        for (const wbr of wbrs) {
            const prev = wbr.previousSibling
            expect(prev?.textContent?.endsWith("_")).toBe(true)
        }
    })

    it("inserts no <wbr> for a ref with no underscore (a hyphen already has a natural break point)", () => {
        const { container } = render(<span>{refWithBreaks("VGGSQFUCUMXWEO-UHFFFAOYSA-N")}</span>)
        expect(container.querySelectorAll("wbr")).toHaveLength(0)
        expect(container.textContent).toBe("VGGSQFUCUMXWEO-UHFFFAOYSA-N")
    })

    it("handles an empty string", () => {
        const { container } = render(<span>{refWithBreaks("")}</span>)
        expect(container.textContent).toBe("")
        expect(container.querySelectorAll("wbr")).toHaveLength(0)
    })
})
