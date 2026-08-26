import type { IdentifierSearch, SearchMatch } from "../api/scientificApi"

export type IdentifierClassification =
    | { valid: true; identifier: IdentifierSearch; label: string }
    | { valid: false; message: string }

const inchiKeyPattern = /^[A-Z]{14}-[A-Z]{10}-[A-Z]$/
const formulaPattern = /^(?:C\d*)?(?:H\d*)?(?:[A-BD-Z][a-z]?\d*)*(?:[+-]\d*|\d+[+-])?$/
const refPattern = /^(?:spec|se)_[a-z0-9]+$/

export function classifyIdentifier(input: string): IdentifierClassification {
    const value = input.trim()
    if (!value) return { valid: false, message: "Enter a formula, public reference, SMILES, InChI, or InChIKey." }
    if (/^InChI=/i.test(value)) return { valid: true, identifier: { kind: "inchi", value }, label: "InChI" }
    if (inchiKeyPattern.test(value)) return { valid: true, identifier: { kind: "inchi-key", value }, label: "InChIKey" }
    if (refPattern.test(value)) return { valid: true, identifier: { kind: "public-ref", value }, label: "public reference" }
    if (formulaPattern.test(value) && !/[=#()[\]@\\/]/.test(value)) {
        return { valid: true, identifier: { kind: "formula", value }, label: "formula" }
    }
    if (/\s/.test(value)) return { valid: false, message: "Structure strings cannot contain spaces. Use a supported exact identifier." }
    return { valid: true, identifier: { kind: "smiles", value }, label: "SMILES" }
}

export function resultPath(match: SearchMatch): string {
    return `/species-entries/${encodeURIComponent(match.entryRef)}`
}
