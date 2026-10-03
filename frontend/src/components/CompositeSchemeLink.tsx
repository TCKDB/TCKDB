import { Link } from "react-router-dom"
import type { CompositeSchemeSummary } from "../api/scientificSchemas"
import { compositeSchemePath } from "../domain/methodsLinks"

/**
 * A link to a composite recipe's page (`/methods/composite-schemes/:ref`),
 * rendered only where a level of theory actually carries a `composite_scheme`
 * (ADR 0021). The text is the recipe's own server-generated name, never a
 * guess; a level with no recipe renders nothing at all.
 */
export function CompositeSchemeLink({ scheme }: { scheme: Pick<CompositeSchemeSummary, "composite_scheme_ref" | "name"> | null | undefined }) {
    if (!scheme) return null
    return <Link to={compositeSchemePath(scheme.composite_scheme_ref)}>{scheme.name} recipe</Link>
}
