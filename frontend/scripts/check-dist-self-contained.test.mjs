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

/** Runs the dist check on a small build; only the (d) template verdict is read. */
function templateFlagged(js, { module = true, extra = null } = {}) {
    const root = mkdtempSync(join(tmpdir(), "check-dist-"))
    roots.push(root)
    mkdirSync(join(root, "assets"))
    writeFileSync(join(root, "index.html"), "<!doctype html><html><body></body></html>")
    writeFileSync(join(root, "assets", "schemas-test.js"), module ? `export{};${js}` : js)
    if (extra) writeFileSync(join(root, "assets", "other.js"), `export{};${extra}`)
    const run = spawnSync("node", [SCRIPT], { env: { ...process.env, CHECK_DIST_DIR: root }, encoding: "utf8" })
    expect(run.stdout).toContain("check-dist-self-contained:") // the scan ran to its summary
    return run.stderr.includes(FLAG)
}

const HELPER =
    "function xn(e){try{return typeof URL<`u`&&typeof URL.canParse==`function`?URL.canParse(e):(new URL(e),!0)}catch{return!1}}"
const T = "`http://[${e}]`"
const CALL = "export const a=xn(`http://[${h}]`)"
const SIMPLE = "function xn(e){return URL.canParse(e)}"

describe("dist check: URL.canParse host templates", () => {
    it("accepts the real minified zod helper", () => {
        const real = `${HELPER}function Hn(e){return Vn.test(e)?xn(${T}):!1}`
        expect(templateFlagged(real)).toBe(false)
    })

    it("accepts a plain one-line helper", () => {
        expect(templateFlagged(`${SIMPLE}${CALL}`)).toBe(false)
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

    describe("bypasses stay flagged", () => {
        const cases = {
            "for-of member target over new URL": `function xn(e){for(globalThis.u of [new URL(e)]);return URL.canParse(e)}${CALL}`,
            "for-of member target over arguments": `function xn(e){for(globalThis.u of arguments);return URL.canParse(e)}${CALL}`,
            "helper that fetches": `function xn(e){fetch(e);return URL.canParse(e)}${CALL}`,
            "helper that returns the string": `function xn(e){URL.canParse(e);return e}${CALL}`,
            "var in helper": `function xn(e){var u=e;return URL.canParse(e)}${CALL}`,
            "element access in helper": `function xn(e){return URL.canParse(e)&&globalThis['u']}${CALL}`,
            "property write in helper": `function xn(e){globalThis.u=e;return URL.canParse(e)}${CALL}`,
            "same name declared in a nested scope": `globalThis.xn=fetch;function g(){function xn(e){return URL.canParse(e)}}${CALL}`,
            "array destructuring write of the helper name": `[xn]=[fetch];${SIMPLE}${CALL}`,
            "for-of over the helper name": `for(xn of [fetch]);${SIMPLE}${CALL}`,
            "object destructuring write of the helper name": `({k:xn}={k:fetch});${SIMPLE}${CALL}`,
            "with binding the helper name": `${SIMPLE}with({xn:fetch})export const a=xn(\`http://[\${h}]\`)`,
            "helper name used as a value": `${SIMPLE}export const f=[xn];${CALL}`,
            "URL.canParse assigned": `URL.canParse=fetch;export const a=URL.canParse(\`http://[\${h}]\`)`,
            "URL['canParse'] assigned": `URL['canParse']=fetch;export const a=URL.canParse(\`http://[\${h}]\`)`,
            "defineProperty on URL": `Object.defineProperty(URL,'canParse',{value:fetch});export const a=URL.canParse(\`http://[\${h}]\`)`,
            "destructuring write of URL": `[URL]=[{canParse:fetch}];export const a=URL.canParse(\`http://[\${h}]\`)`,
            "element write on globalThis": `globalThis['URL']=Object;export const a=URL.canParse(\`http://[\${h}]\`)`,
            "Object.assign on globalThis": `Object.assign(globalThis,{URL:Object});export const a=URL.canParse(\`http://[\${h}]\`)`,
            "var URL": `var URL=Object;export const a=URL.canParse(\`http://[\${h}]\`)`,
            "member write of URL": `window.URL=Object;export const a=URL.canParse(\`http://[\${h}]\`)`,
        }
        for (const [name, js] of Object.entries(cases)) {
            it(name, () => expect(templateFlagged(js)).toBe(true))
        }

        it("helper in a file that is not an ES module", () => {
            expect(templateFlagged(`${SIMPLE}var a=xn(\`http://[\${h}]\`)`, { module: false })).toBe(true)
        })

        it("tamper in another chunk flags the helper form", () => {
            expect(templateFlagged(`${SIMPLE}${CALL}`, { extra: "URL.canParse=fetch" })).toBe(true)
        })

        it("tamper in another chunk flags a bare new URL(template)", () => {
            expect(templateFlagged("new URL(`http://[${e}]`);", { extra: "Object.defineProperty(URL,'canParse',{value:fetch})" })).toBe(true)
        })
    })
})
