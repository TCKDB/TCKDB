import { describe, expect, it } from "vitest"
// `?raw` loads each stylesheet as plain source text -- see the comment atop
// `geometry-detail.css.test.ts` for why this suffix (not a bare import) is
// required under this project's `css: true` vitest config.
import designSystemCss from "./design-system.css?raw"
import indexCss from "./index.css?raw"

// DISCOVERED, never enumerated -- same technique and same rationale as
// `theme.css.test.ts`'s `STYLESHEET_SOURCES`: an explicit file list is a
// guard pointed at a fixed set of targets, and a new stylesheet absent
// from that list would be unexamined, not passing, which is indistinguishable
// from the outside. `import.meta.glob` enumerates what actually exists.
const STYLESHEET_SOURCES = import.meta.glob("./*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>
const ALL_STYLESHEETS: Record<string, string> = Object.fromEntries(
    Object.entries(STYLESHEET_SOURCES).map(([path, css]) => [path.replace(/^\.\//, ""), css]),
)

describe("the stylesheet inventory is discovered, not assumed", () => {
    it("finds design-system.css among the discovered stylesheets", () => {
        expect(Object.keys(ALL_STYLESHEETS)).toContain("design-system.css")
    })
})

/** Extracts a single custom-property's declared value, e.g.
 *  `customProperty(css, "type-label-font")` returns the text after
 *  `--type-label-font:` up to (not including) the terminating `;`. */
function customProperty(css: string, name: string): string {
    const match = new RegExp(`--${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}:\\s*([^;]+);`).exec(css)
    if (!match) throw new Error(`--${name} not declared`)
    return match[1].trim()
}

/**
 * The type scale -- 13 steps, weights 400/600 only. Pins every value in
 * the brief's table exactly, so a future edit that quietly drifts a size
 * (the same failure mode that produced 49 distinct font sizes across the
 * live site before this file existed) fails here instead of shipping.
 */
describe("type scale: every step declares the exact font/tracking/transform", () => {
    const STEPS: Record<string, { font: string; tracking: string; transform: string }> = {
        "display-1": { font: "600 clamp(2.75rem, 5vw, 4rem)/1.0 var(--display)", tracking: "-.03em", transform: "none" },
        "display-2": { font: "600 2.25rem/1.15 var(--display)", tracking: "-.02em", transform: "none" },
        "heading-1": { font: "600 1.75rem/1.2 var(--display)", tracking: "-.02em", transform: "none" },
        "heading-2": { font: "600 1.25rem/1.3 var(--display)", tracking: "0em", transform: "none" },
        "heading-3": { font: "600 .875rem/1.4 var(--sans)", tracking: "0em", transform: "none" },
        "kicker": { font: "400 .8125rem/1.3 var(--sans)", tracking: "0em", transform: "none" },
        "label": { font: "400 .8125rem/1.3 var(--sans)", tracking: "0em", transform: "none" },
        // Post-review addition: the label step at weight 600, for a
        // table header that must stand out from the surrounding
        // label-weight dt/th text -- see design-system.css's own
        // comment above --type-label-strong-font.
        "label-strong": { font: "600 .8125rem/1.3 var(--sans)", tracking: "0em", transform: "none" },
        "body": { font: "400 1.0625rem/1.55 var(--sans)", tracking: "0em", transform: "none" },
        // Post-review addition (species-entry/browse/chrome residuals
        // re-review, item 14): a named step for a page LEDE -- serif,
        // one size up from --type-body and a touch tighter in line-height
        // (a lede is a short 1-2 sentence read, not multi-paragraph body
        // copy) -- see design-system.css's own comment above
        // --type-lede-font for the three bespoke serif declarations this
        // replaces.
        "lede": { font: "400 1.15rem/1.5 var(--sans)", tracking: "0em", transform: "none" },
        "value": { font: "400 .9375rem/1.45 var(--sans)", tracking: "0em", transform: "none" },
        "data": { font: "400 .8125rem/1.5 var(--mono)", tracking: "0em", transform: "none" },
        "note": { font: "400 .8125rem/1.5 var(--sans)", tracking: "0em", transform: "none" },
        "ui": { font: "600 .8125rem/1.3 var(--sans)", tracking: "0em", transform: "none" },
        "data-large": { font: "600 1.5rem/1.2 var(--mono)", tracking: "0em", transform: "none" },
    }

    it("declares exactly the 15 named steps (the brief's original 13, plus post-review's --type-label-strong and --type-lede), no more, no fewer", () => {
        const fontVarNames = new Set(
            [...designSystemCss.matchAll(/--(type-[a-z0-9-]+)-font:/g)].map((m) => m[1]),
        )
        expect(fontVarNames).toEqual(new Set(Object.keys(STEPS).map((step) => `type-${step}`)))
    })

    for (const [step, expected] of Object.entries(STEPS)) {
        describe(`--type-${step}`, () => {
            it("font shorthand matches exactly", () => {
                expect(customProperty(designSystemCss, `type-${step}-font`)).toBe(expected.font)
            })
            it("tracking matches exactly", () => {
                expect(customProperty(designSystemCss, `type-${step}-tracking`)).toBe(expected.tracking)
            })
            it("transform matches exactly", () => {
                expect(customProperty(designSystemCss, `type-${step}-transform`)).toBe(expected.transform)
            })
        })
    }

    it("every step has a corresponding .t-<step> utility class applying all three", () => {
        for (const step of Object.keys(STEPS)) {
            const rule = new RegExp(
                `\\.t-${step}\\s*\\{[^}]*font:\\s*var\\(--type-${step}-font\\)[^}]*letter-spacing:\\s*var\\(--type-${step}-tracking\\)[^}]*text-transform:\\s*var\\(--type-${step}-transform\\)`,
            )
            expect(designSystemCss, `.t-${step} does not wire up all three tokens`).toMatch(rule)
        }
    })
})

describe("spacing scale: 4px base, eight steps", () => {
    const STEPS: Record<string, string> = {
        "1": ".25rem", "2": ".5rem", "3": ".75rem", "4": "1rem",
        "5": "1.5rem", "6": "2rem", "7": "3rem", "8": "4rem",
    }
    for (const [step, value] of Object.entries(STEPS)) {
        it(`--s-${step} is ${value}`, () => {
            expect(customProperty(designSystemCss, `s-${step}`)).toBe(value)
        })
    }
})

describe("measures narrowed for a readable line length", () => {
    it("--measure-prose is 40rem (was 64rem)", () => {
        expect(customProperty(indexCss, "measure-prose")).toBe("40rem")
    })
    it("--measure-note is 44rem (was 92rem)", () => {
        expect(customProperty(indexCss, "measure-note")).toBe("44rem")
    })
})

describe("--measure-wide: a separate CONTAINER measure, not a text one (post-review)", () => {
    // MEASURED (the finding this guards): `.record-header`
    // (`calculation-detail.css`) used `--measure-note` back when that
    // token was still 92rem, to line its right edge up with the wide
    // evidence strip/tables below it. Narrowing `--measure-note` to a
    // genuine prose width (44rem, above) silently narrowed that
    // container too. `--measure-wide` keeps "prose gets narrower" and
    // "this container stays wide" independently adjustable.
    it("design-system.css declares --measure-wide as 92rem", () => {
        expect(customProperty(designSystemCss, "measure-wide")).toBe("92rem")
    })

    it("calculation-detail.css's .record-header uses --measure-wide, not --measure-note", () => {
        const rule = /\.record-header\s*\{([^}]*)\}/.exec(ALL_STYLESHEETS["calculation-detail.css"])
        expect(rule, ".record-header rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/max-width:\s*var\(--measure-wide\)/)
        expect(rule![1]).not.toMatch(/max-width:\s*var\(--measure-note\)/)
    })
})

describe("index.css gives code/pre/kbd/samp the mono face, killing the UA fallback", () => {
    it("declares the global rule", () => {
        expect(indexCss).toMatch(/code,\s*pre,\s*kbd,\s*samp\s*\{[^}]*font-family:\s*var\(--mono\)/)
    })
})

/** Strips `/* ... *\/` comments -- same technique and rationale as
 *  `theme.css.test.ts`'s `stripComments`: stylesheet comments mention
 *  values by name (several quote a literal such as `font-weight: 700`),
 *  and prose mentioning a literal is not a live declaration of it. */
function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

describe("font weights are 400, 600 or 700 only, in every stylesheet", () => {
    const ALLOWED = new Set(["400", "600", "700"])
    const SIZE = /(?:\d*\.?\d+(?:px|rem|em|%|pt)|clamp\()/

    /** Every numeric weight in the text before the size of a `font` shorthand value (`italic 500 1rem ...`). */
    function shorthandWeights(value: string): string[] {
        const size = SIZE.exec(value)
        const head = size ? value.slice(0, size.index) : value
        return head.split(/\s+/).filter((token) => /^\d{3}$/.test(token))
    }

    /** Every weight a stylesheet's rules declare. `@font-face` ranges declare what a file ships, not a use. */
    function weightsDeclared(css: string): string[] {
        const body = stripComments(css).replace(/@font-face\s*\{[^}]*\}/g, "")
        const out: string[] = []
        for (const match of body.matchAll(/(?<![\w-])font-weight\s*:\s*([^;}]+)/g)) {
            out.push(...match[1].trim().split(/\s+/).filter((token) => /^\d+$/.test(token) || /^(lighter|bolder)$/.test(token)))
        }
        for (const match of body.matchAll(/(?<![\w-])font\s*:\s*([^;}]+)/g)) out.push(...shorthandWeights(match[1]))
        for (const match of body.matchAll(/--type-[\w-]+-font\s*:\s*([^;}]+)/g)) out.push(...shorthandWeights(match[1]))
        return out
    }

    for (const [name, css] of Object.entries(ALL_STYLESHEETS)) {
        it(`${name} declares only weights 400, 600 and 700`, () => {
            expect(weightsDeclared(css).filter((weight) => !ALLOWED.has(weight))).toEqual([])
        })
    }

    it("finds declared weights and rejects others (the scan is not vacuous)", () => {
        const all = Object.values(ALL_STYLESHEETS).flatMap(weightsDeclared)
        expect(all.length).toBeGreaterThan(30)
        expect(weightsDeclared(".a { font-weight: 500; } .b { font: 300 1rem var(--sans); } .c { font-weight: 600; }")).toEqual(["500", "600", "300"])
        expect(weightsDeclared(".a { font: italic 500 1rem var(--sans); }")).toEqual(["500"])
        expect(weightsDeclared(".a { font: 700 1rem var(--sans); }")).toEqual(["700"])
    })
})

describe("exactly one .kv-list dt size rule across src/**/*.css", () => {
    // `.kv-list dt` must not be defined twice
    // (`calculation-detail.css` at .6875rem, `entry-science.css`'s scoped
    // `.science-record .kv-list dt` at .64rem) -- two different sizes for
    // the same semantic role, depending on which page happened to load.
    // A single canonical rule here means only ONE `dt` selector inside a
    // `.kv-list` context may set font-size (via `font:` shorthand or the
    // longhand) anywhere in the app.
    it("only design-system.css's .kv-list dt sets a size for that role", () => {
        const matches: string[] = []
        for (const [name, css] of Object.entries(ALL_STYLESHEETS)) {
            // Matches `.kv-list dt { ... }` or `<selector-prefix> .kv-list dt { ... }`,
            // whether the size is set via `font:` shorthand or a bare `font-size:`.
            const rule = /(?:[^{},]*\s)?\.kv-list\s+dt\s*\{([^}]*)\}/g
            for (const m of css.matchAll(rule)) {
                if (/font(-size)?\s*:/.test(m[1])) matches.push(name)
            }
        }
        expect(matches).toEqual(["design-system.css"])
    })
})

