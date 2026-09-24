#!/usr/bin/env node
/**
 * The built page loads nothing from another host (issue #531).
 *
 * Runs over `dist/` -- what nginx actually serves -- after `vite build`:
 *
 *   npm run build && npm run check:dist
 *   node scripts/check-dist-self-contained.mjs --inventory   # also list every absolute URL found
 *
 * Four checks, recovered from the deleted backend landing-page test
 * (`TestLandingPageIsSelfContained`) and restated for a Vite bundle:
 *
 *   (a) No off-host subresource in emitted HTML or SVG. Every attribute that
 *       makes the browser fetch something -- `src`, `srcset`, `imagesrcset`,
 *       `poster`, `data`, `ping`, `background`, `manifest`, `href` and
 *       `xlink:href` on anything but a link, `<meta http-equiv=refresh>`,
 *       `<?xml-stylesheet href>`, and `srcdoc` (checked recursively) -- must
 *       stay on this origin or be a `data:` URI. Tags are matched with quotes
 *       respected, comments end where the HTML tokenizer ends them (`<!-->`,
 *       `<!--->`, `--!>` as well as `-->`), and attribute values are
 *       entity-decoded first. A manifest or JSON file may name no absolute
 *       URL at all.
 *   (b) Scripts load only from this origin. (a) covers `<script src>` and
 *       `modulepreload`. Emitted JS and inline `<script>` bodies are parsed
 *       with the TypeScript parser (already a devDependency), and every load
 *       call whose target is written in the code -- `import()`, static
 *       `import`/`export ... from`, `importScripts`, `fetch`, `sendBeacon`,
 *       `new EventSource`/`WebSocket`/`Worker`/`SharedWorker`, including
 *       `new Worker(new URL("..."))` -- must stay on this origin. The allow
 *       list in (d) does NOT apply to these. An inline `<script>` may name no
 *       absolute URL at all. A file the parser cannot parse fails.
 *   (c) No off-host style load. Every CSS `@import`, `url(...)`, and string
 *       inside `image-set()` -- in .css files, inline `<style>`, and every
 *       HTML/SVG attribute value -- after decoding CSS escapes such as
 *       `\75rl(`, must stay on this origin, be a `data:` URI, or be a
 *       `#fragment`.
 *   (d) Every absolute URL in the output must be on ALLOWED_URLS. It reads
 *       the RAW, unstripped text of every file -- comments included -- so no
 *       stripping step can hide an address; in JS it also reads every parsed
 *       string and template value (escapes decoded), and in CSS and markup
 *       the text after decoding escapes. The scheme may be in any case; tab,
 *       CR and LF inside a host are removed as a browser removes them. Each
 *       candidate is parsed with `new URL()` and matched by ORIGIN (scheme,
 *       host and port, so userinfo cannot disguise the host and
 *       `http://localhost:8000` is not `http://localhost`) and then by the
 *       exact normalised URL. A candidate that does not parse fails. A host
 *       built at run time fails, whether by template (`https://${h}`) or by
 *       concatenation (`"https://" + h`), unless it is one of the two places
 *       below: ALLOWED_RUNTIME_HOSTS (one 3Dmol.js loader), or a bare
 *       `new URL(template);` statement whose result is thrown away (zod's
 *       IPv6 check) in a file where nothing rebinds or reassigns `URL`. An
 *       allow-list entry that no longer matches anything fails, so the lists
 *       cannot rot wider.
 *
 * "Stay on this origin" is decided by the WHATWG URL parser against two
 * sentinel page origins, one https and one http, so the forms a browser
 * treats as off-host -- `/\host`, `\\host`, `https:host` on an http page,
 * tabs or newlines inside the URL -- are judged the way a browser judges them.
 *
 * Every file under dist/ is accounted for: text files are scanned by type,
 * images and fonts are on an explicit binary list, and any other extension
 * fails, so a new kind of emitted file cannot go unread.
 *
 * What this cannot prove, stated rather than papered over:
 *
 * - It is a tripwire against accidents (a CDN link, a web font, an analytics
 *   snippet, a build with the wrong VITE_API_BASE_URL), not a defence against
 *   someone hiding a URL on purpose: a string assembled at run time
 *   (`"htt" + "ps://"`) is invisible to any static scan.
 * - (d) allows URLs by address, not by use. Every entry is one exact URL, so
 *   a new load from, say, github.com fails unless it is one of those exact
 *   URLs, and (b) fails any written-out load call whatever the allow list
 *   says. But a URL that IS on the list passes when it is loaded any way (b)
 *   does not recognise: stored in a variable and then fetched, assigned to
 *   `img.src`, passed to `xhr.open`, called as `(0, window.fetch)(...)`, or
 *   used as a `<form action>`.
 * - A scheme-relative string such as `"http:host"` has no `//`, so (d) does
 *   not read it as a URL; stored in a variable and assigned to `.src` on an
 *   https page, a browser resolves it to `http://host`. (Written directly
 *   into an HTML attribute, (a) judges it correctly.)
 * - The CSS scan strips `/* ... *\/` comments before looking for `url()`
 *   without tracking strings, so a comment opener inside a CSS string can
 *   hide a later `url()` from (c). (d) still reads the raw text.
 * - A line break inside the PATH of an allow-listed URL ends it for (d), so
 *   a different path on that same allowed origin could read as the listed
 *   URL. The origin itself cannot be disguised this way: tab, CR and LF
 *   inside a host are removed before parsing.
 * - 3Dmol.js's remote loaders are in the bundle: base URLs it appends an id
 *   to (RCSB, PubChem) and a trajectory loader that prefixes `http://` to a
 *   caller's server. That the app never calls them rests on
 *   `GeometryViewer.tsx` only calling `addModel` with an in-memory XYZ
 *   string, which this script does not prove.
 *
 * A missing or empty `dist/` is a failure, never a pass.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs"
import { dirname, extname, join, relative } from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"

const DIST = join(dirname(fileURLToPath(import.meta.url)), "..", "dist")
const INVENTORY = process.argv.includes("--inventory")

/**
 * Absolute URLs allowed to APPEAR in the build, and why. Each entry matches
 * exactly one URL: same origin (scheme, host, port) and the same normalised
 * address. None of these may be loaded by the page: (a)-(c) refuse every
 * off-host load regardless of this list.
 */
