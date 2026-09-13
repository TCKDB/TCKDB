import { http, HttpResponse } from "msw"
import { setupServer } from "msw/node"
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest"
import { AuthApiError, createApiKey, fetchMe, listApiKeys, login, logout, register, revokeApiKey } from "./authApi"

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: "error" }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const meResponse = {
    id: 1,
    username: "calvin",
    email: "calvin@example.com",
    full_name: "Calvin Pieters",
    role: "user",
    is_active: true,
}

describe("login", () => {
    // The load-bearing assertion in this whole file: every one of these
    // endpoints is session-cookie based, and `credentials: "include"` is
    // the ONLY thing that makes the cookie round-trip. Dropping it from
    // `authApi.ts` breaks every signed-in feature silently (a normal
    // `fetch` still resolves 200, it just never carries or receives the
    // cookie), so this checks the actual `Request` MSW intercepted, not
    // just the response shape.
    it("sends credentials: include and returns the signed-in user", async () => {
        let sawCredentials: RequestCredentials | undefined
        server.use(http.post("/api/v1/auth/login", async ({ request }) => {
            sawCredentials = request.credentials
            expect(await request.json()).toEqual({ username: "calvin", password: "correct-horse" })
            return HttpResponse.json(meResponse, { status: 200 })
        }))
        await expect(login({ username: "calvin", password: "correct-horse" })).resolves.toEqual({
            id: 1, username: "calvin", email: "calvin@example.com", full_name: "Calvin Pieters", role: "user", is_active: true,
        })
        expect(sawCredentials).toBe("include")
    })

    it("surfaces a 401 as an AuthApiError carrying the server's message", async () => {
        server.use(http.post("/api/v1/auth/login", () => HttpResponse.json({ detail: "Invalid username or password." }, { status: 401 })))
        await expect(login({ username: "calvin", password: "wrong" })).rejects.toEqual(
            expect.objectContaining<Partial<AuthApiError>>({ status: 401, message: "Invalid username or password." }),
        )
    })
})

describe("register", () => {
    it("strips a catalogued code prefix from the 409 detail so the message reads as prose", async () => {
        server.use(http.post("/api/v1/auth/register", () => HttpResponse.json(
            { code: "username_taken", detail: "username_taken: That username is already in use.", context: {} },
            { status: 409 },
        )))
        await expect(register({ username: "calvin", password: "password123" })).rejects.toEqual(
            expect.objectContaining<Partial<AuthApiError>>({
                status: 409,
                code: "username_taken",
                message: "That username is already in use.",
            }),
        )
    })

    it("distinguishes the email-taken conflict from the username-taken one", async () => {
        server.use(http.post("/api/v1/auth/register", () => HttpResponse.json(
            { code: "email_taken", detail: "email_taken: That email address is already in use.", context: {} },
            { status: 409 },
        )))
        await expect(register({ username: "calvin", password: "password123", email: "taken@example.com" })).rejects.toEqual(
            expect.objectContaining<Partial<AuthApiError>>({
                status: 409,
                code: "email_taken",
                message: "That email address is already in use.",
            }),
        )
    })
})

describe("fetchMe", () => {
    it("resolves the user on 200", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json(meResponse)))
        await expect(fetchMe()).resolves.toEqual({
            id: 1, username: "calvin", email: "calvin@example.com", full_name: "Calvin Pieters", role: "user", is_active: true,
        })
    })

    // Not being logged in is not an error -- `fetchMe` reports it as an
    // ordinary `null`, not a thrown `AuthApiError`, so `AuthProvider`
    // never has to distinguish "genuinely unauthenticated" from "the
    // request blew up" by inspecting a caught error's status.
    it("resolves null (not a throw) on 401", async () => {
        server.use(http.get("/api/v1/auth/me", () => new HttpResponse(null, { status: 401 })))
        await expect(fetchMe()).resolves.toBeNull()
    })

    it("still throws for a genuine server error", async () => {
        server.use(http.get("/api/v1/auth/me", () => HttpResponse.json({ detail: "boom" }, { status: 500 })))
        await expect(fetchMe()).rejects.toBeInstanceOf(AuthApiError)
    })
})

describe("logout", () => {
    it("sends credentials: include", async () => {
        let sawCredentials: RequestCredentials | undefined
        server.use(http.post("/api/v1/auth/logout", ({ request }) => {
            sawCredentials = request.credentials
            return new HttpResponse(null, { status: 204 })
        }))
        await expect(logout()).resolves.toBeUndefined()
        expect(sawCredentials).toBe("include")
    })
})

describe("API keys", () => {
    it("createApiKey returns the plaintext key alongside its metadata", async () => {
        server.use(http.post("/api/v1/auth/api-keys", async ({ request }) => {
            expect(await request.json()).toEqual({ label: "ARC on Zeus" })
            return HttpResponse.json({
                id: 7, label: "ARC on Zeus", created_at: "2026-09-13T00:00:00Z", last_used_at: null, revoked_at: null,
                key: "tckdb_live_abc123",
            }, { status: 201 })
        }))
        await expect(createApiKey({ label: "ARC on Zeus" })).resolves.toEqual({
            id: 7, label: "ARC on Zeus", created_at: "2026-09-13T00:00:00Z", last_used_at: null, revoked_at: null,
            key: "tckdb_live_abc123",
        })
    })

    it("listApiKeys never carries a plaintext key field", async () => {
        server.use(http.get("/api/v1/auth/api-keys", () => HttpResponse.json([
            { id: 7, label: "ARC on Zeus", created_at: "2026-09-13T00:00:00Z", last_used_at: null, revoked_at: null },
        ])))
        const keys = await listApiKeys()
        expect(keys).toEqual([{ id: 7, label: "ARC on Zeus", created_at: "2026-09-13T00:00:00Z", last_used_at: null, revoked_at: null }])
        expect(keys[0]).not.toHaveProperty("key")
    })

    it("revokeApiKey calls DELETE on the key's own id", async () => {
        server.use(http.delete("/api/v1/auth/api-keys/7", () => new HttpResponse(null, { status: 204 })))
        await expect(revokeApiKey(7)).resolves.toBeUndefined()
    })
})