describe("the disclosure primitive", () => {
    it("hides the UA marker and draws its own chevron that rotates on [open]", () => {
        expect(designSystemCss).toMatch(/\.disclosure\s*>\s*summary\s*\{[^}]*list-style:\s*none/)
        expect(designSystemCss).toMatch(/\.disclosure\s*>\s*summary::-webkit-details-marker\s*\{[^}]*display:\s*none/)
        expect(designSystemCss).toMatch(/\.disclosure\s*>\s*summary::before\s*\{[^}]*content:/)
        expect(designSystemCss).toMatch(/\.disclosure\[open]\s*>\s*summary::before\s*\{[^}]*transform:\s*rotate\(90deg\)/)
    })

    it("summary uses --type-ui, not an uppercase transform", () => {
        const rule = /\.disclosure\s*>\s*summary\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/font:\s*var\(--type-ui-font\)/)
        expect(rule![1]).not.toMatch(/text-transform/)
    })

    // `.disclosure--inset` -- the named third disclosure box style: a
    // disclosure inset inside an already-boxed card (border-top only, no
    // radius, no background). It replaces a page-scoped override in
    // `species-entry.css` (`.conformer-card .refs-disclosure`);
    // `species-entry.css.test.ts` and `ConformerSelector.test.tsx` assert
    // this modifier and the absence of the page override.
    describe(".disclosure--inset", () => {
        it("is a border-top-only, no-radius, no-background box, using --line-2 (the same token the conformer basin box's own border-top uses)", () => {
            const rule = /\.disclosure\.disclosure--inset\s*\{([^}]*)\}/.exec(designSystemCss)
            expect(rule, ".disclosure.disclosure--inset rule not found").not.toBeNull()
            expect(rule![1]).toMatch(/margin-top:\s*0\b/)
            expect(rule![1]).toMatch(/border:\s*0\b/)
            expect(rule![1]).toMatch(/border-top:\s*1px solid var\(--line-2\)/)
            expect(rule![1]).toMatch(/border-radius:\s*0\b/)
            expect(rule![1]).toMatch(/background:\s*none\b/)
            expect(rule![1]).not.toMatch(/border-bottom/)
        })

        it("removes the open-summary border-bottom the base .disclosure[open] > summary rule draws", () => {
            const rule = /\.disclosure\.disclosure--inset\[open]\s*>\s*summary\s*\{([^}]*)\}/.exec(designSystemCss)
            expect(rule, ".disclosure.disclosure--inset[open] > summary rule not found").not.toBeNull()
            expect(rule![1]).toMatch(/border-bottom:\s*none/)
        })

        it("uses a compound .disclosure.disclosure--inset selector (two classes), not a bare .disclosure--inset -- so it outranks .refs-disclosure's own border-color override regardless of stylesheet load order", () => {
            expect(designSystemCss).toMatch(/\.disclosure\.disclosure--inset\s*\{/)
            expect(designSystemCss).not.toMatch(/(?<!\.disclosure)\.disclosure--inset\s*\{/)
        })
    })
})

