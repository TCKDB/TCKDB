import type { IdentifierSearch, SearchMatch } from "../api/scientificApi"

export type IdentifierClassification =
    | { valid: true; identifier: IdentifierSearch; label: string }
    | { valid: false; message: string; ambiguousValue?: string }

const inchiKeyPattern = /^[A-Z]{14}-[A-Z]{10}-[A-Z]$/
const elementSymbols = new Set([
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac",
    "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh",
    "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
])
const publicRefPattern = /^(spc|spe)_[a-z2-7]{26}$/
const publicRefStartPattern = /^(spc|spe)_/i

function isFormula(value: string): boolean {
    const body = value.replace(/(?:[+-]\d*|\d+[+-])$/, "")
    if (!body) return false
    let position = 0
    while (position < body.length) {
        const token = /^([A-Z][a-z]?)(\d*)/.exec(body.slice(position))
        if (!token || !elementSymbols.has(token[1]) || (token[2].startsWith("0") && token[2] !== "")) return false
        position += token[0].length
    }
    return true
}

function isPlausiblyBareSmiles(value: string): boolean {
    let position = 0
    while (position < value.length) {
        const token = /^(Cl|Br|B|C|N|O|P|S|F|I)/.exec(value.slice(position))
        if (!token) return false
        position += token[0].length
    }
    return true
}

export function classifyIdentifier(input: string): IdentifierClassification {
    const value = input.trim()
    if (!value) return { valid: false, message: "Enter a SMILES, formula, public reference, InChI, or InChIKey." }
    if (/^(formula|smiles):/i.test(value)) {
        const [, kind, supplied] = /^([^:]+):(.*)$/s.exec(value) ?? []
        const body = supplied?.trim() ?? ""
        if (!body) return { valid: false, message: `Enter an identifier after ${kind}:` }
        if (kind.toLowerCase() === "formula") {
            return isFormula(body)
                ? { valid: true, identifier: { kind: "formula", value: body }, label: "formula" }
                : { valid: false, message: "That is not a valid elemental formula." }
        }
        return { valid: true, identifier: { kind: "smiles", value: body }, label: "SMILES" }
    }
    if (/^InChI=/i.test(value)) return { valid: true, identifier: { kind: "inchi", value }, label: "InChI" }
    if (inchiKeyPattern.test(value)) return { valid: true, identifier: { kind: "inchi-key", value }, label: "InChIKey" }
    if (publicRefPattern.test(value)) {
        return value.toLowerCase().startsWith("spe_")
            ? { valid: true, identifier: { kind: "species-entry-ref", value }, label: "species-entry reference" }
            : { valid: true, identifier: { kind: "species-ref", value }, label: "species reference" }
    }
    if (publicRefStartPattern.test(value)) {
        return { valid: false, message: "Public references use spc_ or spe_ followed by 26 lowercase base32 characters (a-z, 2-7)." }
    }
    if (isFormula(value)) {
        if (isPlausiblyBareSmiles(value)) {
            return { valid: false, message: `“${value}” could be a SMILES or a formula. Choose how to search it.`, ambiguousValue: value }
        }
        return { valid: true, identifier: { kind: "formula", value }, label: "formula" }
    }
    if (/^[A-Z][A-Za-z]*(?:\d+)?(?:[+-]\d*|\d+[+-])?$/.test(value)) {
        return { valid: false, message: "That is not a valid elemental formula. Use smiles: if you intended a structure string." }
    }
    if (/\s/.test(value)) return { valid: false, message: "Structure strings cannot contain spaces. Use a supported exact identifier." }
    return { valid: true, identifier: { kind: "smiles", value }, label: "SMILES" }
}

export function resultPath(match: SearchMatch): string {
    return match.entryRef
        ? `/species-entries/${encodeURIComponent(match.entryRef)}`
        : `/species/${encodeURIComponent(match.speciesRef)}`
}
