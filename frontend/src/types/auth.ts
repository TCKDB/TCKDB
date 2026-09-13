import { z } from "zod"

/**
 * Runtime + static types for `/api/v1/auth/*`.
 *
 * Source of truth: `backend/app/api/routes/auth.py` (`MeResponse`,
 * `ApiKeyMetadata`, `ApiKeyCreateResponse`). Session-cookie based, not
 * bearer-token based -- every request goes through `authApi.ts`, which
 * always sends `credentials: "include"`.
 */

export const AppUserRoleSchema = z.enum(["user", "curator", "admin"])
export type AppUserRole = z.infer<typeof AppUserRoleSchema>

/**
 * The signed-in identity. `username` is a login handle, not a stable
 * identity key -- ORCID linking is the intended second login method
 * (`app_user.orcid`, not yet wired up), so a future account can carry
 * more than one way to sign in. Callers key any state off `id` and
 * render `full_name || username` for display; never treat `username` as
 * though it were the identity itself.
 */
export const MeResponseSchema = z.object({
    id: z.number().int(),
    username: z.string(),
    email: z.string().nullable(),
    full_name: z.string().nullable(),
    role: AppUserRoleSchema,
    is_active: z.boolean(),
})
export type MeResponse = z.infer<typeof MeResponseSchema>

/**
 * One API key's metadata -- never the plain key itself. `GET /auth/api-keys`
 * and the list surfaced after creation both return only this shape.
 */
export const ApiKeyMetadataSchema = z.object({
    id: z.number().int(),
    label: z.string().nullable(),
    created_at: z.string(),
    last_used_at: z.string().nullable(),
    revoked_at: z.string().nullable(),
})
export type ApiKeyMetadata = z.infer<typeof ApiKeyMetadataSchema>

/**
 * `POST /auth/api-keys`'s response: the same metadata plus the plaintext
 * `key`, shown exactly once. Nothing in this app persists `key` anywhere
 * (no localStorage, no context that survives a re-fetch) -- see
 * `AccountPage.tsx`'s own docstring on why it lives in page-local state
 * only.
 */
export const ApiKeyCreateResponseSchema = ApiKeyMetadataSchema.extend({
    key: z.string(),
})
export type ApiKeyCreateResponse = z.infer<typeof ApiKeyCreateResponseSchema>
