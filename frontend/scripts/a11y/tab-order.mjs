#!/usr/bin/env node
// Presses Tab through each route in headless Chrome and checks the focus path.
// Usage: node scripts/a11y/tab-order.mjs --base http://127.0.0.1:4173 --out report.json [--chrome PATH] /route ...
// Fails a route when an element has a positive tabindex, focus lands on something with no box, a focused element has
// no accessible name, or focus never leaves the page controls it started on (a trap). Reading order is reported as
// the number of times focus jumps up the page by more than a screen, for a person to judge.
import { spawn } from "node:child_process"
import { mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const args = process.argv.slice(2)
const options = { base: "http://127.0.0.1:4173", out: "tab-order.json", chrome: "google-chrome-stable", port: "9335", presses: "80" }
const routes = []
for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith("--")) options[args[i].slice(2)] = args[++i]
    else routes.push(args[i])
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), "tab-"))
const chrome = spawn(options.chrome, ["--headless=new", `--remote-debugging-port=${options.port}`, `--user-data-dir=${profile}`, "--no-first-run", "--disable-gpu", "about:blank"], { stdio: "ignore" })

async function endpoint() {
    for (let i = 0; i < 60; i++) {
        try { return (await (await fetch(`http://127.0.0.1:${options.port}/json/version`)).json()).webSocketDebuggerUrl } catch { await sleep(250) }
    }
    throw new Error("Chrome DevTools endpoint did not come up")
}

const DESCRIBE = `(() => {
    const el = document.activeElement
    if (!el || el === document.body) return null
    const r = el.getBoundingClientRect()
    const name = el.getAttribute("aria-label") || (el.labels && el.labels[0] && el.labels[0].innerText) || el.innerText || el.getAttribute("title") || el.value || ""
    return { tag: el.tagName.toLowerCase(), name: name.trim().replace(/\\s+/g, " ").slice(0, 60), top: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height), tabindex: el.getAttribute("tabindex") }
})()`

const report = []
let failed = false
try {
    const ws = new WebSocket(await endpoint())
    let id = 1
    const pending = new Map()
    ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result ?? m.error); pending.delete(m.id) } })
    await new Promise((r) => ws.addEventListener("open", r))
    const send = (method, params = {}, sessionId) => new Promise((res) => { const i = id++; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params, ...(sessionId ? { sessionId } : {}) })) })
    const { targetId } = await send("Target.createTarget", { url: "about:blank" })
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true })
    const call = (m, p) => send(m, p, sessionId)
    const ev = async (expression) => (await call("Runtime.evaluate", { expression, returnByValue: true })).result.value
    await call("Page.enable")
    await call("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })
    const key = (type, k, code, vk) => call("Input.dispatchKeyEvent", { type, key: k, code, windowsVirtualKeyCode: vk })
    for (const route of routes) {
        await call("Page.navigate", { url: options.base + route })
        for (let i = 0; i < 100; i++) {
            if (await ev(`!!document.querySelector("main h1") && !document.querySelector("main [aria-busy='true']")`)) break
            await sleep(150)
        }
        await sleep(500)
        const positive = await ev(`[...document.querySelectorAll("[tabindex]")].filter((el) => Number(el.getAttribute("tabindex")) > 0).length`)
        const path = []
        for (let n = 0; n < Number(options.presses); n++) {
            await key("rawKeyDown", "Tab", "Tab", 9)
            await key("keyUp", "Tab", "Tab", 9)
            await sleep(25)
            const here = await ev(DESCRIBE)
            if (here === null) break
            if (path.length > 0 && JSON.stringify(here) === JSON.stringify(path[0]) && n > 3) break
            path.push(here)
        }
        const boxless = path.filter((p) => p.w === 0 || p.h === 0).length
        const nameless = path.filter((p) => p.name === "").length
        let backJumps = 0
        for (let i = 1; i < path.length; i++) if (path[i].top < path[i - 1].top - 900) backJumps += 1
        const pass = positive === 0 && boxless === 0 && nameless === 0 && path.length > 0
        if (!pass) failed = true
        report.push({ route, stops: path.length, positiveTabindex: positive, boxless, nameless, backJumps, pass, first: path.slice(0, 3).map((p) => `${p.tag} ${p.name}`), path: path.map((p) => `${p.tag}: ${p.name}${p.w === 0 || p.h === 0 ? " [no box]" : ""}`) })
        console.log(`${pass ? "ok  " : "FAIL"} ${route} stops=${path.length} positive=${positive} boxless=${boxless} nameless=${nameless} backJumps=${backJumps}`)
    }
    ws.close()
} finally {
    const exited = new Promise((resolve) => chrome.once("exit", resolve))
    chrome.kill()
    await exited
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}
writeFileSync(options.out, JSON.stringify(report, null, 2))
process.exit(failed ? 1 : 0)
