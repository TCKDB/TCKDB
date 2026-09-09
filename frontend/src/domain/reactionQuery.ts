/**
 * Reaction mode's own input grammar (archive home page, `IdentifierSearch.tsx`)
 * -- deliberately separate from `classifyIdentifier` (`recordModel.ts`),
 * which classifies a SINGLE species identifier. A reaction has no formula
 * and is written as an equation, not one token, so it needs its own shape:
 *
 *   - a bare structure, or a comma-list of structures ("participation":
 *     find reactions where these appear together on one side, either side
 *     of the stored reaction) -- `NN` or `NN,[H]`
 *   - a full equation, reactants and products separated by an arrow, each
 *     side a comma-list -- `NN,[H] <> N,[NH2]`
 *
 * Commas are the only safe species separator inside one side: a SMILES can
 * itself carry `+` (`[NH4+]`) or `.` (a disconnected-component SMILES like
 * `[Na+].[Cl-]`), so neither could divide species without occasionally
 * slicing a real structure in two. `splitSmilesList` (`api/browseApi.ts`,
 * the browse page's OWN reactant/product text-field parser) already made
 * this exact call for the identical reason -- reused here rather than
 * re-decided, so equation entry on this page and on the browse page can
 * never quietly disagree about what a comma means.
 *
 * A recognized or reference-SHAPED value (`rxn_…`, `spc_…`, an unrouted
 * `thm_…`, …) never reaches this grammar at all -- `IdentifierSearch.tsx`
 * checks `looksLikeReferenceAttempt` (`recordModel.ts`) FIRST, before
 * choosing which grammar to parse with, so a reference pasted while
 * reaction mode is selected still routes through `classifyIdentifier`
 * exactly as it would in species mode. No arrow or comma can ever appear
 * inside a reference's fixed `{prefix}_{26 base32 chars}` shape, so the two
 * grammars can never collide on the same input.
 */
import { splitSmilesList } from "../api/browseApi"

export type ReactionQueryClassification =
    | { valid: true; kind: "participation"; smiles: string[] }
    | { valid: true; kind: "equation"; reactants: string[]; products: string[]; direction: "forward" | "either"; arrow: string }
    | { valid: false; message: string }

const EQUATION_EXAMPLE = "NN,[H] <> N,[NH2]"

/**
 * Reversible arrows map to `direction=either` (match either stored side);
 * directional arrows map to `direction=forward` (match the sides exactly
 * as written) -- the arrow the reader types already states the direction
 * they mean, the same way it would on paper, so the syntax carries that
 * fact instead of a separate control repeating it. Ordered longest-first
 * within each alternation group so `<->` is never mis-matched as a bare
 * `<>` missing its middle character, and `ARROW_PATTERN` itself lists the
 * 3-character forms before the 2-character ones for the same reason.
 */
const REVERSIBLE_ARROWS = ["<=>", "<->", "<>"]
const FORWARD_ARROWS = ["=>", "->"]
const ARROW_PATTERN = /<=>|<->|<>|=>|->/g

function isReversibleArrow(arrow: string): boolean {
    return (REVERSIBLE_ARROWS as string[]).includes(arrow)
}

export function classifyReactionQuery(input: string): ReactionQueryClassification {
    const value = input.trim()
    if (!value) {
        return { valid: false, message: `Enter a structure, an equation (e.g. ${EQUATION_EXAMPLE}), or a reaction reference.` }
    }

    const arrows = value.match(ARROW_PATTERN) ?? []
    if (arrows.length > 1) {
        return {
            valid: false,
            message: `That has more than one reaction arrow. Use exactly one, e.g. ${EQUATION_EXAMPLE}.`,
        }
    }

    if (arrows.length === 0) {
        const smiles = splitSmilesList(value)
        if (smiles.length === 0) {
            return { valid: false, message: `Enter a structure, an equation (e.g. ${EQUATION_EXAMPLE}), or a reaction reference.` }
        }
        return { valid: true, kind: "participation", smiles }
    }

    const [arrow] = arrows
    if (!arrow) return { valid: false, message: `Enter a structure, an equation (e.g. ${EQUATION_EXAMPLE}), or a reaction reference.` }
    const splitIndex = value.indexOf(arrow)
    const reactants = splitSmilesList(value.slice(0, splitIndex))
    const products = splitSmilesList(value.slice(splitIndex + arrow.length))
    if (reactants.length === 0 || products.length === 0) {
        return {
            valid: false,
            message: `An equation needs a structure on both sides of the arrow, e.g. ${EQUATION_EXAMPLE}.`,
        }
    }
    return {
        valid: true,
        kind: "equation",
        reactants,
        products,
        direction: isReversibleArrow(arrow) ? "either" : "forward",
        arrow,
    }
}

/** True for an arrow token this grammar recognizes -- exported only for the arrow-set's own unit coverage. */
export function isRecognizedArrow(token: string): boolean {
    return isReversibleArrow(token) || (FORWARD_ARROWS as string[]).includes(token)
}
