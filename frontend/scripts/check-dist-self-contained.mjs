#!/usr/bin/env node
/**
 * The built page loads nothing from another host (issue #531).
 *
 * Runs over `dist/` -- what nginx actually serves -- after `vite build`:
 *
 *   npm run build && npm run check:dist
 *
 * It checks four things, recovered from the deleted backend landing-page
 * test (`TestLandingPageIsSelfContained`) and restated for a Vite bundle:
 *
 *   (a) No off-host subresource in any emitted HTML: every `src`, `srcset`,
 *       `poster`, `data`, and every `href` that is not on an `<a>`/`<area>`,
 *       is relative, root-relative, or a `data:` URI.
 *   (b) Scripts load only from this origin: (a) covers `<script src>` and
 *       `<link rel=modulepreload>`; inline `<script>` blocks may not name any
 *       absolute URL; in emitted JS, every module-loading sink with a
 *       literal target (`import(...)`, `import ... from`, `importScripts`,
 *       `new Worker`/`SharedWorker`) must be relative.
 *   (c) No off-host style import: every CSS `@import` and `url(...)` (in
 *       .css files, inline `<style>`, and `style=""` attributes) is local,
 *       a `data:` URI, or a `#fragment`.
 *   (d) Every absolute address anywhere in emitted HTML/CSS/JS/SVG is on
 *       ALLOWED_ORIGINS below, each with the reason it is there. An entry
 *       nothing uses any more fails too, so the list cannot rot wider.
 *
 * What (b) and (d) cannot prove, stated rather than papered over: a static
 * scan cannot see a URL a script computes at run time, and it cannot tell
 * whether a string in a library is ever reached. The 3Dmol.js origins below
 * are that case: its remote loaders are in the bundle, and whether the app
 * reaches them is a claim about call sites (`GeometryViewer.tsx` only calls
 * `addModel` with an in-memory XYZ string), not something this script
 * proves. What it does guarantee is that no NEW absolute address, and no
 * absolute module load, reaches the build without someone adding it here.
 * It also means a build with `VITE_API_BASE_URL` set to another host fails,
 * which is the point: the served build talks to `/api/` on its own origin.
 *
 * A missing or empty `dist/` is a failure, never a pass.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import { dirname, extname, join, relative } from "node:path"
import { fileURLToPath } from "node:url"

const DIST = join(dirname(fileURLToPath(import.meta.url)), "..", "dist")

/**
 * Absolute origins allowed to APPEAR in the build, and why. Keyed by
 * `scheme://host` (or `//host` for a protocol-relative literal). None of
 * these may be a subresource: (a)-(c) refuse every absolute URL regardless
 * of this list. This list only governs (d), the inventory.
 */
const ALLOWED_ORIGINS = new Map([
    ["http://www.w3.org", "XML, SVG and XLink namespace identifiers (React DOM, the SVG icons); never fetched"],
    ["http://json-schema.org", "zod's JSON Schema `$schema` identifier (draft-07); never fetched"],
    ["https://json-schema.org", "zod's JSON Schema `$schema` identifier (2020-12); never fetched"],
    ["https://react.dev", "text of React's minified error messages"],
    ["https://reactrouter.com", "text of React Router's error messages"],
    ["http://localhost", "React Router's placeholder base for `new URL()` parsing when there is no window; never fetched"],
    ["https://github.com", "text of a React Router warning, and comments inside 3Dmol.js shader source"],
    ["http://mrl.nyu.edu", "comment inside 3Dmol.js shader source"],
    ["http://stackoverflow.com", "comment inside 3Dmol.js shader source"],
    ["https://files.rcsb.org", "3Dmol.js built-in loader (fetch by PDB id); the app does not call it"],
    ["https://models.rcsb.org", "3Dmol.js built-in loader (fetch by PDB id, BinaryCIF); the app does not call it"],
    ["//mmtf.rcsb.org", "3Dmol.js built-in loader (MMTF); the app does not call it"],
    ["https://pubchem.ncbi.nlm.nih.gov", "3Dmol.js built-in loader (fetch by PubChem CID); the app does not call it"],
])

/** `<a href>` / `<area href>` targets are links a reader follows, not loads. */
const ALLOWED_ANCHOR_ORIGINS = new Set([])

const SCANNED_EXTENSIONS = new Set([".html", ".css", ".js", ".mjs", ".svg"])

const failures = []
const fail = (file, message) => failures.push(`${file}: ${message}`)

