import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { loadHomeExample, parseHomeExample } from "./siteConfig"

const SPE = "spe_abcdefghijklmnopqrstuvwxyz"
const RXE = "rxe_234567abcdefghijklmnopqrst"
const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

describe("parseHomeExample", () => {
    it("names no example when the value is unset or empty", () => {
        expect(parseHomeExample(undefined)).toBeNull()
        expect(parseHomeExample("")).toBeNull()
        expect(parseHomeExample("   ")).toBeNull()
    })

    it.each([
        ["a molecule ref that is too short", "spe_abc"],
        ["a molecule ref one character too long", `${SPE}a`],
        ["a ref with characters outside base32", "spe_abcdefghijklmnopqrstuvwx10"],
        ["an uppercase ref", SPE.toUpperCase()],
        ["a species ref, which is not an entry", "spc_abcdefghijklmnopqrstuvwxyz"],
        ["a reaction ref, which is not an entry", "rxn_abcdefghijklmnopqrstuvwxyz"],
        ["a path", `/species-entries/${SPE}`],
        ["a ref with trailing text", `${SPE} extra`],
    ])("names no example for %s", (_name, value) => {
        expect(parseHomeExample(value)).toBeNull()
    })

    it("accepts a molecule entry ref and a reaction entry ref, ignoring surrounding whitespace", () => {
        expect(parseHomeExample(SPE)).toEqual({ kind: "molecule", ref: SPE })
        expect(parseHomeExample(RXE)).toEqual({ kind: "reaction", ref: RXE })
        expect(parseHomeExample(`  ${SPE}\n`)).toEqual({ kind: "molecule", ref: SPE })
    })

    it("names no example for a value that is not text", () => {
        expect(parseHomeExample(42)).toBeNull()
        expect(parseHomeExample(null)).toBeNull()
    })
})

describe("loadHomeExample", () => {
    const json = { headers: { "content-type": "application/json" } }

    it("reads a valid file, asking the server to revalidate", async () => {
        let cache = ""
        server.use(http.get("/site-config.json", ({ request }) => { cache = request.cache; return HttpResponse.json({ homeExampleRef: SPE }) }))
        expect(await loadHomeExample()).toEqual({ kind: "molecule", ref: SPE })
        expect(cache).toBe("no-cache")
    })

    it("reads a reaction entry ref and ignores other keys", async () => {
        server.use(http.get("/site-config.json", () => HttpResponse.json({ homeExampleRef: RXE, other: 1 })))
        expect(await loadHomeExample()).toEqual({ kind: "reaction", ref: RXE })
    })

    it.each([
        ["a 404", () => new HttpResponse(null, { status: 404 })],
        ["a 500", () => new HttpResponse("{}", { status: 500, ...json })],
        ["an HTML page with status 200 (the SPA entry point)", () => new HttpResponse("<div id=\"root\"></div>", { headers: { "content-type": "text/html" } })],
        ["JSON with the wrong content type", () => new HttpResponse(JSON.stringify({ homeExampleRef: SPE }), { headers: { "content-type": "text/plain" } })],
        ["bad JSON", () => new HttpResponse("{nope", json)],
        ["JSON that is not an object", () => new HttpResponse("[1]", json)],
        ["an object without the ref", () => HttpResponse.json({ homeExample: SPE })],
        ["a ref of the wrong kind", () => HttpResponse.json({ homeExampleRef: "spc_abcdefghijklmnopqrstuvwxyz" })],
        ["a ref that is not text", () => HttpResponse.json({ homeExampleRef: 7 })],
        ["a network error", () => HttpResponse.error()],
    ])("names no example for %s", async (_name, answer) => {
        server.use(http.get("/site-config.json", answer))
        expect(await loadHomeExample()).toBeNull()
    })
})
