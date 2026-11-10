import type { ReactNode } from "react"
import "../record-glyph.css"

export type RecordKind =
    | "species"
    | "species-entry"
    | "conformer-group"
    | "calculation"
    | "reaction"
    | "transition-state"
    | "network"

const KIND_CLASS: Record<RecordKind, string> = {
    species: "species",
    "species-entry": "species",
    "conformer-group": "species",
    calculation: "species",
    reaction: "reaction",
    "transition-state": "ts",
    network: "network",
}

const SHAPES: Record<RecordKind, ReactNode> = {
    species: <circle cx="7" cy="7" r="5.5" fillOpacity=".2" />,
    "species-entry": <>
        <circle cx="7" cy="7" r="5.5" fillOpacity=".2" />
        <circle cx="7" cy="7" r="2.4" fill="none" />
    </>,
    "conformer-group": <rect x="1.8" y="1.8" width="10.4" height="10.4" rx="3" fillOpacity=".2" />,
    calculation: <rect x="3.5" y="3.5" width="7" height="7" fillOpacity=".2" />,
    reaction: <path d="M7 1.5 12.5 7 7 12.5 1.5 7Z" fillOpacity=".2" strokeLinejoin="round" />,
    "transition-state": <path d="M7 1.8 12.6 12 1.4 12Z" fillOpacity=".2" strokeLinejoin="round" />,
    network: <path d="M4.2 1.8h5.6L12.6 7l-2.8 5.2H4.2L1.4 7Z" fillOpacity=".2" strokeLinejoin="round" />,
}

/** A small shape in the record kind's colour. Decorative unless given a `label`. */
export function RecordGlyph({ kind, label, size = 14, className }: {
    kind: RecordKind
    label?: string
    size?: number
    className?: string
}) {
    const classes = `record-glyph record-glyph--${KIND_CLASS[kind]}${className ? ` ${className}` : ""}`
    const labelled = label !== undefined
    return (
        <svg
            className={classes}
            viewBox="0 0 14 14"
            width={size}
            height={size}
            data-kind={kind}
            {...(labelled ? { role: "img" } : { "aria-hidden": true })}
        >
            {labelled && <title>{label}</title>}
            {SHAPES[kind]}
        </svg>
    )
}