const ALLOWED_URLS = [
    // Identifiers, never fetched.
    { url: "http://www.w3.org/2000/svg", why: "SVG namespace (React DOM, the SVG icons)" },
    { url: "http://www.w3.org/1999/xlink", why: "XLink namespace (React DOM)" },
    { url: "http://www.w3.org/1998/Math/MathML", why: "MathML namespace (React DOM)" },
    { url: "http://www.w3.org/XML/1998/namespace", why: "XML namespace (React DOM)" },
    { url: "http://json-schema.org/draft-04/schema#", why: "zod's JSON Schema `$schema` identifier" },
    { url: "http://json-schema.org/draft-07/schema#", why: "zod's JSON Schema `$schema` identifier" },
    { url: "https://json-schema.org/draft/2020-12/schema", why: "zod's JSON Schema `$schema` identifier" },
    { url: "http://localhost/", why: "React Router's placeholder base for `new URL()` parsing when there is no window; no port, never fetched" },
    // Text a person reads, never fetched.
    { url: "https://react.dev/errors/", why: "React's minified-error link; the error code is appended at run time" },
    { url: "https://reactrouter.com/en/main/routers/picking-a-router.", why: "text of a React Router error message" },
    { url: "https://github.com/ungap/url-search-params.", why: "text of a React Router warning" },
    { url: "https://github.com/kosua20/Rendu", why: "comment inside 3Dmol.js shader source (a string to the parser)" },
    { url: "https://github.com/molstar/molstar/blob/master/src/mol-gl/shader/fxaa.frag.ts", why: "comment inside 3Dmol.js shader source" },
    { url: "http://mrl.nyu.edu/~dzorin/cg05/lecture12.pdf", why: "comment inside 3Dmol.js shader source" },
    { url: "http://stackoverflow.com/questions/9595300/cylinder-impostor-in-glsl", why: "comment inside 3Dmol.js shader source" },
    // 3Dmol.js's own remote loaders: base URLs it appends an id to. The app does not call them (see header).
    { url: "https://files.rcsb.org/view/", why: "3Dmol.js loader base (fetch by PDB id)" },
    { url: "https://models.rcsb.org/", why: "3Dmol.js loader base (BinaryCIF by PDB id)" },
    { url: "https://mmtf.rcsb.org/v1.0/", why: "3Dmol.js loader base (MMTF), written protocol-relative" },
    { url: "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/", why: "3Dmol.js loader base (fetch by PubChem CID)" },
]

/**
 * Places a library builds a host at run time, and why each is tolerated.
 * Matched by the emitted chunk's name, the enclosing method's name (public
 * API, so it survives minification) and the literal the host is glued to.
 * An entry that no longer matches anything fails, like ALLOWED_URLS.
 */