describe("the kv-list primitive is defined exactly once, globally", () => {
    it("design-system.css declares the base grid with an auto-fit column track, 16rem (, widened from 12rem)", () => {
        const rule = /\.kv-list\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(16rem,\s*1fr\)\)/)
    })

    // Post-review fix: the two deleted per-page rules each zeroed the UA
    // `dl` default margin with their OWN value (1rem 1.1rem 1.1rem vs.
    // 1rem 0 0) and neither carried over -- a bare `<dl class="kv-list">`
    // was falling back to the browser default, different from both.
    it("margin is reset to 0 -- the primitive carries no external-spacing opinion of its own", () => {
        const rule = /\.kv-list\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/margin:\s*0\b/)
    })

    it("calculation-detail.css no longer defines its own bare .kv-list rule", () => {
        expect(ALL_STYLESHEETS["calculation-detail.css"]).not.toMatch(/(?<![\w.-])\.kv-list\s*\{/)
    })

    it("entry-science.css no longer defines its own scoped .science-record .kv-list rule", () => {
        expect(ALL_STYLESHEETS["entry-science.css"]).not.toMatch(/\.science-record\s+\.kv-list\s*\{/)
    })
})

describe("exactly one bare .table-scroll rule across src/**/*.css (post-review)", () => {
    // MEASURED (the finding this guards): design-system.css, entry-
    // science.css and geometry-detail.css each defined a byte-identical
    // `.table-scroll { overflow-x: auto }` -- the exact "duplicate bare
    // selector, only safe because Vite never unloads a route's CSS"
    // pattern this PR's own header comment names as the bug being fixed
    // for .kv-list. Scoped/compound selectors like `.science-record
    // .table-scroll .stage-table` are a DIFFERENT rule (a different
    // property, min-width, on a different element) and are not counted
    // here -- only a rule whose selector is the bare class alone.
    it("only design-system.css declares a bare .table-scroll rule", () => {
        const names: string[] = []
        for (const [name, css] of Object.entries(ALL_STYLESHEETS)) {
            if (/(?<![\w.-])\.table-scroll\s*\{/.test(stripComments(css))) names.push(name)
        }
        expect(names).toEqual(["design-system.css"])
    })
})

describe("the data-table primitive", () => {
    // `th` reaches for the named `--type-label-strong` step rather than
    // `font: var(--type-label-font)` plus a bolted-on `font-weight: 600`
    // (a 14th ad-hoc style outside the scale).
    it("th uses the named --type-label-strong step, muted colour, left-aligned", () => {
        const rule = /\.data-table\s+th\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/font:\s*var\(--type-label-strong-font\)/)
        expect(rule![1]).not.toMatch(/font-weight:\s*600/)
        expect(rule![1]).toMatch(/color:\s*var\(--muted\)/)
        expect(rule![1]).toMatch(/text-align:\s*left/)
    })

    it("td.num is right-aligned and uses the data step", () => {
        const rule = /\.data-table\s+td\.num\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/text-align:\s*right/)
        expect(rule![1]).toMatch(/font:\s*var\(--type-data-font\)/)
    })

    it("rows are separated by a hairline, not a zebra background", () => {
        const thRule = /\.data-table\s+th\s*\{([^}]*)\}/.exec(designSystemCss)![1]
        const tdRule = /\.data-table\s+td\s*\{([^}]*)\}/.exec(designSystemCss)![1]
        expect(thRule).toMatch(/border-bottom:\s*1px solid var\(--line-2\)/)
        expect(tdRule).toMatch(/border-bottom:\s*1px solid var\(--line-2\)/)
        expect(designSystemCss).not.toMatch(/\.data-table[^{]*nth-child/)
    })

    it(".table-scroll wrapper scrolls horizontally", () => {
        const rule = /\.table-scroll\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/overflow-x:\s*auto/)
    })
})