// ---------------------------------------------------------------- helpers

const SCHEME = /^[a-z][a-z0-9+.-]*:/i

/** Relative or root-relative on this origin (no scheme, not `//host`). */
function isSameOrigin(target) {
    const t = target.trim()
    return !SCHEME.test(t) && !t.startsWith("//") && !t.startsWith("\\\\")
}

function isLocalOrData(target) {
    const t = target.trim()
    return isSameOrigin(t) || /^data:/i.test(t)
}

function walk(dir) {
    const out = []
    for (const name of readdirSync(dir)) {
        const path = join(dir, name)
        if (statSync(path).isDirectory()) out.push(...walk(path))
        else out.push(path)
    }
    return out
}

function attributes(source) {
    const attrs = []
    const re = /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g
    let m
    while ((m = re.exec(source))) attrs.push([m[1].toLowerCase(), m[2] ?? m[3] ?? m[4] ?? ""])
    return attrs
}

/** Every URL a CSS text asks the browser to load. */
function cssTargets(css) {
    const targets = []
    const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "")
    for (const m of stripped.matchAll(/url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*?))\s*\)/gi)) {
        targets.push(m[1] ?? m[2] ?? m[3] ?? "")
    }
    for (const m of stripped.matchAll(/@import\s+(?:"([^"]*)"|'([^']*)')/gi)) targets.push(m[1] ?? m[2])
    return targets
}

function checkCss(file, css, counts) {
    for (const target of cssTargets(css)) {
        counts.cssRefs += 1
        if (!isLocalOrData(target) && !target.trim().startsWith("#")) {
            fail(file, `(c) off-host style load: ${target}`)
        }
    }
}

/** Origins of every absolute address in a text, for the (d) inventory. */
function absoluteOrigins(text) {
    const origins = []
    const re = /\b((?:https?|wss?|ftp):\/\/[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)|["'`(](\/\/[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)/g
    for (const m of text.matchAll(re)) origins.push((m[1] ?? m[2]).toLowerCase())
    return origins
}

// ---------------------------------------------------------------- checks

function checkHtml(file, html, counts) {
    const withoutComments = html.replace(/<!--[\s\S]*?-->/g, "")

    for (const m of withoutComments.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi)) {
        const inline = m[2]
        if (inline.trim() !== "") {
            counts.inlineScripts += 1
            for (const origin of absoluteOrigins(inline)) fail(file, `(b) inline script names an absolute URL: ${origin}`)
        }
    }
    for (const m of withoutComments.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style\s*>/gi)) checkCss(file, m[1], counts)

    // Tags only; script/style bodies removed so their text is not read as markup.
    const markup = withoutComments
        .replace(/(<script\b[^>]*>)[\s\S]*?(<\/script\s*>)/gi, "$1$2")
        .replace(/(<style\b[^>]*>)[\s\S]*?(<\/style\s*>)/gi, "$1$2")
    for (const m of markup.matchAll(/<([a-zA-Z][\w:-]*)\b([^>]*)>/g)) {
        const tag = m[1].toLowerCase()
        for (const [name, value] of attributes(m[2])) {
            if (name === "style") checkCss(file, value, counts)
            const isAnchor = (tag === "a" || tag === "area") && name === "href"
            if (isAnchor) {
                counts.anchors += 1
                if (!isSameOrigin(value)) {
                    const origin = absoluteOrigins(`"${value}`)[0] ?? value
                    if (!ALLOWED_ANCHOR_ORIGINS.has(origin)) fail(file, `(d) link to an origin not on the anchor allow list: ${value}`)
                }
                continue
            }
            const loads = name === "src" || name === "href" || name === "xlink:href" || name === "poster" || name === "data"
            const candidates = name === "srcset" ? value.split(",").map((s) => s.trim().split(/\s+/)[0]) : loads ? [value] : []
            for (const target of candidates) {
                if (target === "") continue
                counts.subresources += 1
                if (tag === "script" && name === "src") counts.scriptSrcs += 1
                if (tag === "link" && /\bstylesheet\b/i.test(m[2])) counts.stylesheets += 1
                if (!isLocalOrData(target)) fail(file, `(a) off-host subresource <${tag} ${name}="${target}">`)
            }
        }
    }
}