const ALLOWED_RUNTIME_HOSTS = [
    {
        chunk: "3Dmol-", method: "setCoordinatesFromURL", literal: "http://",
        why: "3Dmol.js trajectory loader: prefixes http:// to a caller-supplied server URL. The app does not call it (see header)",
    },
]
const runtimeHostHits = new Set()

/** `<a href>` / `<area href>` targets are links a reader follows, not loads. None today. */
const ALLOWED_ANCHOR_URLS = []

/** Extensions read and scanned, by how. */
const TEXT_KINDS = new Map([
    [".html", "html"], [".htm", "html"],
    [".svg", "svg"],
    [".css", "css"],
    [".js", "js"], [".mjs", "js"], [".cjs", "js"],
    [".json", "manifest"], [".webmanifest", "manifest"],
])
/** Known, not scanned: a browser never loads anything on their behalf. */
const INERT_TEXT = new Map([[".txt", "plain text (the font licence) is displayed, never interpreted"]])
/** Known, not scanned: formats that cannot name a URL the browser will follow. */
const BINARY = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp", ".woff", ".woff2", ".ttf", ".otf", ".eot"])

const PAGE_BASES = [new URL("https://tckdb.invalid/assets/"), new URL("http://tckdb.invalid/assets/")]

const failures = []
const fail = (file, message) => failures.push(`${file}: ${message}`)

const counts = {
    loads: 0, scriptSrcs: 0, stylesheets: 0, anchors: 0, inlineScripts: 0, tags: 0, cssRefs: 0,
    loadCalls: 0, dynamicImports: 0, staticImports: 0, jsStrings: 0, discardedUrlParses: 0,
}

// ---------------------------------------------------------------- URL judgement

/** From an https AND an http page, the target stays on the page's origin or is data:. */
function loadVerdict(target) {
    for (const base of PAGE_BASES) {
        let url
        try {
            url = new URL(target, base)
        } catch {
            return "does not parse as a URL"
        }
        if (url.protocol === "data:") continue
        if (url.origin !== base.origin) return `resolves off-host to ${url.origin}`
    }
    return null
}

function checkLoad(file, label, target) {
    counts.loads += 1
    const verdict = loadVerdict(target)
    if (verdict) fail(file, `${label} ${JSON.stringify(target)} ${verdict}`)
}

function allowedBy(list, url) {
    return list.find((entry) => {
        const pattern = new URL(entry.url)
        return pattern.origin === url.origin && pattern.href === url.href
    })
}

// ---------------------------------------------------------------- decoding

/** CSS escapes: `\` + 1-6 hex digits (+ one optional space), or `\` + any other char. */
function cssUnescape(text) {
    return text.replace(/\\(?:([0-9a-fA-F]{1,6})[ \t\n\r\f]?|([\s\S]))/g, (m, hex, ch) => {
        if (hex) {
            const code = parseInt(hex, 16)
            return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : "�"
        }
        return ch === "\n" ? "" : ch
    })
}

const NAMED_ENTITIES = {
    amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", colon: ":", sol: "/", bsol: "\\", period: ".",
    Tab: "\t", NewLine: "\n", nbsp: " ", lpar: "(", rpar: ")", num: "#", quest: "?", commat: "@",
}

/** HTML character references. An unknown named one is reported, not guessed at. */
function entityDecode(text, onUnknown) {
    return text.replace(/&(#[xX][0-9a-fA-F]+|#[0-9]+|[A-Za-z][A-Za-z0-9]*);?/g, (m, ref) => {
        if (ref[0] === "#") {
            const code = ref[1] === "x" || ref[1] === "X" ? parseInt(ref.slice(2), 16) : parseInt(ref.slice(1), 10)
            return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : "�"
        }
        if (ref in NAMED_ENTITIES) return NAMED_ENTITIES[ref]
        if (onUnknown && m.endsWith(";")) onUnknown(m)
        return m
    })
}

// ---------------------------------------------------------------- absolute URL inventory (d)

