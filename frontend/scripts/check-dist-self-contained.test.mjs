import { spawnSync } from "node:child_process"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { afterAll, describe, expect, it } from "vitest"

const SCRIPT = join(import.meta.dirname, "check-dist-self-contained.mjs")
const FLAG = "template builds a host at run time"
const roots = []

afterAll(() => {
    for (const root of roots) rmSync(root, { recursive: true, force: true })
})

/** Runs the dist check on a one-file build; only the (d) template verdict is read. */
function templateFlagged(js) {
    const root = mkdtempSync(join(tmpdir(), "check-dist-"))
    roots.push(root)
    mkdirSync(join(root, "assets"))
    writeFileSync(join(root, "index.html"), "<!doctype html><html><body></body></html>")
    writeFileSync(join(root, "assets", "schemas-test.js"), js)
    const run = spawnSync("node", [SCRIPT], { env: { ...process.env, CHECK_DIST_DIR: root }, encoding: "utf8" })
    expect(run.stdout).toContain("check-dist-self-contained:") // the scan ran to its summary
    return run.stderr.includes(FLAG)
}

const HELPER =
    "function xn(e){try{return typeof URL<`u`&&typeof URL.canParse==`function`?URL.canParse(e):(new URL(e),!0)}catch{return!1}}"
const T = "`http://[${e}]`"

describe("dist check: URL.canParse host templates", () => {
    it("accepts a template that only reaches a parse-only helper", () => {
        expect(templateFlagged(`${HELPER}function Hn(e){return /^[0-9a-f:.]+$/.test(e)?xn(${T}):!1}`)).toBe(false)
    })

    it("accepts a template passed straight to URL.canParse", () => {
        expect(templateFlagged(`export const ok=e=>URL.canParse(${T})`)).toBe(false)
    })

    it("flags the same template passed to fetch", () => {
        expect(templateFlagged(`fetch(${T})`)).toBe(true)
    })

    it("flags the same template kept as a URL", () => {
        expect(templateFlagged(`export const u=new URL(${T})`)).toBe(true)
    })

    it("flags the same template stored", () => {
        expect(templateFlagged(`export const s=${T}`)).toBe(true)
    })

    it("flags a helper that also fetches", () => {
        expect(templateFlagged(`function xn(e){fetch(e);return URL.canParse(e)}export const a=xn(${T})`)).toBe(true)
    })

    it("flags a helper that returns the string", () => {
        expect(templateFlagged(`function xn(e){URL.canParse(e);return e}export const a=xn(${T})`)).toBe(true)
    })

    it("flags a helper name declared twice", () => {
        const second = "function g(){function xn(e){return fetch(e)}return xn}"
        expect(templateFlagged(`${HELPER}${second}export const a=xn(${T})`)).toBe(true)
    })

    it("flags a shadowed URL", () => {
        expect(templateFlagged(`var URL=globalThis.Other;${HELPER}export const a=xn(${T})`)).toBe(true)
    })
})