describe("the card primitive", () => {
    it("base card has a hairline border, rounded corners, and surface background", () => {
        const rule = /(?<!--)\.card\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/border:\s*1px solid var\(--line\)/)
        expect(rule![1]).toMatch(/background:\s*var\(--surface\)/)
    })

    it("--sunken/--derived/--selected modifiers exist with distinct treatments", () => {
        expect(designSystemCss).toMatch(/\.card--sunken\s*\{[^}]*background:\s*var\(--surface-sunken\)/)
        expect(designSystemCss).toMatch(/\.card--derived\s*\{[^}]*border-left:\s*3px solid var\(--accent\)/)
        expect(designSystemCss).toMatch(/\.card--selected\s*\{[^}]*background:\s*var\(--accent-50\)/)
    })
})

describe("the data run primitive", () => {
    it(".data never silently truncates or reflows a raw value", () => {
        const rule = /^\.data\s*\{([^}]*)\}/m.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/overflow-wrap:\s*anywhere/)
        expect(rule![1]).toMatch(/font:\s*var\(--type-data-font\)/)
    })

    it(".data--select adds user-select: all", () => {
        expect(designSystemCss).toMatch(/\.data--select\s*\{[^}]*user-select:\s*all/)
    })
})

describe("the note primitive", () => {
    it("is capped to --measure-note, muted, at the note step", () => {
        const rule = /\.note\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/max-width:\s*var\(--measure-note\)/)
        expect(rule![1]).toMatch(/color:\s*var\(--muted\)/)
        expect(rule![1]).toMatch(/font:\s*var\(--type-note-font\)/)
    })
})