// Browsers delete tab, CR and LF anywhere in a URL before parsing it, so a
// host written `localhost\t.evil.example` is `localhost.evil.example`. Inside
// the authority those three are absorbed whenever more host follows; in the
// path a tab is absorbed too, while a line break still ends the URL (text
// wraps; and a path cannot change which origin a URL points at).
const HOST_CHARS = String.raw`(?:[^\s"'\x60<>(){}\\/?#]|[\t\n\r]+(?=[^\s"'\x60<>(){}\\/?#]))`
const REST_CHARS = String.raw`(?:[^\s"'\x60<>(){}\\]|\t+(?=[^\s"'\x60<>(){}\\]))`
// scheme in any case, then one or more / or \, then authority and the rest.
const SCHEMED = new RegExp(String.raw`(?<![A-Za-z0-9+.-])(https?|wss?|ftp):[\\/]+(${HOST_CHARS}+)(${REST_CHARS}*)`, "gi")
// protocol-relative, where a URL value starts: after a quote, `=`, or at the start of a JS string.
const PROTOCOL_RELATIVE_IN_TEXT = new RegExp(String.raw`(?<=["'\x60=])\s*[\\/]{2}(${HOST_CHARS}+)(${REST_CHARS}*)`, "g")
const PROTOCOL_RELATIVE_AT_START = new RegExp(String.raw`^\s*[\\/]{2}(${HOST_CHARS}+)(${REST_CHARS}*)`, "g")
// a template piece that ends mid-authority, right before a `${...}`.
const OPEN_AUTHORITY = /(?<![A-Za-z0-9+.-])(https?|wss?|ftp):[\\/]*[^\s"'\x60<>(){}\\/?#]*$|^\s*[\\/]{2}[^\s"'\x60<>(){}\\/?#]*$/i

const seen = new Map() // href -> Set(file)

// What a DNS name or IP literal can look like once the URL parser has
// normalised it (IDN hosts are already punycode by then).
const PLAUSIBLE_HOST = /^(?:\[[0-9a-f:.]+\]|[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?(?:\.[a-z0-9_](?:[a-z0-9_-]*[a-z0-9_])?)*\.?)$/

function record(file, candidate, shown, onFound, { protocolRelative = false } = {}) {
    let url
    try {
        url = new URL(candidate.replace(/[\t\n\r]/g, ""))
    } catch {
        if (!protocolRelative) fail(file, `(d) absolute address that does not parse: ${shown}`)
        return
    }
    // `\/*$` in a regex source reads as `//` + host `*$` to a URL parser, but
    // no lookup can ever resolve such a host. A schemed URL gets no such pass.
    if (protocolRelative && !PLAUSIBLE_HOST.test(url.hostname)) return
    if (onFound) onFound(url)
    seen.set(url.href, (seen.get(url.href) ?? new Set()).add(file))
}

/**
 * Inventory every absolute URL in one piece of text. `atStart` = a whole JS
 * string value. `openEnd` = a template piece followed by `${...}`, whose
 * trailing half-written authority is judged by the template rule instead.
 */
function inventory(file, text, { atStart = false, onFound, openEnd = false } = {}) {
    for (let view of new Set([text, cssUnescape(text)])) {
        if (openEnd) view = view.replace(OPEN_AUTHORITY, "")
        for (const m of view.matchAll(SCHEMED)) record(file, `${m[1]}://${m[2]}${m[3]}`, m[0], onFound)
        for (const m of view.matchAll(atStart ? PROTOCOL_RELATIVE_AT_START : PROTOCOL_RELATIVE_IN_TEXT)) {
            record(file, `https://${m[1]}${m[2]}`, m[0].trim(), onFound, { protocolRelative: true })
        }
    }
}

/**
 * (d) over the raw text of a JS file, comments and all, so nothing the parser
 * skips can hide an address. Schemed URLs only: a regex literal such as
 * `/\/\/*$/` would otherwise read as a protocol-relative host. A URL cut off
 * by a `${` is a template, judged on the parsed tree instead.
 */
function inventoryRawJs(file, text) {
    for (const m of text.matchAll(SCHEMED)) {
        let [whole, scheme, host, rest] = m
        if (text[m.index + whole.length] === "{" && whole.endsWith("$")) {
            if (rest === "") continue // `https://${h}`: a host built at run time, judged on the tree
            rest = rest.slice(0, -1)
        }
        record(file, `${scheme}://${host}${rest}`, whole)
    }
}

// ---------------------------------------------------------------- CSS (c)

function balancedBody(text, openIndex) {
    let depth = 0
    for (let i = openIndex; i < text.length; i += 1) {
        if (text[i] === "(") depth += 1
        else if (text[i] === ")" && --depth === 0) return text.slice(openIndex + 1, i)
    }
    return text.slice(openIndex + 1)
}

/** Every URL a CSS text asks the browser to load, after decoding escapes. */
function cssTargets(rawCss) {
    const css = cssUnescape(rawCss.replace(/\/\*[\s\S]*?\*\//g, ""))
    const targets = []
    for (const m of css.matchAll(/url\(\s*(?:"([^"]*)"|'([^']*)'|([^)]*?))\s*\)/gi)) targets.push(m[1] ?? m[2] ?? m[3] ?? "")
    for (const m of css.matchAll(/@import\s*(?:"([^"]*)"|'([^']*)')/gi)) targets.push(m[1] ?? m[2])
    for (const m of css.matchAll(/(?:-webkit-)?image-set\s*\(/gi)) {
        const body = balancedBody(css, m.index + m[0].length - 1)
        for (const s of body.matchAll(/"([^"]*)"|'([^']*)'/g)) targets.push(s[1] ?? s[2])
    }
    return targets
}

function checkCss(file, css) {
    for (const target of cssTargets(css)) {
        counts.cssRefs += 1
        if (target.trim().startsWith("#")) continue
        checkLoad(file, "(c) style load", target)
    }
    inventory(file, css)
}

// ---------------------------------------------------------------- JS (b), (d)

const CALL_SINKS = new Set(["fetch", "importScripts", "sendBeacon"])
const NEW_SINKS = new Set(["EventSource", "WebSocket", "Worker", "SharedWorker"])

function calleeName(expression) {
    if (ts.isIdentifier(expression)) return expression.text
    if (ts.isPropertyAccessExpression(expression)) return expression.name.text
    return null
}

/** The written-out part of a load target, or null when it is computed. */
function literalTarget(node) {
    if (!node) return null
    if (ts.isParenthesizedExpression(node)) return literalTarget(node.expression)
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
    // `https://host/${x}` is judged by its head; `${base}/x` starts computed and cannot be.
    if (ts.isTemplateExpression(node)) return node.head.text === "" ? null : `${node.head.text}x`
    if (ts.isNewExpression(node) && calleeName(node.expression) === "URL") return literalTarget(node.arguments?.[0])
    return null
}

/**
 * `new URL(`http://[${v}]`);` as a bare statement: the URL is built only to
 * see whether it throws (zod's IPv6 check) and is then thrown away, so the
 * host it names is never fetched. Anything else that builds a host fails.
 */
function isDiscardedUrlParse(templatePiece, urlIsShadowed) {
    const template = templatePiece.parent
    const construct = template?.parent
    return (
        !urlIsShadowed &&
        ts.isTemplateExpression(template) &&
        ts.isNewExpression(construct) &&
        ts.isIdentifier(construct.expression) && // `new api.URL(...)` never qualifies
        construct.expression.text === "URL" &&
        construct.arguments?.length === 1 &&
        ts.isExpressionStatement(construct.parent)
    )
}

const BINDING_PARENTS = [
    ts.isVariableDeclaration, ts.isParameter, ts.isFunctionDeclaration, ts.isFunctionExpression,
    ts.isClassDeclaration, ts.isClassExpression, ts.isBindingElement, ts.isImportSpecifier,
    ts.isImportClause, ts.isNamespaceImport,
]

/**
 * Whether anything in the file could make `URL` mean something other than
 * the platform's: a declaration or binding named URL, an assignment to `URL`
 * or to any `x.URL`, or a `{ URL }` shorthand (counted, to fail closed).
 */
function shadowsUrl(sf) {
    let found = false
    const visit = (node) => {
        if (found) return
        const parent = node.parent
        if (ts.isIdentifier(node) && node.text === "URL" && parent) {
            if (parent.name === node && BINDING_PARENTS.some((is) => is(parent))) found = true
            if (ts.isShorthandPropertyAssignment(parent)) found = true
        }
        if (
            ts.isBinaryExpression(node) &&
            node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment &&
            node.operatorToken.kind <= ts.SyntaxKind.LastAssignment
        ) {
            const left = node.left
            if ((ts.isIdentifier(left) && left.text === "URL") || (ts.isPropertyAccessExpression(left) && left.name.text === "URL")) {
                found = true
            }
        }
        ts.forEachChild(node, visit)
    }
    visit(sf)
    return found
}

/** The literal text a JS value is known to END with, or null when it ends computed. */
function trailingText(node) {
    if (ts.isParenthesizedExpression(node)) return trailingText(node.expression)
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
    if (ts.isTemplateExpression(node)) return node.templateSpans.at(-1).literal.text
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
        const right = node.right
        if (ts.isStringLiteral(right) || ts.isNoSubstitutionTemplateLiteral(right)) return (trailingText(node.left) ?? "") + right.text
        return trailingText(right)
    }
    return null
}

/** Name of the nearest named function or method around a node. */
function enclosingName(node) {
    for (let n = node.parent; n; n = n.parent) {
        if ((ts.isMethodDeclaration(n) || ts.isFunctionDeclaration(n) || ts.isFunctionExpression(n)) && n.name) {
            return n.name.getText()
        }
    }
    return null
}

/** Whether a JS value is known to START with literal text. */
function startsLiteral(node) {
    if (ts.isParenthesizedExpression(node)) return startsLiteral(node.expression)
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text !== ""
    if (ts.isTemplateExpression(node)) return node.head.text !== ""
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) return startsLiteral(node.left)
    return false
}

function checkScript(file, source, { inline }) {
    const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS)
    if (sf.parseDiagnostics.length > 0) {
        const d = sf.parseDiagnostics[0]
        fail(file, `(b) JS does not parse, so it cannot be judged: ${ts.flattenDiagnosticMessageText(d.messageText, " ")} at ${d.start}`)
        return
    }
    const onFound = inline ? (url) => fail(file, `(b) inline script names an absolute URL: ${url.href}`) : undefined
    const urlIsShadowed = shadowsUrl(sf)
    inventoryRawJs(file, source)

    const sink = (label, target) => {
        counts.loadCalls += 1
        checkLoad(file, `(b) ${label} of`, target)
    }

    const visit = (node) => {
        if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
            counts.jsStrings += 1
            inventory(file, node.text, { atStart: true, onFound })
        } else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
            counts.jsStrings += 1
            const text = node.text ?? node.rawText ?? ""
            const openEnd = !ts.isTemplateTail(node) && OPEN_AUTHORITY.test(text)
            inventory(file, text, { atStart: ts.isTemplateHead(node), onFound, openEnd })
            if (openEnd && !isDiscardedUrlParse(node, urlIsShadowed)) {
                fail(file, `(d) template builds a host at run time: ${JSON.stringify(`${text}\${...}`)}`)
            } else if (openEnd) {
                counts.discardedUrlParses += 1
            }
        } else if (
            ts.isBinaryExpression(node) &&
            node.operatorToken.kind === ts.SyntaxKind.PlusToken &&
            !startsLiteral(node.right) &&
            OPEN_AUTHORITY.test(trailingText(node.left) ?? "\u0000")
        ) {
            // `"https://" + host`: the same run-time host as `https://${host}`.
            const literal = trailingText(node.left)
            const method = enclosingName(node)
            const allowed = ALLOWED_RUNTIME_HOSTS.find(
                (e) => file.split("/").at(-1).startsWith(e.chunk) && e.method === method && e.literal === literal,
            )
            if (allowed) runtimeHostHits.add(allowed)
            else fail(file, `(d) concatenation builds a host at run time: ${JSON.stringify(literal)} + ... in ${method ?? "(anonymous)"}`)
        } else if (ts.isCallExpression(node)) {
            const isImport = node.expression.kind === ts.SyntaxKind.ImportKeyword
            const name = isImport ? "import" : calleeName(node.expression)
            if (isImport || CALL_SINKS.has(name)) {
                const target = literalTarget(node.arguments[0])
                if (target !== null) {
                    if (isImport) counts.dynamicImports += 1
                    sink(`${name}()`, target)
                }
            }
        } else if (ts.isNewExpression(node) && NEW_SINKS.has(calleeName(node.expression))) {
            const target = literalTarget(node.arguments?.[0])
            if (target !== null) sink(`new ${calleeName(node.expression)}()`, target)
        } else if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
            counts.staticImports += 1
            sink(ts.isImportDeclaration(node) ? "import" : "export from", node.moduleSpecifier.text)
        }
        ts.forEachChild(node, visit)
    }
    visit(sf)
}

// ---------------------------------------------------------------- HTML / SVG (a), (b), (c)

const ATTR = /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>\x60]+)))?/g
const QUOTE_AWARE = String.raw`((?:"[^"]*"|'[^']*'|[^'">])*)`
const TAG = new RegExp(String.raw`<([a-zA-Z][\w:-]*)${QUOTE_AWARE}>`, "g")
const RAW_TEXT = new RegExp(String.raw`<(script|style)\b${QUOTE_AWARE}>([\s\S]*?)<\/\1\s*>`, "gi")

