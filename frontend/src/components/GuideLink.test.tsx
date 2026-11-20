import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { GuideLink } from "./GuideLink"
import { SectionHeading } from "./PageSections"

afterEach(cleanup)

describe("GuideLink", () => {
    it("as an icon, is named by its question and says where it goes, with the same text as its title", () => {
        render(<MemoryRouter><GuideLink to="level-of-theory" question="What is a level of theory?" /></MemoryRouter>)
        const link = screen.getByRole("link", { name: "What is a level of theory? Open the guide" })
        expect(link).toHaveAttribute("href", "/guide#level-of-theory")
        expect(link).toHaveAttribute("title", "What is a level of theory? Open the guide")
        expect(link).toHaveTextContent("")
        expect(link.querySelector("svg")).toHaveAttribute("aria-hidden", "true")
    })

    it("inline, shows the term as its own text, with no hidden extra and no note styling of its own", () => {
        render(<MemoryRouter><p className="t-body">Two passes. <GuideLink to="staged-optimisation">What is a staged optimisation?</GuideLink></p></MemoryRouter>)
        const link = screen.getByRole("link", { name: "What is a staged optimisation?" })
        expect(link).toHaveAttribute("href", "/guide#staged-optimisation")
        expect(link.textContent).toBe("What is a staged optimisation?")
        expect(link.closest(".note")).toBeNull()
    })

    it("refuses link text that names nothing", () => {
        const quiet = vi.spyOn(console, "error").mockImplementation(() => {})
        try {
            for (const text of ["What is this?", "what is this", "  "]) {
                expect(() => render(<MemoryRouter><GuideLink to="ts-checks">{text}</GuideLink></MemoryRouter>)).toThrow(/must name the term/)
                expect(() => render(<MemoryRouter><GuideLink to="ts-checks" question={text} /></MemoryRouter>)).toThrow(/must name the term/)
            }
        } finally {
            quiet.mockRestore()
        }
    })

    it("no source file uses the bare default text or a guide link without words", () => {
        const sources = import.meta.glob("../**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>
        const offenders: string[] = []
        for (const [path, text] of Object.entries(sources)) {
            if (/\.test\.tsx$/.test(path)) continue
            if (/What is this\?/.test(text)) offenders.push(`${path}: "What is this?"`)
            if (/<GuideLink\s+to="[^"]*"\s*\/>/.test(text)) offenders.push(`${path}: <GuideLink to=… /> with no question or text`)
        }
        expect(offenders).toEqual([])
    })
})

describe("SectionHeading guide", () => {
    it("puts the glyph after the heading, outside it, so the heading keeps its own name", () => {
        render(
            <MemoryRouter>
                <SectionHeading id="trust-irc-heading" guide={{ to: "ts-checks", question: "What are the transition-state checks?" }}>Trust and IRC</SectionHeading>
            </MemoryRouter>,
        )
        const heading = screen.getByRole("heading", { level: 2, name: "Trust and IRC" })
        const link = screen.getByRole("link", { name: "What are the transition-state checks? Open the guide" })
        expect(heading).not.toContainElement(link)
        expect(heading.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
        expect(document.querySelector(".section-intro")).toBeNull()
    })
})
