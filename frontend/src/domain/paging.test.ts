import { describe, expect, it } from "vitest"
import { firstPageRequest, nextPageRequest, pagedRef, pageQuery, parsePagedRef, previousPageRequest } from "./paging"

const window = (offset: number, limit: number, returned: number, total: number) => ({ offset, limit, returned, total })

describe("page requests", () => {
    it("the first page is offset 0 at the default size", () => {
        expect(firstPageRequest()).toEqual({ offset: 0, limit: 50 })
        expect(firstPageRequest(10)).toEqual({ offset: 0, limit: 10 })
    })

    it("next steps by what was returned and stops at the end", () => {
        expect(nextPageRequest(window(0, 50, 50, 120))).toEqual({ offset: 50, limit: 50 })
        expect(nextPageRequest(window(100, 50, 20, 120))).toBeNull()
        expect(nextPageRequest(window(50, 50, 50, 100))).toBeNull()
        expect(nextPageRequest(window(0, 50, 0, 0))).toBeNull()
    })

    it("next never loops on a window that returned nothing", () => {
        expect(nextPageRequest(window(150, 50, 0, 120))).toBeNull()
    })

    it("previous steps back one window and clamps at the start", () => {
        expect(previousPageRequest(window(100, 50, 20, 120))).toEqual({ offset: 50, limit: 50 })
        expect(previousPageRequest(window(20, 50, 50, 120))).toEqual({ offset: 0, limit: 50 })
        expect(previousPageRequest(window(0, 50, 50, 120))).toBeNull()
    })

    it("the wire text of a request is built in one place", () => {
        expect(pageQuery({ offset: 50, limit: 25 }).toString()).toBe("offset=50&limit=25")
    })
})

describe("paged refs", () => {
    it("round-trips a page", () => {
        expect(parsePagedRef(pagedRef("spe_abc", { offset: 50, limit: 25 }))).toEqual({ ref: "spe_abc", request: { offset: 50, limit: 25 } })
    })

    it("a plain ref names no window, so nothing is sent", () => {
        expect(parsePagedRef("spe_abc")).toEqual({ ref: "spe_abc", request: null })
    })

    it("a malformed suffix is treated as part of a plain ref, not as a window", () => {
        expect(parsePagedRef("spe_abc#x:y")).toEqual({ ref: "spe_abc#x:y", request: null })
        expect(parsePagedRef("spe_abc#5:0")).toEqual({ ref: "spe_abc#5:0", request: null })
    })
})
