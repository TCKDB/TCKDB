#!/usr/bin/env node
// Screenshots routes at true viewport widths through the DevTools protocol (a plain --window-size is clamped to 500 px).
// Usage: node scripts/a11y/shoot.mjs --base http://127.0.0.1:4173 --out DIR [--prefix before_] [--zoom 1] [--chrome PATH] /route ...
// Each shot waits for a non-loading `main h1`; a route that never gets one fails the run.
// Pass: exactly one h1, and scrollWidth <= viewport width at 720 and below with no element sticking out of its scroll container.
import { spawn } from "node:child_process"
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const VIEWPORTS = [
    { name: "1440-light", width: 1440, height: 900, scheme: "light" },
    { name: "1440-dark", width: 1440, height: 900, scheme: "dark" },
    { name: "390", width: 390, height: 844, scheme: "light" },
    { name: "320", width: 320, height: 640, scheme: "light" },
]
// 200% browser zoom at 1440 leaves a 720 px layout viewport; `--zoom 1` adds it.
const ZOOM_VIEWPORT = { name: "1440-zoom200", width: 720, height: 450, scheme: "light" }
const MAX_SHOT_HEIGHT = 6000
const SETTLE_MS = 400
const READY_PROBE = `(() => !!document.querySelector("main h1") && !document.querySelector("main [aria-busy='true']"))()`

const args = process.argv.slice(2)
const options = { base: "http://127.0.0.1:4173", out: "shots", prefix: "", chrome: "google-chrome-stable", port: "9333", timeout: "15000" }
const routes = []
for (let i = 0; i < args.length; i++) {
    const a = args[i]
    if (a.startsWith("--")) options[a.slice(2)] = args[++i]
    else routes.push(a)
}
const viewports = options.zoom === "1" ? [...VIEWPORTS, ZOOM_VIEWPORT] : VIEWPORTS
if (routes.length === 0) {
    console.error("Give at least one route, e.g. /species")
    process.exit(2)
}

const slug = (route) => (route === "/" ? "home" : route.replace(/^\//, "").replace(/[^a-zA-Z0-9]+/g, "-").replace(/-+$/, ""))

const OVERFLOW_PROBE = `(() => {
    const vw = document.documentElement.clientWidth
    const clipsX = (el) => {
        for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
            const ox = getComputedStyle(p).overflowX
            if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") return true
        }
        return false
    }
    const offenders = []
    for (const el of document.body.querySelectorAll("*")) {
        const r = el.getBoundingClientRect()
        if (r.width === 0 || r.right <= vw + 1) continue
        if (clipsX(el)) continue
        offenders.push((el.tagName.toLowerCase() + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\\s+/).join(".") : "")) + " right=" + Math.round(r.right))
        if (offenders.length >= 20) break
    }
    return { scrollWidth: document.documentElement.scrollWidth, clientWidth: vw, h1Count: document.querySelectorAll("h1").length, offenders }
})()`

const profile = mkdtempSync(join(tmpdir(), "shoot-"))
const chrome = spawn(options.chrome, [
    "--headless=new", `--remote-debugging-port=${options.port}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--disable-gpu", "--hide-scrollbars", "about:blank",
], { stdio: "ignore" })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function endpoint() {
    for (let i = 0; i < 60; i++) {
        try {
            const res = await fetch(`http://127.0.0.1:${options.port}/json/version`)
            return (await res.json()).webSocketDebuggerUrl
        } catch { await sleep(250) }
    }
    throw new Error("Chrome DevTools endpoint did not come up")
}

function connect(url) {
    const ws = new WebSocket(url)
    let nextId = 1
    const pending = new Map()
    ws.addEventListener("message", (event) => {
        const msg = JSON.parse(event.data)
        if (msg.id && pending.has(msg.id)) {
            const { resolve, reject } = pending.get(msg.id)
            pending.delete(msg.id)
            if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result)
        }
    })
    const opened = new Promise((resolve, reject) => { ws.addEventListener("open", resolve); ws.addEventListener("error", reject) })
    const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
        const id = nextId++
        pending.set(id, { resolve, reject })
        ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }))
    })
    return { opened, send, close: () => ws.close() }
}

async function waitForReady(call, timeoutMs) {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
        const result = await call("Runtime.evaluate", { expression: READY_PROBE, returnByValue: true })
        if (result.result.value === true) return true
        await sleep(150)
    }
    return false
}

const report = []
let failed = false
try {
    const cdp = connect(await endpoint())
    await cdp.opened
    mkdirSync(options.out, { recursive: true })
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" })
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true })
    const call = (method, params) => cdp.send(method, params, sessionId)
    await call("Page.enable")
    for (const route of routes) {
        for (const vp of viewports) {
            await call("Emulation.setDeviceMetricsOverride", { width: vp.width, height: vp.height, deviceScaleFactor: 1, mobile: vp.width < 500 })
            await call("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: vp.scheme }] })
            await call("Page.navigate", { url: options.base + route })
            const ready = await waitForReady(call, Number(options.timeout))
            if (!ready) {
                failed = true
                report.push({ route, viewport: vp.name, pass: false, error: `main h1 not ready within ${options.timeout} ms` })
                console.log(`FAIL ${route} @${vp.name} main h1 not ready within ${options.timeout} ms (no screenshot taken)`)
                continue
            }
            await sleep(SETTLE_MS)
            const probe = (await call("Runtime.evaluate", { expression: OVERFLOW_PROBE, returnByValue: true })).result.value
            const metrics = await call("Page.getLayoutMetrics")
            const height = Math.min(Math.ceil(metrics.cssContentSize.height), MAX_SHOT_HEIGHT)
            const shot = await call("Page.captureScreenshot", {
                format: "png", captureBeyondViewport: true,
                clip: { x: 0, y: 0, width: vp.width, height, scale: 1 },
            })
            const file = join(options.out, `${options.prefix}${slug(route)}_${vp.name}.png`)
            writeFileSync(file, Buffer.from(shot.data, "base64"))
            const narrow = vp.width <= 720
            const ok = probe.h1Count === 1 && (!narrow || (probe.scrollWidth <= vp.width && probe.offenders.length === 0))
            if (!ok) failed = true
            report.push({ route, viewport: vp.name, file, ...probe, pass: ok })
            console.log(`${ok ? "ok  " : "FAIL"} ${route} @${vp.name} scrollWidth=${probe.scrollWidth} offenders=${probe.offenders.length} h1=${probe.h1Count}`)
        }
    }
    cdp.close()
} finally {
    const exited = new Promise((resolve) => chrome.once("exit", resolve))
    chrome.kill()
    await exited
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}
writeFileSync(join(options.out, `${options.prefix}report.json`), JSON.stringify(report, null, 2))
process.exit(failed ? 1 : 0)
