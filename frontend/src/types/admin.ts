import { z } from "zod"
import { AppUserRoleSchema } from "./auth"

/**
 * Runtime + static types for `/api/v1/admin/*`.
 *
 * Source of truth: `backend/app/api/routes/admin.py`
 * (`AdminUserResponse`, `PaginatedResponse`).
 *
 * Note what is NOT here: `email` and `orcid`. The backend deliberately
 * withholds them from this route -- role management needs to know who
 * someone is, not how to reach them, and this would otherwise be the one
 * surface serving contact details for every account at once. Adding them
 * to this schema would not make them appear; it would make the schema
 * lie. If they are ever wanted, the decision is the backend's to revisit.
 */
export const AdminUserSchema = z.object({
    id: z.number().int(),
    username: z.string(),
    full_name: z.string().nullable(),
    affiliation: z.string().nullable(),
    role: AppUserRoleSchema,
    is_active: z.boolean(),
    created_at: z.string(),
})
export type AdminUser = z.infer<typeof AdminUserSchema>

export const AdminUserPageSchema = z.object({
    items: z.array(AdminUserSchema),
    total: z.number().int(),
    skip: z.number().int(),
    limit: z.number().int(),
})
export type AdminUserPage = z.infer<typeof AdminUserPageSchema>

/** `PATCH /admin/users/{id}/role` -- the changed row, nothing more. */
export const UserRoleChangeSchema = z.object({
    id: z.number().int(),
    username: z.string(),
    role: AppUserRoleSchema,
})
export type UserRoleChange = z.infer<typeof UserRoleChangeSchema>
