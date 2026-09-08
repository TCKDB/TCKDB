import { describe, expect, it } from "vitest"
import ts from "typescript"

/**
 * Anti-regression guard for the "a literal `--` must never render in
 * user-facing text" rule: the owner asked for it after finding one live on
 * `/methods` ("also stop using `--`"). Seven occurrences were swept out in
 * #419. Within hours a new one shipped in #429 (`MethodsIndexPage.tsx`'s
 * `intro`) -- because #419 was a sweep with no guard, so nothing stopped the
 * next PR from reintroducing the same defect in a file nobody thought to
 * check. This file is that guard, built the same way
 * `text-transform-scientific-guard.test.ts` is: it DISCOVERS every source
 * file rather than trusting a hand-maintained list, so a brand-new component
 * or page is covered the moment it is created, not the moment someone
 * remembers to add it here.
 *
 * WHAT COUNTS AS "USER-FACING TEXT" HERE, AND HOW IT IS FOUND:
 *
 *   1. Every JSX text node (`ts.isJsxText`) -- the plain text a component
 *      renders between tags. This is unambiguous: if it is a JsxText node,
 *      React puts it on the page.
 *   2. Every string literal and no-substitution template literal
 *      (`ts.isStringLiteral` / `ts.isNoSubstitutionTemplateLiteral`)
 *      ANYWHERE in application source, whether it is a JSX attribute value,
 *      a `const` a prop is threaded through, or a domain-module return
 *      value -- MINUS the two carve-outs below. This is broader than
 *      restricting to a fixed list of prop names (`intro`, `hint`, `label`,
 *      ...); the codebase's actual list, found by grepping call sites, is
 *      `caption`, `description`, `heading`, `hint`, `intro`, `kicker`,
 *      `label`, `note`, `placeholder`, `summary`, `title`, plus
 *      `aria-label`. A fixed list would need to be re-derived and edited by
 *      hand every time a new prop carries prose -- exactly the kind of
 *      enumeration `text-transform-scientific-guard.test.ts` rejects for
 *      stylesheets ("DISCOVERED, never enumerated"). Scanning every string
 *      literal and filtering out the two things that are demonstrably NOT
 *      prose (below) means a brand-new prop name needs no update here to
 *      stay covered.
 *
 *   The two carve-outs from (2), and why each is safe:
 *
 *   A. CODE-SHAPED STRINGS: a string that, once trimmed, is nothing but one
 *      or more space-separated kebab-case tokens (`looksLikeCodeShaped`
 *      below) -- i.e. it reads as a CSS class list such as
 *      `"value-pill value-pill--muted"` or `"kv-list--wide"` -- or a CSS
 *      custom-property reference such as `"var(--type-label-font)"`. BEM's
 *      own `block--modifier` convention means a double hyphen is the
 *      EXPECTED shape for a class name in this codebase (see
 *      `.value-pill--muted`, `.card--selected`, `.kv-list--wide` throughout
 *      `src/components` and `src/pages`), so the token pattern explicitly
 *      allows one OR two hyphens between segments. This is a content-shape
 *      test, not an attribute-name test -- it fires the same way whether the
 *      string sits directly in a `className="..."` attribute or is built up
 *      through a ternary/template literal and assigned to a `className`
 *      further down (`className={active ? "value-pill value-pill--muted" :
 *      "value-pill"}`), which an attribute-name-only filter would miss.
 *   B. DIAGNOSTIC / MODULE CODE: a string literal that is (a) the module
 *      specifier of an `import`/`export` statement, (b) the argument to
 *      `require(...)`, (c) the argument to `console.*(...)`, or (d) the
 *      argument to `new <Something>Error(...)` / `<Something>Error(...)`.
 *      None of these renders to a visitor -- they are developer-facing by
 *      construction -- so a `--` inside one is not the defect this rule
 *      targets, even though nothing about its shape looks like a class name.
 *
 *   Comments are handled for free: the TypeScript AST does not create nodes
 *   for `//` or `/* *\/` comments at all (they are trivia attached to the
 *   token that follows), so a `visit()` walk over the parsed tree never
 *   passes through one -- no separate comment-stripping regex is needed
 *   (contrast `text-transform-scientific-guard.test.ts`'s `stripComments`,
 *   required there only because it works over raw CSS text, not an AST).
 *
 * WHAT THIS FILE DOES NOT COVER (documented, not merely discovered later):
 *
 *   - `*.test.ts` / `*.test.tsx` files are excluded from the file set
 *     entirely. A `--` in an `it("...")` description or a hard-coded mock
 *     prop is developer-facing test narration, not text a reader of the
 *     archive ever sees rendered -- sweeping those would just be noise (the
 *     pre-guard repo state had hundreds of them, in test names and CSS
 *     selectors like `.card.card--derived`). If a test's OWN inline JSX
 *     render literally hard-codes user-facing prose containing `--`, this
 *     guard will not catch it there -- only in the component/page/domain
 *     file that is the actual source of that copy.
 *   - Only two shapes are recognised as "code, not prose": a class-name
 *     token list and a `var(--...)` reference. Nothing else is special-
 *     cased by shape. In particular, a hypothetical sentence written with
 *     NO spaces around its `--` and otherwise entirely lower-case with no
 *     punctuation (e.g. a string that was, token-for-token, indistinguishable
 *     from a kebab-case identifier) would slip past `looksLikeCodeShaped`
 *     undetected. Every real occurrence this guard's own sweep found (and
 *     fixed) was written ` -- ` with surrounding spaces, which is why this
 *     is a real, named gap rather than one discovered by surprise: the
 *     shape test relies on prose being written like prose.
 *   - Non-`.ts`/`.tsx` sources (Markdown, JSON, HTML) are not scanned --
 *     the rule as raised is specifically about rendered React text.
 *   - Diagnostic call detection (carve-out B above) only looks one level up
 *     the AST (the string literal's immediate parent call/new/import/export
 *     node) -- a string built by concatenation or interpolation one step
 *     further away from the diagnostic call site would not be exempted by
 *     it, and would instead need to clear carve-out A or be allowlisted.
 */