describe("value-pill--muted matches .value-pill's face and size EXACTLY, including line-height", () => {
    // Post-review fix: the first cut of this rule omitted the base
    // `.value-pill`'s `/1.3` line-height from the `font:` shorthand --
    // pin the FULL shorthand, not just weight/size/face, so a future
    // edit can't silently drop it again.
    it("is 600 .72rem/1.3 sans, not a mono uppercase form", () => {
        const rule = /\.value-pill--muted\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(rule).not.toBeNull()
        expect(rule![1]).toMatch(/font:\s*600\s*\.72rem\/1\.3\s*var\(--sans\)/)
        expect(rule![1]).not.toMatch(/text-transform/)
        expect(rule![1]).not.toMatch(/var\(--mono\)/)
    })
})

/**
 * `letter-spacing` inherits as an already-resolved length, not
 * recomputed per descendant font-size, so a `.value-pill` sitting
 * inside a `.t-display-1` heading (`letter-spacing: -.03em`, computed
 * at THAT heading's own ~64px) would inherit a fixed ~-1.8px tracking
 * onto the pill's own 11.52px text -- several characters cramming into
 * the space of one, measured live as illegible overlapping glyphs on
 * `CorrectionSchemePage.tsx`'s level-of-theory pill and
 * `FrequencyScaleFactorPage.tsx`'s scale-kind pill. Both
 * `.value-pill` and its `--muted` variant reset it explicitly so
 * neither can drift back to inheriting a heading's tracking.
 */
