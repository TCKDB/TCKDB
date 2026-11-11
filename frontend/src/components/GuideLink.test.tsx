import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { GuideLink } from "./GuideLink"

afterEach(cleanup)

describe("GuideLink", () => {
    it("names its guide entry in hidden text, so each link has its own accessible name", () => {
        render(<MemoryRouter><GuideLink to="staged-optimisation" /><GuideLink to="ts-checks" /><GuideLink to="level-of-theory" /></MemoryRouter>)
        expect(screen.getByRole("link", { name: "What is this? (staged optimisation)" })).toHaveAttribute("href", "/guide#staged-optimisation")
        expect(screen.getByRole("link", { name: "What is this? (transition-state checks)" })).toHaveAttribute("href", "/guide#ts-checks")
        expect(screen.getByRole("link", { name: "What is this? (level of theory)" })).toHaveAttribute("href", "/guide#level-of-theory")
    })
})
