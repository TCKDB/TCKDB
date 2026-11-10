import { describe, expect, it } from "vitest"
import { NODE_RX } from "./domain/dependencyGraphLayout"

/** Corners are at most 6px and nothing is a pill: CSS `border-radius`, the radius tokens and SVG `rx`. */
const MAX_RADIUS_PX = 6

const STYLESHEETS = import.meta.glob("./*.css", { query: "?raw", import: "default", eager: true }) as Record<string, string>
const SOURCES = Object.fromEntries(
    Object.entries(import.meta.glob("./**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>)
        .filter(([path]) => !path.endsWith(".test.tsx")),
)

function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, "")
}

/** Each length in a radius value as px, or the raw token when it is not a plain length (`50%`, `var(...)`). */
function radiusTokens(value: string): string[] {
    return value.trim().split(/\s+/)
}

function toPx(token: string): number | null {
    const match = /^(\d*\.?\d+)(px|rem|em)$/.exec(token)
    if (!match) return null
    return Number(match[1]) * (match[2] === "px" ? 1 : 16)
}

/** Every radius declaration in the CSS that breaks the shape rule, as "declaration". */
function radiusOffenders(css: string): string[] {
    const offenders: string[] = []
    for (const match of stripComments(css).matchAll(/(border(?:-[a-z]+)*-radius)\s*:\s*([^;}]+)/g)) {
        for (const token of radiusTokens(match[2])) {
            const px = toPx(token)
            if (px !== null && px > MAX_RADIUS_PX) offenders.push(`${match[1]}: ${match[2].trim()}`)
            if (token === "999px" || /^\d{3,}(px|rem|em)$/.test(token)) offenders.push(`${match[1]}: ${match[2].trim()}`)
            if (token.endsWith("%") && token !== "50%") offenders.push(`${match[1]}: ${match[2].trim()}`)
        }
    }
    return [...new Set(offenders)]
}

function radiusDeclarationCount(css: string): number {
    return [...stripComments(css).matchAll(/border(?:-[a-z]+)*-radius\s*:/g)].length
}

/** Numeric SVG `rx` attributes above the limit in TSX source. */
function svgRxOffenders(source: string): string[] {
    const out: string[] = []
    for (const match of source.matchAll(/\brx=(?:\{(\d+(?:\.\d+)?)\}|"(\d+(?:\.\d+)?)")/g)) {
        const value = Number(match[1] ?? match[2])
        if (value > MAX_RADIUS_PX) out.push(match[0])
    }
    return out
}

describe("corner radius is at most 6px and nothing is a pill", () => {
    for (const [path, css] of Object.entries(STYLESHEETS)) {
        it(`${path} has no border-radius above ${MAX_RADIUS_PX}px, no 999px and no percentage but 50%`, () => {
            expect(radiusOffenders(css)).toEqual([])
        })
    }

    it("finds radius declarations (the scan is not vacuous)", () => {
        const total = Object.values(STYLESHEETS).reduce((n, css) => n + radiusDeclarationCount(css), 0)
        expect(total).toBeGreaterThan(40)
    })

    it("flags large radii, pills and large percentages", () => {
        expect(radiusOffenders(".a { border-radius: 999px; } .b { border-radius: .5rem; } .c { border-radius: 50%; } .d { border-radius: 4px 12px; } .e { border-radius: 40%; }"))
            .toEqual(["border-radius: 999px", "border-radius: .5rem", "border-radius: 4px 12px", "border-radius: 40%"])
    })

    it("keeps the radius tokens within the limit", () => {
        const index = stripComments(STYLESHEETS["./index.css"])
        for (const name of ["radius", "radius-sm"]) {
            const value = new RegExp(`--${name}:\\s*(\\d+)px`).exec(index)
            expect(value, `--${name}`).not.toBeNull()
            expect(Number(value![1])).toBeLessThanOrEqual(MAX_RADIUS_PX)
        }
    })
})

describe("SVG rx is at most 6", () => {
    it("has no numeric rx above the limit in any component or page", () => {
        const offenders = Object.entries(SOURCES).flatMap(([path, source]) => svgRxOffenders(source).map((hit) => `${path}: ${hit}`))
        expect(offenders).toEqual([])
    })

    it("finds rx attributes (the scan is not vacuous)", () => {
        const total = Object.values(SOURCES).reduce((n, source) => n + [...source.matchAll(/\brx=/g)].length, 0)
        expect(total).toBeGreaterThanOrEqual(3)
        expect(svgRxOffenders('<rect rx={999} /><rect rx="8" /><rect rx={4} /><rect rx={NODE_RX} />')).toEqual(["rx={999}", 'rx="8"'])
    })

    it("keeps the dependency graph's node radius within the limit", () => {
        expect(NODE_RX).toBeLessThanOrEqual(MAX_RADIUS_PX)
    })
})