const LOAD_ATTRS = new Set(["src", "href", "xlink:href", "poster", "data", "background", "manifest", "codebase", "archive", "lowsrc", "dynsrc"])
const SRCSET_ATTRS = new Set(["srcset", "imagesrcset"])

function attributes(source, onUnknownEntity) {
    const attrs = []
    for (const m of source.matchAll(ATTR)) {
        attrs.push([m[1].toLowerCase(), entityDecode(m[2] ?? m[3] ?? m[4] ?? "", onUnknownEntity)])
    }
    return attrs
}

/**
 * Remove comments where the HTML tokenizer ends them: `<!-->` and `<!--->`
 * close at once, otherwise the first `-->` or `--!>` closes, else end of file.
 * A non-greedy `<!--[\s\S]*?-->` gets the first three wrong, and what it
 * swallows after `<!-->` is a real element to the browser.
 */
function stripComments(html) {
    let out = ""
    let i = 0
    for (;;) {
        const start = html.indexOf("<!--", i)
        if (start < 0) return out + html.slice(i)
        out += html.slice(i, start)
        const body = start + 4
        if (html.startsWith(">", body)) i = body + 1
        else if (html.startsWith("->", body)) i = body + 2
        else {
            const ends = [html.indexOf("-->", body), html.indexOf("--!>", body)]
            const closes = ends.map((at, k) => (at < 0 ? Infinity : at + (k === 0 ? 3 : 4)))
            i = Math.min(...closes)
            if (i === Infinity) return out
        }
    }
}

