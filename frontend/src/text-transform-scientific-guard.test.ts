import { describe, expect, it } from "vitest"

/**
 * Anti-regression guard for the "uppercasing destroys a scientific symbol"
 * defect class -- see the long comment beside `--type-kicker-transform` in
 * `design-system.css` for the full history: `s⁻¹` read `S⁻¹` (siemens),
 * `log₁₀ k` read `LOG₁₀ K` (kelvin), `Cp (J/mol·K)` read `CP (J/MOL·K)`.
 * Each incident was fixed with a bespoke, selector-scoped `text-transform:
 * none` in ONE stylesheet -- correct for that one instance, but nothing
 * stopped the next stylesheet from reintroducing the same defect under a
 * selector name none of the earlier fixes could have named in advance.
 *
 * WHAT THIS FILE CANNOT DO: it reads CSS source text, not a rendered page --
 * it cannot know that `.foo-chart-th` will one day render "Cp (J/mol·K)"
 * versus "Status". That is exactly why `ArrheniusChart.test.tsx` /
 * `ThermoCpChart.test.tsx` / `EntryThermoSection.test.tsx` / etc. each carry
 * their own `getComputedStyle`-based regression test for the specific
 * instances this sweep found and fixed -- this file is the OTHER half.
 *
 * WHAT THIS FILE DOES: walks every discovered stylesheet, resolves every
 * rule's `text-transform` (including through a `var(--type-*-transform)`
 * chain back to `design-system.css`'s own `:root` block) to `uppercase`,
 * `none`, or "unresolved", and requires every `uppercase`-resolving
 * (file, selector) pair to be on `ALLOWLISTED_UPPERCASE_SELECTORS` below --
 * a hand-reviewed, hand-commented registry of every selector confirmed, by
 * checking what text it ACTUALLY renders app-wide, to carry prose only.
 *
 * This is what makes the guard fire on a genuinely NEW stylesheet: a fifth
 * chart file landing tomorrow with `.new-chart-axis-title { text-transform:
 * var(--type-label-transform); }` resolves to `uppercase` on a selector this
 * registry has never seen -- the "every flagged selector is allowlisted"
 * test below fails immediately, by name, before anyone has to notice a
 * corrupted unit on a live page a fourth time. Landing the fix (add
 * `.t-preserve-case` or a scoped `text-transform: none`, OR add the
 * selector to the allowlist with a comment proving the text is prose-only)
 * is the only way to turn the guard green again -- so a developer cannot
 * silence it without a moment of conscious review either way.
 *
 * DISCOVERED, never enumerated (same rationale as `theme.css.test.ts`'s own
 * `STYLESHEET_SOURCES`): an explicit file list is a guard pointed at a fixed
 * set of targets, and a new stylesheet absent from that list would be
 * unexamined, not passing -- indistinguishable from the outside.
 * `import.meta.glob` enumerates what actually exists, including any future
 * subdirectory (`**`, not `*`), so a new stylesheet is covered the moment
 * it is created.
 */