function checkJs(file, js, counts) {
    const sinks = [
        /\bimport\s*\(\s*(["'`])([^"'`]*)\1/g,
        /\bimport\s*(["'`])([^"'`]*)\1/g,
        /\bfrom\s*(["'`])([^"'`]*)\1/g,
        /\bimportScripts\s*\(\s*(["'`])([^"'`]*)\1/g,
        /\bnew\s+(?:Shared)?Worker\s*\(\s*(["'`])([^"'`]*)\1/g,
    ]
    sinks.forEach((re, i) => {
        for (const m of js.matchAll(re)) {
            counts.moduleLoads += 1
            if (i === 0) counts.dynamicImports += 1
            if (i === 2) counts.staticImports += 1
            if (!isSameOrigin(m[2])) fail(file, `(b) script loads a module from off-host: ${m[0]}`)
        }
    })
}

// ---------------------------------------------------------------- main

if (!existsSync(DIST) || !statSync(DIST).isDirectory()) {
    console.error(`check-dist-self-contained: ${DIST} does not exist. Run \`npm run build\` first.`)
    process.exit(1)
}

const files = walk(DIST).filter((f) => SCANNED_EXTENSIONS.has(extname(f).toLowerCase()))
const byExt = (ext) => files.filter((f) => extname(f).toLowerCase() === ext)
const counts = {
    subresources: 0, scriptSrcs: 0, stylesheets: 0, anchors: 0, inlineScripts: 0,
    cssRefs: 0, moduleLoads: 0, dynamicImports: 0, staticImports: 0, absoluteAddresses: 0,
}
const seenOrigins = new Map()

for (const path of files) {
    const file = relative(DIST, path)
    const text = readFileSync(path, "utf8")
    const ext = extname(path).toLowerCase()
    if (ext === ".html") checkHtml(file, text, counts)
    if (ext === ".css") checkCss(file, text, counts)
    if (ext === ".js" || ext === ".mjs") checkJs(file, text, counts)
    for (const origin of absoluteOrigins(text)) {
        counts.absoluteAddresses += 1
        seenOrigins.set(origin, (seenOrigins.get(origin) ?? []).concat(file))
    }
}

for (const [origin, where] of seenOrigins) {
    if (!ALLOWED_ORIGINS.has(origin)) {
        fail([...new Set(where)].join(", "), `(d) absolute address to an origin not on the allow list: ${origin}`)
    }
}
for (const origin of ALLOWED_ORIGINS.keys()) {
    if (!seenOrigins.has(origin)) {
        failures.push(`allow list: ${origin} no longer appears in the build; remove it from ALLOWED_ORIGINS`)
    }
}

// Non-vacuity: the scan must have seen the build it claims to have checked.
// Each floor is something the current build has; a scan pointed at the wrong
// directory, or a parser that stopped matching, trips one of them.
const floors = [
    ["index.html present", existsSync(join(DIST, "index.html")) ? 1 : 0, 1],
    ["HTML files", byExt(".html").length, 1],
    ["JS files", byExt(".js").length + byExt(".mjs").length, 10],
    ["CSS files", byExt(".css").length, 1],
    ["<script src> in HTML", counts.scriptSrcs, 1],
    ["stylesheet <link> in HTML", counts.stylesheets, 1],
    ["inline <script> in HTML (theme bootstrap)", counts.inlineScripts, 1],
    ["CSS url()/@import references (the self-hosted fonts)", counts.cssRefs, 6],
    ["literal dynamic import() in JS (lazy routes, the 3Dmol chunk)", counts.dynamicImports, 10],
    ["static `from \"./chunk\"` imports in JS", counts.staticImports, 10],
    ["absolute addresses inventoried", counts.absoluteAddresses, 1],
]
for (const [what, got, floor] of floors) {
    if (got < floor) failures.push(`non-vacuity: expected at least ${floor} ${what}, found ${got}`)
}

console.log(
    `check-dist-self-contained: scanned ${files.length} files in ${relative(process.cwd(), DIST) || "."} ` +
        `(${byExt(".html").length} html, ${byExt(".js").length} js, ${byExt(".css").length} css, ${byExt(".svg").length} svg); ` +
        `${counts.subresources} HTML subresources, ${counts.inlineScripts} inline scripts, ${counts.cssRefs} CSS refs, ` +
        `${counts.moduleLoads} JS module loads, ${counts.absoluteAddresses} absolute addresses across ${seenOrigins.size} origins`,
)
if (failures.length > 0) {
    console.error(`FAIL: ${failures.length} problem(s)`)
    for (const f of failures) console.error(`  - ${f}`)
    process.exit(1)
}
console.log("OK: the built page loads nothing from another host")
