import { afterEach, describe, expect, it } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import NotFoundPage from "./NotFoundPage"

afterEach(cleanup)

function page(path: string) {
    return render(
        <MemoryRouter initialEntries={[path]}>
            <Routes>
                <Route path="*" element={<NotFoundPage />} />
            </Routes>
        </MemoryRouter>,
    )
}

describe("NotFoundPage", () => {
    it("names the wrong address and offers a link home, instead of a silent redirect", () => {
        page("/this-route-does-not-exist")
        expect(screen.getByRole("heading", { name: "No page at this address" })).toBeVisible()
        expect(screen.getByText("/this-route-does-not-exist")).toBeVisible()
        expect(screen.getByRole("link", { name: /home/i })).toHaveAttribute("href", "/")
    })

    it("shows the full attempted path, including query and hash", () => {
        page("/species-entries/spe_demo/single-point?conformer=cg_1#detail")
        expect(screen.getByText("/species-entries/spe_demo/single-point?conformer=cg_1#detail")).toBeVisible()
    })
})