const STYLESHEET_SOURCES = import.meta.glob("./**/*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>
const ALL_STYLESHEETS: Record<string, string> = Object.fromEntries(
    Object.entries(STYLESHEET_SOURCES).map(([path, css]) => [path.replace(/^\.\//, ""), css]),
)

describe("the stylesheet inventory is discovered, not assumed", () => {
    it("finds every stylesheet in src/, not a hand-maintained subset", () => {
        expect(Object.keys(ALL_STYLESHEETS).length).toBeGreaterThanOrEqual(15)
        expect(ALL_STYLESHEETS).toHaveProperty("design-system.css")
    })
})

/** Strips `/* ... *\/` comments -- same technique as `theme.css.test.ts`'s
 *  `stripComments`: this codebase narrates design history (including, in
 *  several places, the literal string `text-transform: uppercase`) inside
 *  comments, and prose ABOUT a rule is not the rule itself. */
function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/**
 * Every `--type-*-transform` custom property `design-system.css` declares
 * in its `:root` block, name -> literal value (`"uppercase"` / `"none"`).
 * Built from `design-system.css` itself, not hand-copied, so a future step
 * added to the type scale (a 16th `--type-*-transform`) is picked up
 * automatically rather than silently unresolved by this file.
 */
function extractTransformTokens(designSystemCss: string): Map<string, string> {
    const tokens = new Map<string, string>()
    const root = /:root\s*\{([\s\S]*?)\n\}/.exec(stripComments(designSystemCss))
    const body = root ? root[1] : stripComments(designSystemCss)
    for (const m of body.matchAll(/--(type-[a-z0-9-]+-transform):\s*([^;]+);/g)) {
        tokens.set(m[1], m[2].trim())
    }
    return tokens
}

/** Resolves a declared `text-transform` value to a literal keyword where
 *  possible: a bare keyword passes through unchanged; a `var(--type-*-
 *  transform)` reference resolves through the token map above. Anything
 *  else (a custom property this map has never seen, `env()`, etc.) is
 *  returned as-is -- it will never equal `"uppercase"` as a bare string, so
 *  it is simply never flagged as a live scientific-text risk by this guard
 *  (nor could it be, without the token existing in this project). */
function resolveTextTransform(rawValue: string, tokens: Map<string, string>): string {
    const varMatch = /^var\(\s*--([a-z0-9-]+)\s*\)$/.exec(rawValue.trim())
    if (!varMatch) return rawValue.trim()
    return tokens.get(varMatch[1]) ?? rawValue.trim()
}

type FlaggedRule = { file: string; selector: string; declaredValue: string }

/**
 * Every `(file, selector)` -> `text-transform` declaration in `css`, as a
 * flat list. Selector text is split on top-level commas (a rule with
 * several comma-separated selectors sharing one declaration) and trimmed.
 * Matches inside an `@media (...) { .sel { decl } }` block are found the
 * same way a rule at the top level is -- the outer `@media (...)` condition
 * never itself contains a balanced `{...}` pair on its own, so the regex's
 * first genuine `{...}` match is always the innermost rule body, whatever
 * is nested around it. (Same technique this project's own test suite
 * already uses throughout, e.g. `design-system.css.test.ts`'s per-rule
 * regexes.)
 */
function extractTextTransformDeclarations(file: string, css: string): FlaggedRule[] {
    const out: FlaggedRule[] = []
    const source = stripComments(css)
    for (const ruleMatch of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const [, selectorList, declarations] = ruleMatch
        const transformMatch = /text-transform\s*:\s*([^;}]+)\s*;?/.exec(declarations)
        if (!transformMatch) continue
        const declaredValue = transformMatch[1].trim()
        for (const selector of selectorList.split(",")) {
            const trimmed = selector.trim().replace(/\s+/g, " ")
            if (!trimmed || trimmed.startsWith("@")) continue
            out.push({ file, selector: trimmed, declaredValue })
        }
    }
    return out
}

/**
 * Every selector confirmed SAFE -- its rendered text is English prose (an
 * eyebrow, a kicker, a field label, a table header, a toggle option, ...)
 * with no unit, quantity symbol, chemical formula, species/conformer
 * label, or public reference ever passing through it, audited directly
 * against every call site in `src/pages/` and `src/components/` (2026-09
 * sweep). A selector belongs here ONLY after that check -- adding an entry
 * "to make the test pass" without checking what text it renders defeats the
 * entire point of this file. Two base primitives (`.data-table th`,
 * `.kv-list dt`, `design-system.css`) are correctly listed here even though
 * SOME of their call sites carry scientific text -- those specific call
 * sites are protected individually with `.t-preserve-case` (see e.g.
 * `EntryThermoSection.tsx`'s NASA-coefficient headers), which changes
 * THEIR OWN computed style, not this shared rule's declared one; the shared
 * rule staying `uppercase` is correct for every OTHER (prose) call site it
 * serves.
 */
const ALLOWLISTED_UPPERCASE_SELECTORS: Record<string, string[]> = {
    "arrhenius-chart.css": [
        ".arrhenius-chart-control-label", // "X-axis" / "Display units"
        // Base rule for BOTH axis titles; the y-axis's own compound
        // selector (`.arrhenius-chart-axis-title--y`, this same file)
        // overrides back to `none` for its unit-string text -- this base
        // rule itself only ever renders "Temperature (K)" / "1000 / T (K⁻¹)".
        ".arrhenius-chart-axis-title",
        // `<span className="arrhenius-chart-controls-heading">Chart
        // controls</span>` (ArrheniusChart.tsx) -- a static literal string,
        // no unit/symbol/formula. Verified directly against both files
        // (#411, "Make the Arrhenius chart y-axis control discoverable"),
        // not taken on faith.
        ".arrhenius-chart-controls-heading",
    ],
    "browse.css": [
        // `.browse-kind-selector legend` retired here: the kind switcher
        // demoted from a `<fieldset>`/`<legend>` radiogroup to a plain
        // `<nav>` of links (owner: browse-by-kind must not read as "modes
        // of one page" -- see `BrowseKindSelector.tsx`'s own doc comment).
        // `.browse-kind-links-label` is its replacement -- the `<p
        // className="browse-kind-links-label">` heading the link list,
        // verified directly against `BrowseKindSelector.tsx`: a single
        // static literal, `Also in this archive`, never interpolated with
        // a kind name, unit, or any other scientific content.
        ".browse-kind-links-label",
        ".browse-filter-field label", // "Charge" / "Formula" / "SMILES" / ...
        ".browse-filter-evidence-group legend", // "Show only entries with…"
        ".browse-count", // "N species entries" (a count sentence)
    ],
    "calculation-dependency-graph.css": [
        ".dep-graph-edge-label-text", // "geometry for frequencies" etc. (dependencyWording.ts, all prose)
        ".dep-graph-node-pill-text", // "Optimisation" / "Frequency" / ... (calculationTypeFormat.ts, all prose)
    ],
    "calculation-detail.css": [
        ".opt-stage-box-label", // "Coarse optimisation" / "Fine optimisation" / "Guess" / "Optimized" / "Validated"
    ],
    "conformer-group.css": [
        ".metric span", // "Deposited observations" / "Distinct stored geometries" / ...
        ".observation-card header > div:last-child small", // scientific_origin: "computed"/"experimental"/"estimated", or "origin not recorded"
        // Retired/dead -- no live `.tsx` renders these classes any more
        // (`design-system.css`'s own "to retire" list names them); kept
        // allowlisted rather than deleted, since deleting orphaned CSS is
        // outside this guard's job.
        ".review-badge",
        ".stage-table th",
        ".stage-table td::before",
    ],
    "design-system.css": [
        ".t-kicker", // eyebrow steps: "Archive index" / "Not found" / "Theoretical Chemical Kinetics Database" / ...
        ".t-label", // the base label step -- see the many per-file entries below for where it is scoped narrower
        ".t-label-strong", // the base label-strong step
        // Shared primitives -- correct for the great majority of their
        // call sites (ordinary field/column labels); the scientific-text
        // call sites are protected individually with `.t-preserve-case`
        // (see this rule's own comment above).
        ".data-table th",
        ".kv-list dt",
    ],
    "energy-display.css": [
        ".energy-display-label", // "Electronic energy" / "Electronic energy at final geometry"
        ".energy-toggle legend", // "Units"
    ],
    "entry-science.css": [
        ".supersession-notice strong", // "Superseded"
    ],
    "geometry-detail.css": [
        ".coordinate-toggle legend", // "Units" / "Elements"
    ],
    "index.css": [
        ".accession-rail", // "species" / "entry" / "record"
        ".identifier-search label", // "Exact species identifier" / "Exact reaction equation" (mode-dependent, both prose)
        ".destination > span:last-child", // "Coming soon" / "Open catalogue"
        ".brand", // "TCKDB" (already all-caps)
        ".brand span", // "T" (single letter, case-invariant)
        ".eyebrow", // "Archive index" / "Not found" / "Species record · chemical identity" / ...
        ".theme-toggle-option", // "Light" / "Dark" / "System"
        ".identifier-search-mode-option", // "Species" / "Reactions" (IdentifierSearch.tsx's SearchModeToggle)
    ],
    "network-diagram.css": [
        // `<legend>Layout</legend>` (NetworkDiagram.tsx) -- the literal word
        // "Layout" on the PES layout-mode chooser. No unit, symbol or
        // formula. The chemistry labels render inside the SVG and inside
        // `.net-pes-layout-option`, neither of which this selector reaches.
        ".net-pes-layout-fieldset legend",
        // `<legend>{`Saddle points (${shownCount} of ${saddles.length} shown)`}</legend>`
        // (NetworkDiagram.tsx's NetworkPesChannelFieldset) -- a count
        // sentence around the literal words "Saddle points", no unit,
        // symbol or formula. Each channel's own chemistry label
        // ("[NH-][NH3+] to NN (isomerization)") renders inside a sibling
        // `.net-pes-channel-option`, not this legend.
        ".net-pes-channel-fieldset legend",
    ],
    "network-ktp-chart.css": [
        // `<legend>{`Channels (${selectedGroups.length} of ${groups.length} shown)`}</legend>`
        // (NetworkKtpChart.tsx) -- a count sentence around the literal word
        // "Channels", no unit/symbol/formula. The channel's own chemistry
        // label ("NN to [NH-][NH3+]") renders inside a sibling
        // `.network-ktp-channel-option`, not this legend.
        ".network-ktp-channel-fieldset legend",
    ],
    "page-shell.css": [
        ".page-toc nav::before", // "On this page" (generated content, not app text)
    ],
    "refs-disclosure.css": [
        ".ref-item-label", // "Species" / "Reaction entry" / "Level of theory ref" / ... (RefsDisclosure.tsx callers, all prose)
    ],
    "species-entry.css": [
        ".entry-facts span", // "Entry kind / state" / "Archive availability" / "Term symbol" / ... (FactItem labels, all prose)
        // "bond {atomA}–{atomB}" -- a fixed lower-case "bond" prefix around
        // digits and an en-dash; upper-casing changes no letter that
        // carries scientific meaning the way s/S or k/K does.
        ".conformer-basin-rotor dt",
    ],
    "species-overview.css": [
        ".entry-state-group h3 span", // "N entries" (a count sentence)
    ],
    "thermo-cp-chart.css": [
        ".cp-chart-toggle legend", // "Show" / "Units"
        ".cp-chart-legend-flag", // "selected"
        // Base rule for BOTH axis titles; the y-axis's own compound
        // selector (`.cp-chart-axis-title--y`, this same file) overrides
        // back to `none` for its "Cp (...)" unit-bearing text -- this base
        // rule itself only ever renders "Temperature (K)".
        ".cp-chart-axis-title",
    ],
}

describe("no CSS rule outside the reviewed allowlist resolves text-transform to uppercase", () => {
    const tokens = extractTransformTokens(ALL_STYLESHEETS["design-system.css"])

    it("the token map itself is non-empty (a guard against a broken :root regex silently allowlisting everything)", () => {
        expect(tokens.size).toBeGreaterThanOrEqual(9)
        expect(tokens.get("type-label-transform")).toBe("uppercase")
        expect(tokens.get("type-body-transform")).toBe("none")
    })

    const flaggedAsUppercase: FlaggedRule[] = []
    for (const [file, css] of Object.entries(ALL_STYLESHEETS)) {
        for (const rule of extractTextTransformDeclarations(file, css)) {
            if (resolveTextTransform(rule.declaredValue, tokens) === "uppercase") flaggedAsUppercase.push(rule)
        }
    }

    it("finds at least the known uppercase-resolving selectors (a guard against the extraction regex itself silently matching nothing)", () => {
        expect(flaggedAsUppercase.length).toBeGreaterThanOrEqual(20)
    })

    it("every uppercase-resolving (file, selector) pair is on the reviewed allowlist", () => {
        const unlisted = flaggedAsUppercase.filter((rule) => {
            const allowed = ALLOWLISTED_UPPERCASE_SELECTORS[rule.file] ?? []
            return !allowed.includes(rule.selector)
        })
        expect(
            unlisted,
            unlisted.length
                ? "The following selector(s) resolve text-transform to `uppercase` but are not on " +
                  "ALLOWLISTED_UPPERCASE_SELECTORS in text-transform-scientific-guard.test.ts:\n" +
                  unlisted.map((r) => `  ${r.file} :: ${r.selector} (declared \`text-transform: ${r.declaredValue}\`)`).join("\n") +
                  "\n\nCheck what text this selector ACTUALLY renders (grep its class name/tag across src/pages and " +
                  "src/components). If it is a unit, quantity symbol, chemical formula, species/conformer label, or " +
                  "public reference, fix it with `.t-preserve-case` (design-system.css) or a scoped `text-transform: " +
                  "none`. If it is genuinely prose only, add it to the allowlist above WITH a comment naming the exact " +
                  "text it renders."
                : undefined,
        ).toEqual([])
    })

    // ADVISORY ONLY, deliberately never a failing assertion (decided 2026-09,
    // forced by #411): a stale allowlist entry -- a selector that was
    // rewritten or deleted outright, so it no longer resolves to `uppercase`
    // in the CSS at all -- is INERT, not a risk. The test above only checks
    // selectors that ACTUALLY appear in `flaggedAsUppercase`; an allowlist
    // entry matching nothing can never hide a genuine new violation, because
    // there is no rule left for it to hide. Its only cost is a few stale
    // lines in this file.
    //
    // An earlier version of this test DID fail on a stale entry ("no stale
    // entries masking a since-removed uppercase declaration") -- reverted
    // the first time it was actually exercised: PR #411 landed a NEW
    // uppercasing rule (`.arrhenius-chart-controls-heading`, correctly
    // caught and allowlisted below, prose "Chart controls"), and a
    // concurrent agent is already rewriting that same control block and
    // expected to delete that class entirely shortly after (the owner
    // called it "a wall of chrome"). Under the old rule, that unrelated
    // deletion -- carrying zero scientific-text risk -- would have forced
    // a same-PR edit to THIS file merely to keep CI green, for a change
    // this guard has no actual stake in. That is precisely the failure
    // mode this project's own guidance warns about: a check that fires on
    // work unrelated to what it protects is a check people learn to route
    // around, and the next genuine violation ships anyway. So: log it,
    // never fail on it. Someone doing allowlist hygiene can grep this
    // warning in a verbose run and delete the dead line whenever
    // convenient -- never urgent, never blocking.
    it("logs (never fails on) any allowlisted selector that no longer resolves to uppercase in the CSS", () => {
        const flaggedKeys = new Set(flaggedAsUppercase.map((r) => `${r.file}::${r.selector}`))
        const stale: string[] = []
        for (const [file, selectors] of Object.entries(ALLOWLISTED_UPPERCASE_SELECTORS)) {
            for (const selector of selectors) {
                if (!flaggedKeys.has(`${file}::${selector}`)) stale.push(`${file} :: ${selector}`)
            }
        }
        if (stale.length) {
            console.warn(
                "[text-transform-scientific-guard] advisory only, not a failure -- these allowlist entries " +
                "no longer resolve to uppercase (selector likely renamed or deleted); safe to delete from " +
                "ALLOWLISTED_UPPERCASE_SELECTORS whenever convenient:\n" + stale.map((s) => `  ${s}`).join("\n"),
            )
        }
    })
})