function checkMarkup(file, html) {
    const onUnknownEntity = (entity) => fail(file, `(a) unrecognised character reference ${entity} in markup`)

    // Raw-text elements first: their bodies are not markup, and a comment
    // marker inside a script must not hide a tag after it.
    for (const m of html.matchAll(RAW_TEXT)) {
        const body = m[3]
        if (m[1].toLowerCase() === "style") {
            checkCss(file, body)
        } else if (body.trim() !== "") {
            counts.inlineScripts += 1
            checkScript(`${file} (inline <script>)`, body.replace(/^\s*<!\[CDATA\[|\]\]>\s*$/g, ""), { inline: true })
        }
    }
    for (const m of html.matchAll(/<\?xml-stylesheet\b([\s\S]*?)\?>/gi)) {
        for (const [name, value] of attributes(m[1], onUnknownEntity)) {
            if (name === "href") checkLoad(file, "(c) <?xml-stylesheet href>", value)
        }
    }

    // (d) reads the raw, unstripped text: no stripping step can hide an address.
    inventory(file, html)
    inventory(file, entityDecode(html))

    const markup = stripComments(html.replace(RAW_TEXT, (m, tag, attrs) => `<${tag}${attrs}></${tag}>`))

    for (const m of markup.matchAll(TAG)) {
        const tag = m[1].toLowerCase()
        counts.tags += 1
        const attrs = attributes(m[2], onUnknownEntity)
        const attrMap = new Map(attrs)
        for (const [name, value] of attrs) {
            // Any attribute can carry CSS (style=, fill="url(...)", mask=...).
            if (/url\(|image-set|@import/i.test(cssUnescape(value))) checkCss(file, value)

            const label = `(a) <${tag} ${name}>`
            const isLink = (tag === "a" || tag === "area") && (name === "href" || name === "xlink:href")
            if (isLink) {
                counts.anchors += 1
                if (loadVerdict(value) === null) continue
                let url = null
                try {
                    url = new URL(value)
                } catch {
                    /* reported below */
                }
                if (!url || !allowedBy(ALLOWED_ANCHOR_URLS, url)) fail(file, `(d) link not on the anchor allow list: ${value}`)
            } else if (LOAD_ATTRS.has(name)) {
                if (tag === "script" && name === "src") counts.scriptSrcs += 1
                if (tag === "link" && /\bstylesheet\b/i.test(attrMap.get("rel") ?? "")) counts.stylesheets += 1
                checkLoad(file, label, value)
            } else if (SRCSET_ATTRS.has(name)) {
                for (const candidate of value.split(",")) {
                    const target = candidate.trim().split(/\s+/)[0]
                    if (target) checkLoad(file, label, target)
                }
            } else if (name === "ping") {
                for (const target of value.trim().split(/\s+/)) if (target) checkLoad(file, label, target)
            } else if (name === "srcdoc") {
                checkMarkup(`${file} (srcdoc)`, value)
            } else if (tag === "meta" && name === "content" && /^refresh$/i.test((attrMap.get("http-equiv") ?? "").trim())) {
                const target = /^\s*[\d.]*\s*[;,]?\s*(?:url\s*=\s*)?["']?([^"']*)/i.exec(value)?.[1] ?? ""
                if (target.trim()) checkLoad(file, "(a) <meta http-equiv=refresh>", target)
            }
        }
    }
}

// ---------------------------------------------------------------- main

function walk(dir) {
    const out = []
    for (const name of readdirSync(dir)) {
        const path = join(dir, name)
        if (statSync(path).isDirectory()) out.push(...walk(path))
        else out.push(path)
    }
    return out
}

if (!existsSync(DIST) || !statSync(DIST).isDirectory()) {
    console.error(`check-dist-self-contained: ${DIST} does not exist. Run \`npm run build\` first.`)
    process.exit(1)
}

const byKind = new Map()
const allFiles = walk(DIST)

for (const path of allFiles) {
    const file = relative(DIST, path)
    const ext = extname(path).toLowerCase()
    if (BINARY.has(ext)) continue
    const kind = TEXT_KINDS.get(ext) ?? (INERT_TEXT.has(ext) ? "inert" : null)
    if (!kind) {
        fail(file, `unknown file type ${JSON.stringify(ext || "(no extension)")}: add it to TEXT_KINDS, INERT_TEXT or BINARY in this script`)
        continue
    }
    byKind.set(kind, (byKind.get(kind) ?? 0) + 1)
    if (kind === "inert") continue

    const text = readFileSync(path, "utf8")
    if (kind === "html" || kind === "svg") checkMarkup(file, text)
    else if (kind === "css") checkCss(file, text)
    else if (kind === "js") checkScript(file, text, { inline: false })
    else if (kind === "manifest") {
        for (const view of new Set([text, JSON.stringify(safeJson(file, text) ?? "")])) {
            inventory(file, view, { onFound: (url) => fail(file, `(a) manifest/JSON names an absolute URL: ${url.href}`) })
        }
    }
}

function safeJson(file, text) {
    try {
        return JSON.parse(text)
    } catch {
        fail(file, "(a) JSON does not parse, so it cannot be judged")
        return null
    }
}

if (INVENTORY) {
    for (const [href, files] of [...seen].sort()) console.log(`${href}\t${[...files].join(", ")}`)
}

for (const [href, files] of seen) {
    if (!allowedBy(ALLOWED_URLS, new URL(href))) fail([...files].join(", "), `(d) absolute URL not on the allow list: ${href}`)
}
for (const entry of ALLOWED_URLS) {
    if (![...seen.keys()].some((href) => allowedBy([entry], new URL(href)))) {
        failures.push(`allow list: ${entry.url} matches nothing in the build any more; remove it from ALLOWED_URLS`)
    }
}

for (const entry of ALLOWED_RUNTIME_HOSTS) {
    if (!runtimeHostHits.has(entry)) {
        failures.push(`allow list: runtime host ${entry.chunk}* ${entry.method} "${entry.literal}" + ... matches nothing any more; remove it from ALLOWED_RUNTIME_HOSTS`)
    }
}

// Non-vacuity: the scan must have seen the build it claims to have checked.
const floors = [
    ["index.html present", existsSync(join(DIST, "index.html")) ? 1 : 0, 1],
    ["HTML files", byKind.get("html") ?? 0, 1],
    ["JS files", byKind.get("js") ?? 0, 10],
    ["CSS files", byKind.get("css") ?? 0, 1],
    ["SVG files", byKind.get("svg") ?? 0, 1],
    ["<script src> in HTML", counts.scriptSrcs, 1],
    ["stylesheet <link> in HTML", counts.stylesheets, 1],
    ["inline <script> in HTML (theme bootstrap)", counts.inlineScripts, 1],
    ["CSS url()/@import references (the self-hosted fonts)", counts.cssRefs, 6],
    ["literal dynamic import() in JS (lazy routes, the 3Dmol chunk)", counts.dynamicImports, 10],
    ["static imports in JS", counts.staticImports, 10],
    ["JS string values read", counts.jsStrings, 1000],
    ["absolute URLs inventoried", seen.size, 1],
]
for (const [what, got, floor] of floors) {
    if (got < floor) failures.push(`non-vacuity: expected at least ${floor} ${what}, found ${got}`)
}

const kinds = [...byKind].map(([k, n]) => `${n} ${k}`).join(", ")
console.log(
    `check-dist-self-contained: ${allFiles.length} files in dist (${kinds}); ${counts.tags} tags, ` +
        `${counts.loads} load targets judged, ${counts.inlineScripts} inline scripts, ${counts.cssRefs} CSS refs, ` +
        `${counts.loadCalls} written-out JS loads (${counts.dynamicImports} import(), ${counts.staticImports} static), ` +
        `${counts.jsStrings} JS strings, ${seen.size} distinct absolute URLs`,
)
if (failures.length > 0) {
    console.error(`FAIL: ${failures.length} problem(s)`)
    for (const f of failures) console.error(`  - ${f}`)
    process.exit(1)
}
console.log("OK: the built page loads nothing from another host")