describe("value-pill/value-pill--muted reset letter-spacing, so a pill inside a display heading never inherits its tracking", () => {
    it("both declare letter-spacing: normal", () => {
        // `\.value-pill\s*\{` only matches the BASE rule -- in the chained
        // `.value-pill.value-pill--muted` selector, `.value-pill` is
        // immediately followed by another `.`, never whitespace/`{`, so
        // this pattern cannot match partway into that compound selector.
        const base = /\.value-pill\s*\{([^}]*)\}/.exec(designSystemCss)
        const muted = /\.value-pill--muted\s*\{([^}]*)\}/.exec(designSystemCss)
        expect(base, ".value-pill rule not found").not.toBeNull()
        expect(muted, ".value-pill.value-pill--muted rule not found").not.toBeNull()
        expect(base![1]).toMatch(/letter-spacing:\s*normal/)
        expect(muted![1]).toMatch(/letter-spacing:\s*normal/)
    })
})

describe(".kv-list dd falls back to overflow-wrap: anywhere for an unbreakable value (post-review pass)", () => {
    // What is true now: the 16rem column width (below) is what fixes all
    // six required routes, and `.data`/`code` inherit `overflow-wrap:
    // anywhere` from `.kv-list dd` directly -- no rule of their own
    // needed for that property (they still need their own `font:` rule,
    // pinned separately above, since `code` carries a UA-stylesheet
    // `font-family` that inheritance alone would not override).
    //
    // There is no second rule (`.kv-list dd .data, .kv-list dd code`)
    // carrying `word-break: keep-all` or a duplicate `overflow-wrap:
    // anywhere`: `word-break: keep-all` only affects CJK line breaking, so it
    // does nothing for these Latin/underscore refs, and a `<wbr>` inserted
    // by a ref-rendering helper can split a ref that has room to sit whole.
    it("declares overflow-wrap: anywhere on the base .kv-list dd rule", () => {
        const rule = /\.kv-list dd \{([^}]*)\}/.exec(designSystemCss)
        expect(rule, ".kv-list dd rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/overflow-wrap:\s*anywhere/)
    })

    it("does not narrow that fallback to overflow-wrap: normal (the caught regression: a 64-char hash with no _/- overflowed the next column instead of wrapping)", () => {
        const rule = /\.kv-list dd \{([^}]*)\}/.exec(designSystemCss)
        expect(rule![1]).not.toMatch(/overflow-wrap:\s*normal/)
    })

    it("no longer declares a separate .kv-list dd .data, .kv-list dd code overflow-wrap/word-break override", () => {
        expect(designSystemCss).not.toMatch(/\.kv-list dd \.data,\s*\n?\s*\.kv-list dd code \{[^}]*overflow-wrap/)
    })

    it("word-break: keep-all does not appear as a live declaration in design-system.css (inert for Latin/underscore refs)", () => {
        // stripComments first: this file's own comments quote
        // `word-break: keep-all` by name -- prose mentioning a declaration is not a live one.
        expect(stripComments(designSystemCss)).not.toMatch(/word-break:\s*keep-all/)
    })
})

describe(".data-table td.num never wraps", () => {
    // MEASURED: a NASA-7 coefficient in scientific notation broke as
    // "-5.91781e- / 8" at 680px -- a numeric column has nothing to
    // legitimately wrap at.
    it("declares white-space: nowrap", () => {
        const rule = /\.data-table td\.num \{([^}]*)\}/.exec(designSystemCss)
        expect(rule, ".data-table td.num rule not found").not.toBeNull()
        expect(rule![1]).toMatch(/white-space:\s*nowrap/)
    })
})

