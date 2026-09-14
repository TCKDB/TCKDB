import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { ArtifactDownloadButton } from "./ArtifactDownloadButton"
import { AuthProvider } from "./AuthProvider"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => { server.resetHandlers(); cleanup(); vi.restoreAllMocks() })
afterAll(() => server.close())

const DIGEST = "a".repeat(64)
const DOWNLOAD = `/api/v1/scientific/artifacts/${DIGEST}/download`

const meResponse = {
    id: 1, username: "calvin", email: null, full_name: "Calvin Pieters",
    role: "user", is_active: true,
}

/** jsdom implements neither; the component's save path uses both. */
let saved: { name: string | null; revoked: string[] }
beforeEach(() => {
    saved = { name: null, revoked: [] }
    URL.createObjectURL = vi.fn(() => "blob:fake")
    URL.revokeObjectURL = vi.fn((url: string) => { saved.revoked.push(url) })
    // An anchor click in jsdom would try to navigate; capture the download
    // attribute instead, which is the thing under test.
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        saved.name = this.download
    })
})

function signedIn() {
    server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(meResponse)))
}

function renderButton(props: Partial<{ artifactRef: string | null; filename: string | null }> = {}) {
    // `in`, not `??`: the fallback case passes null DELIBERATELY, and
    // `null ?? default` hands back the default -- the helper would have
    // quietly substituted a ref and filename the test was trying to omit.
    return render(
        <AuthProvider>
            <ArtifactDownloadButton
                sha256={DIGEST}
                artifactRef={"artifactRef" in props ? props.artifactRef : "art_7k2p9x"}
                filename={"filename" in props ? props.filename : "input.log"}
            />
        </AuthProvider>,
    )
}

describe("who is offered a download", () => {
    it("a signed-out reader is told to sign in rather than given a button that 401s", async () => {
        server.use(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))
        renderButton()

        expect(await screen.findByText(/sign in to download/i)).toBeInTheDocument()
        expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument()
    })

    it("renders nothing while the sign-in state is unknown", async () => {
        /**
         * A 500 on /auth/me states nothing about this visitor. Offering
         * "Sign in to download" would assert they are signed out, which
         * nobody has said -- the same distinction #467 drew for the header.
         */
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json({ detail: "boom" }, { status: 500 })))
        const { container } = renderButton()

        await expect(screen.findByText(/sign in/i, {}, { timeout: 300 })).rejects.toThrow()
        expect(container.querySelector("button")).toBeNull()
    })
})

describe("downloading", () => {
    it("sends the session cookie and saves under the archive's name", async () => {
        signedIn()
        let credentials: RequestCredentials | undefined
        server.events.on("request:start", ({ request }) => {
            if (request.url.includes("/download")) credentials = request.credentials
        })
        server.use(http.get(DOWNLOAD, () => HttpResponse.text("log bytes")))

        renderButton()
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))

        // The route is authenticated unconditionally; without this the
        // download is a 401 and the test would still pass on the filename.
        await vi.waitFor(() => expect(credentials).toBe("include"))
        await vi.waitFor(() => expect(saved.name).toBe("input_art_7k2p9x.log"))
    })

    it("releases the object URL, which otherwise pins the blob for the page's life", async () => {
        signedIn()
        server.use(http.get(DOWNLOAD, () => HttpResponse.text("log bytes")))

        renderButton()
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))

        await vi.waitFor(() => expect(saved.revoked).toContain("blob:fake"))
    })

    it("falls back to the digest when the archive has no name for the file", async () => {
        signedIn()
        server.use(http.get(DOWNLOAD, () => HttpResponse.text("log bytes")))

        renderButton({ artifactRef: null, filename: null })
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))

        await vi.waitFor(() => expect(saved.name).toBe(DIGEST))
    })
})

describe("when the archive refuses", () => {
    it("a 404 names both reasons, because the reader cannot tell them apart", async () => {
        signedIn()
        server.use(http.get(DOWNLOAD, () =>
            HttpResponse.json({ code: "not_found", detail: "x", context: {} }, { status: 404 })))

        renderButton()
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/not in the archive/i)
        expect(alert).toHaveTextContent(/not one of your own deposits/i)
    })

    it("a 502 says retrying will not help, because it is a recorded custody break", async () => {
        /**
         * The archive distinguishes these and a reader cannot. A 502 here
         * means the stored bytes failed verification -- it is written to
         * the integrity log and hard-fails the owning calculation for
         * every later reader. Telling someone to try again would be wrong
         * advice forever.
         */
        signedIn()
        server.use(http.get(DOWNLOAD, () =>
            HttpResponse.json({ code: "artifact_integrity_failed", detail: "x", context: {} }, { status: 502 })))

        renderButton()
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))

        const alert = await screen.findByRole("alert")
        expect(alert).toHaveTextContent(/retrying will not change it/i)
        expect(alert.textContent).not.toMatch(/try again/i)
    })

    it("a missing object reads differently from failed verification", async () => {
        signedIn()
        server.use(http.get(DOWNLOAD, () =>
            HttpResponse.json({ code: "artifact_object_missing", detail: "x", context: {} }, { status: 502 })))

        renderButton()
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))

        expect(await screen.findByRole("alert")).toHaveTextContent(/the object is missing/i)
    })

    it("a 503 does say try again, because that one is transient", async () => {
        signedIn()
        server.use(http.get(DOWNLOAD, () =>
            HttpResponse.json({ code: "artifact_storage_unavailable", detail: "x", context: {} }, { status: 503 })))

        renderButton()
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))

        expect(await screen.findByRole("alert")).toHaveTextContent(/try again/i)
    })

    it("a failed download saves nothing", async () => {
        signedIn()
        server.use(http.get(DOWNLOAD, () =>
            HttpResponse.json({ code: "not_found", detail: "x", context: {} }, { status: 404 })))

        renderButton()
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))
        await screen.findByRole("alert")

        expect(saved.name).toBeNull()
    })

    it("the button comes back so the reader can retry", async () => {
        signedIn()
        server.use(http.get(DOWNLOAD, () =>
            HttpResponse.json({ code: "artifact_storage_unavailable", detail: "x", context: {} }, { status: 503 })))

        renderButton()
        await userEvent.click(await screen.findByRole("button", { name: "Download" }))
        await screen.findByRole("alert")

        expect(screen.getByRole("button", { name: "Download" })).toBeEnabled()
    })
})
