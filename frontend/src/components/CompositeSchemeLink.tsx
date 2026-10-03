import { Link } from "react-router-dom"
import type { CompositeSchemeSummary } from "../api/scientificSchemas"
import { compositeSchemePath } from "../domain/methodsLinks"

/**
 * A link to a composite recipe's page (`/methods/composite-schemes/:ref`),
 * rendered only where a level of theory actually carries a `composite_scheme`
 * (ADR 0021). The text is a fixed short "view recipe": the recipe's own label can be a long
 * spelled-out formula that the level link beside it already shows; a level with no recipe renders nothing at all.
 */
export function CompositeSchemeLink({ scheme }: { scheme: Pick<CompositeSchemeSummary, "composite_scheme_ref"> | null | undefined }) {
    if (!scheme) return null
    return <Link to={compositeSchemePath(scheme.composite_scheme_ref)}>view recipe</Link>
}