const SOURCE_FILES = import.meta.glob("./**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>

const ALL_SOURCES: Record<string, string> = Object.fromEntries(
    Object.entries(SOURCE_FILES)
        .map(([path, code]) => [path.replace(/^\.\//, ""), code])
        .filter(([path]) => !/\.test\.tsx?$/.test(path)),
)

describe("the source inventory is discovered, not assumed", () => {
    it("finds every non-test .ts/.tsx file in src/, not a hand-maintained subset", () => {
        expect(Object.keys(ALL_SOURCES).length).toBeGreaterThanOrEqual(100)
        expect(ALL_SOURCES).toHaveProperty("pages/MethodsIndexPage.tsx")
        expect(ALL_SOURCES).toHaveProperty("pages/LevelOfTheoryPage.tsx")
    })
})

/**
 * A string that, trimmed, is nothing but space-separated kebab-case tokens
 * (one or two hyphens between segments, per BEM's `block--modifier`
 * convention) or a `var(--...)` reference. See the header comment, carve-out
 * A, for what this is for and its documented edge case.
 */
const CODE_TOKEN = "[a-z0-9]+(?:-{1,2}[a-z0-9]+)*"
const CLASS_TOKEN_LIST_RE = new RegExp(`^${CODE_TOKEN}(?: ${CODE_TOKEN})*$`)

function looksLikeCodeShaped(raw: string): boolean {
    const trimmed = raw.trim()
    if (trimmed === "") return false
    if (CLASS_TOKEN_LIST_RE.test(trimmed)) return true
    if (/^var\(--[a-z0-9-]/.test(trimmed)) return true
    return false
}

/**
 * True when `node` (a string/template literal) sits directly in a
 * diagnostic-or-module position: an import/export module specifier, a
 * `require(...)` argument, a `console.*(...)` argument, or a
 * `new/plain ...Error(...)` argument. See the header comment, carve-out B.
 */
function isDiagnosticOrModulePosition(node: ts.Node): boolean {
    const parent = node.parent
    if (!parent) return false
    if ((ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent)) && parent.moduleSpecifier === node) return true
    if (ts.isCallExpression(parent)) {
        const calleeText = parent.expression.getText()
        if (calleeText === "require") return true
        if (/Error$/.test(calleeText)) return true
        if (ts.isPropertyAccessExpression(parent.expression) && parent.expression.expression.getText() === "console") return true
    }
    if (ts.isNewExpression(parent) && /Error$/.test(parent.expression.getText())) return true
    return false
}

type Candidate = { file: string; kind: string; text: string }

/**
 * Every JSX-text node and every non-code-shaped, non-diagnostic string /
 * template literal in `source` that contains a literal `--`, as a flat
 * list. Shared by the real discovery pass below and by this file's own
 * true-positive/true-negative self-tests, so both exercise the identical
 * extraction logic.
 */
function extractDoubleHyphenCandidates(file: string, source: string, isTsx: boolean): Candidate[] {
    const out: Candidate[] = []
    const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, isTsx ? ts.ScriptKind.TSX : ts.ScriptKind.TS)

    function record(kind: string, rawText: string) {
        if (rawText.includes("--") && !looksLikeCodeShaped(rawText)) {
            out.push({ file, kind, text: rawText.trim().replace(/\s+/g, " ").slice(0, 160) })
        }
    }

    function visit(node: ts.Node) {
        if (ts.isJsxText(node)) {
            record("JsxText", node.text)
        } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
            if (!isDiagnosticOrModulePosition(node)) record(ts.SyntaxKind[node.kind], node.text)
        } else if (ts.isTemplateExpression(node)) {
            // A template literal is one logical string, not one per quasi --
            // e.g. `dep-graph-node dep-graph-node--${kind}` is a code-shaped
            // class name whose "--" only looks unresolved because the head
            // quasi ends mid-token, right before the interpolation. Rebuild
            // the shape with a neutral "0" standing in for each `${...}` so
            // the token boundaries the interpolation would complete at
            // runtime are reconstructed for the code-shape test, while the
            // literal-inclusion test and the displayed text use the quasis
            // as written (an interpolation could itself supply a "--" at
            // runtime; that is outside what static source scanning can see,
            // and is the same limitation `looksLikeCodeShaped`'s own header
            // note already names).
            if (!isDiagnosticOrModulePosition(node)) {
                const rawConcat = node.head.text + node.templateSpans.map((s) => s.literal.text).join("")
                const shapeConcat = node.head.text + node.templateSpans.map((s) => `0${s.literal.text}`).join("")
                const displayConcat = node.head.text + node.templateSpans.map((s) => `\${…}${s.literal.text}`).join("")
                if (rawConcat.includes("--") && !looksLikeCodeShaped(shapeConcat)) {
                    out.push({ file, kind: "TemplateExpression", text: displayConcat.trim().replace(/\s+/g, " ").slice(0, 160) })
                }
            }
        }
        ts.forEachChild(node, visit)
    }
    visit(sf)
    return out
}

describe("the extraction mechanism itself: true positives and true negatives", () => {
    it("flags a literal `--` in JSX text", () => {
        const found = extractDoubleHyphenCandidates(
            "synthetic.tsx",
            `export const X = () => <p>identity -- two levels can share a method</p>`,
            true,
        )
        expect(found.map((f) => f.kind)).toContain("JsxText")
    })

    it("flags a literal `--` in a JSX attribute string, wherever it is threaded from", () => {
        const found = extractDoubleHyphenCandidates(
            "synthetic.tsx",
            `const intro = "table -- never grouped"\nexport const X = () => <Section intro={intro} />`,
            true,
        )
        expect(found.some((f) => f.text.includes("table -- never grouped"))).toBe(true)
    })

    it("does NOT flag a `--` inside a comment (the TS AST has no node for comments)", () => {
        const found = extractDoubleHyphenCandidates(
            "synthetic.tsx",
            `// this comment says -- do not flag me\nexport const X = () => <p>fine</p>`,
            true,
        )
        expect(found).toEqual([])
    })

    it("does NOT flag a BEM double-hyphen class-name list, in a className attribute or built up elsewhere", () => {
        const found = extractDoubleHyphenCandidates(
            "synthetic.tsx",
            `export const X = ({ on }: { on: boolean }) => {
                const cls = on ? "value-pill value-pill--muted" : "value-pill"
                return <span className={cls}>ok</span>
            }`,
            true,
        )
        expect(found).toEqual([])
    })

    it("does NOT flag a `var(--...)` custom-property reference", () => {
        const found = extractDoubleHyphenCandidates("synthetic.ts", `const style = { fontSize: "var(--type-label-font)" }`, false)
        expect(found).toEqual([])
    })

    it("does NOT flag a class name built across a template-literal interpolation (`dep-graph-node dep-graph-node--${kind}`)", () => {
        const found = extractDoubleHyphenCandidates(
            "synthetic.tsx",
            "export const X = ({ kind }: { kind: string }) => <div className={`dep-graph-node dep-graph-node--${kind}`} />",
            true,
        )
        expect(found).toEqual([])
    })

    it("DOES flag prose written as a template literal with an interpolation", () => {
        const found = extractDoubleHyphenCandidates(
            "synthetic.tsx",
            "export const X = ({ count }: { count: number }) => <p>{`${count} records -- filtered from the full set`}</p>",
            true,
        )
        expect(found.some((f) => f.kind === "TemplateExpression")).toBe(true)
    })

    it("does NOT flag a thrown Error message, a console call, or an import specifier", () => {
        const found = extractDoubleHyphenCandidates(
            "synthetic.ts",
            [
                `import { foo } from "./some--module"`,
                `throw new Error("missing field -- cannot continue")`,
                `console.warn("skipped -- see log")`,
            ].join("\n"),
            false,
        )
        expect(found).toEqual([])
    })
})

/**
 * Every (file, exact text) pair confirmed to legitimately need a literal
 * `--` -- e.g. because it names this very rule or quotes another PR's title
 * verbatim. Empty today: the 2026-09 sweep found and fixed every real
 * occurrence, and nothing legitimately needs one. An entry belongs here
 * ONLY with a comment naming why that exact text is not the defect this
 * guard exists to catch -- the same discipline
 * `ALLOWLISTED_UPPERCASE_SELECTORS` in `text-transform-scientific-guard.test.ts`
 * uses.
 */
const ALLOWLISTED_DOUBLE_HYPHENS: Record<string, string[]> = {}

describe("no literal `--` renders anywhere outside the reviewed allowlist", () => {
    const flagged: Candidate[] = []
    for (const [file, source] of Object.entries(ALL_SOURCES)) {
        flagged.push(...extractDoubleHyphenCandidates(file, source, file.endsWith(".tsx")))
    }

    it("every flagged candidate is on the reviewed allowlist", () => {
        const unlisted = flagged.filter((c) => !(ALLOWLISTED_DOUBLE_HYPHENS[c.file] ?? []).includes(c.text))
        expect(
            unlisted,
            unlisted.length
                ? "The following text render(s) a literal `--`, which must never appear in user-facing copy " +
                  "(replace it with a real em dash —, or restructure the sentence):\n" +
                  unlisted.map((c) => `  ${c.file} :: ${c.kind} :: ${JSON.stringify(c.text)}`).join("\n") +
                  "\n\nIf this really is a legitimate exception (not prose -- e.g. it quotes something verbatim), add " +
                  "the file and the EXACT text to ALLOWLISTED_DOUBLE_HYPHENS in double-hyphen-prose-guard.test.ts " +
                  "with a comment explaining why."
                : undefined,
        ).toEqual([])
    })

    // ADVISORY ONLY, never a failing assertion -- same rationale as
    // text-transform-scientific-guard.test.ts's own stale-entry check: a
    // rewritten/deleted call site can only make an allowlist entry inert,
    // never hide a genuine new violation, since the entry no longer matches
    // anything the scan finds.
    it("logs (never fails on) any allowlisted entry that no longer appears in the scanned sources", () => {
        const flaggedKeys = new Set(flagged.map((c) => `${c.file}::${c.text}`))
        const stale: string[] = []
        for (const [file, texts] of Object.entries(ALLOWLISTED_DOUBLE_HYPHENS)) {
            for (const text of texts) {
                if (!flaggedKeys.has(`${file}::${text}`)) stale.push(`${file} :: ${text}`)
            }
        }
        if (stale.length) {
            console.warn(
                "[double-hyphen-prose-guard] advisory only, not a failure -- these allowlist entries no longer " +
                "appear in the scanned sources; safe to delete from ALLOWLISTED_DOUBLE_HYPHENS whenever convenient:\n" +
                stale.map((s) => `  ${s}`).join("\n"),
            )
        }
    })
})
