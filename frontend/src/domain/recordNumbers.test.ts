import { describe, expect, it } from "vitest"
import { numberedWords, numberRefs } from "./recordNumbers"

describe("recordNumbers", () => {
    it("numbers a page after the records before it", () => {
        expect([...numberRefs(["a", "b"], 50)]).toEqual([["a", 51], ["b", 52]])
        expect([...numberRefs(["a", "b"])]).toEqual([["a", 1], ["b", 2]])
    })

    it("names a record of a later page with its number even when the page holds only that one", () => {
        expect(numberedWords("Thermo record", "a", numberRefs(["a"], 50))).toBe("Thermo record 51")
        expect(numberedWords("Thermo record", "a", numberRefs(["a"]))).toBe("Thermo record")
        expect(numberedWords("Thermo record", "b", numberRefs(["a", "b"]))).toBe("Thermo record 2")
    })
})