describe(".data inside .data-table stays unbreakable", () => {
    // `.data` is breakable (`overflow-wrap: anywhere`) so a long ref wraps
    // inside a narrow key/value cell; inside a table the `.table-scroll`
    // wrapper scrolls instead, and a breakable ref let auto table layout
    // shrink its column to three characters wide (PR C review).
    it("overrides overflow-wrap and forbids wrapping for td .data / td code", () => {
        const m = designSystemCss.match(/\.data-table td code,\s*\.data-table td \.data \{([^}]*)\}/)
        expect(m, ".data-table td code, .data-table td .data rule").not.toBeNull()
        expect(m![1]).toMatch(/overflow-wrap:\s*normal/)
        expect(m![1]).toMatch(/white-space:\s*nowrap/)
    })
})


describe("every font family in a rule resolves to a font token", () => {
    // `index.css` declares the tokens and the @font-face faces themselves; everything else must use them.
    const OWN_DECLARATIONS = /@font-face\s*\{[^}]*\}|:root\s*\{[^}]*\}/g
    const TOKEN_FAMILY = /^(var\(--(mono|sans|display)(\s*,[^)]*)?\)|inherit)$/

    /** The family list of a `font:` shorthand value: everything after the size (and optional line height). */
    function shorthandFamily(value: string): string | null {
        if (/^(inherit|initial|unset)$/.test(value) || /^var\(--type-[\w-]+-font\)$/.test(value)) return null
        const match = /(?:^|\s)(?:\d*\.?\d+(?:px|rem|em|%|pt)|clamp\([^)]*\)|(?:x-)?(?:small|medium|large))(?:\/[\d.]+(?:px|rem|em|%)?)?\s+(.+)$/.exec(value)
        return match ? match[1].trim() : value
    }

    /** Every family list declared by `font-family` or `font` in the CSS that is outside the token declarations. */
    function familyLists(css: string): string[] {
        const body = stripComments(css).replace(OWN_DECLARATIONS, "")
        const lists = [...body.matchAll(/(?<![\w-])font-family\s*:\s*([^;}]+)/g)].map((m) => m[1].trim())
        for (const m of body.matchAll(/(?<![\w-])font\s*:\s*([^;}]+)/g)) {
            const family = shorthandFamily(m[1].trim())
            if (family !== null) lists.push(family)
        }
        return lists
    }

    for (const [name, css] of Object.entries(ALL_STYLESHEETS)) {
        it(`${name} sets font-family and the font shorthand only through var(--mono), var(--sans), var(--display) or inherit`, () => {
            expect(familyLists(css).filter((value) => !TOKEN_FAMILY.test(value))).toEqual([])
        })
    }

    it("catches a literal family in either form (the scan is not vacuous)", () => {
        expect(familyLists(".a { font: 400 1rem/1.4 Georgia, serif; }")).toEqual(["Georgia, serif"])
        expect(familyLists(".a { font-family: Georgia, serif; }")).toEqual(["Georgia, serif"])
        expect(familyLists(".a { font: 600 .8rem var(--mono); font: var(--type-ui-font); }")).toEqual(["var(--mono)"])
        expect(familyLists(".a { font: .74rem var(--mono, ui-monospace, monospace); }").filter((v) => !TOKEN_FAMILY.test(v))).toEqual([])
    })
})

describe("the self-hosted font families are exactly the reviewed set", () => {
    const FAMILIES = [...indexCss.matchAll(/@font-face\s*\{\s*font-family:\s*"([^"]+)"/g)].map((m) => m[1])
    it("declares Source Sans 3, Source Code Pro and the Noto Sans Math symbols face, once or twice each, nothing else", () => {
        expect([...new Set(FAMILIES)].sort()).toEqual(["Noto Sans Math Symbols", "Source Code Pro", "Source Sans 3"])
    })
    it("gives Source Sans 3 and Source Code Pro an italic face, so italics are never synthesised", () => {
        for (const family of ["Source Sans 3", "Source Code Pro"]) {
            const faces = [...indexCss.matchAll(/@font-face\s*\{[^}]*\}/g)].map((m) => m[0]).filter((face) => face.includes(`"${family}"`))
            expect(faces.some((face) => /font-style:\s*italic/.test(face)), family).toBe(true)
            expect(faces.some((face) => /font-style:\s*normal/.test(face)), family).toBe(true)
        }
    })
    it("points every face at a file under /fonts/", () => {
        const urls = [...indexCss.matchAll(/@font-face[^}]*?url\("([^"]+)"\)/g)].map((m) => m[1])
        expect(urls.length).toBe(FAMILIES.length)
        for (const url of urls) expect(url.startsWith("/fonts/")).toBe(true)
    })
})
