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
 *   - a ONE-SIDED equation -- `<> [NH2]` (products only) or `[NH2] <>`
 *     (reactants only), the arrow with exactly one side left empty. See
 *     `classifyReactionQuery`'s own comment on the one case this rejects
 *     (BOTH sides empty) and why a one-sided equation is not a narrower
 *     search than the bare-structure form above.
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
 * **Every recognized arrow means the same thing** (owner correction,
 * reversing an earlier design here that mapped `<>`/`<=>`/`<->` to
 * `direction=either` and `=>`/`->` to `direction=forward`): "how would the
 * user know there is a forward-only one... you are getting stuck on the
 * look of the arrow". A reader typing `<>` had no way to discover that
 * `->` existed or meant something different -- a hidden mode the syntax
 * itself could never teach. The arrow here ONLY separates reactants from
 * products; `IdentifierSearch.tsx` always searches `direction=either`
 * regardless of which arrow was typed, and labels each individual result
 * with how it actually matched (`ReactionParticipationMatch.matchedDirection`,
 * the same `matched_direction`/"Matched on the reverse direction" the
 * archive's own `/reactions` browse index already surfaces via
 * `ReactionBrowseRow.tsx`) rather than asking the reader to already know a
 * second arrow exists. One syntax, everything found, each row says how.
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
    | { valid: true; kind: "equation"; reactants: string[]; products: string[] }
    | { valid: false; message: string }

const EQUATION_EXAMPLE = "NN,[H] <> N,[NH2]"

/**
 * Every arrow shape this grammar accepts -- liberal on purpose (owner:
 * "be liberal in what you accept"), and, per the correction above, NONE of
 * them carry a direction any more; they are interchangeable ways to write
 * "reactants on the left, products on the right". Ordered longest-first so
 * `<->` is never mis-matched as a bare `<>` missing its middle character.
 */
const ARROW_PATTERN = /<=>|<->|<>|=>|->/g
const RECOGNIZED_ARROWS = ["<=>", "<->", "<>", "=>", "->"]

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
    // One side may be empty -- "<> [NH2]" (products only, reactants
    // unconstrained) or "[NH2] <>" (reactants only) both parse (owner:
    // "what if they only know the products but not the reactants"). The
    // ONLY rejected shape is an arrow with NOTHING on either side, still a
    // parse error, not a valid empty-both-sides query. This is honest
    // rather than a narrowing convenience: every reaction in this archive
    // is reversible and every search here already runs `direction=either`
    // (see this module's own top comment), so `<> [NH2]` and a bare
    // `[NH2]` participation query hit the exact same rows -- OWNER
    // MEASURED, `[NH2]` is a stored reactant in 4 reactions and a stored
    // product in none, and `product_smiles=[NH2]&direction=either` still
    // returns those same 4 (an either-direction match finds it on whichever
    // side it actually sits). The side written does not filter anything;
    // `IdentifierSearch.tsx` sends only the side actually given (the empty
    // side contributes no query param at all, matching how a bare
    // "participation" query already sends only `reactant_smiles`), and each
    // row's own `matchedDirection` label is what tells the reader how it
    // matched -- never a second sentence here explaining the asymmetry.
    if (reactants.length === 0 && products.length === 0) {
        return {
            valid: false,
            message: `An equation needs a structure on at least one side of the arrow, e.g. ${EQUATION_EXAMPLE}.`,
        }
    }
    return { valid: true, kind: "equation", reactants, products }
}

/** True for an arrow token this grammar recognizes -- exported only for the arrow-set's own unit coverage. */
export function isRecognizedArrow(token: string): boolean {
    return RECOGNIZED_ARROWS.includes(token)
}
