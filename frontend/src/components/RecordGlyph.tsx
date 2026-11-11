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
    | "conformer-observation"
    | "geometry"
    | "level-of-theory"
    | "thermo"
    | "kinetics"

const KIND_CLASS: Record<RecordKind, string> = {
    species: "species",
    "species-entry": "species",
    "conformer-group": "species",
    calculation: "species",
    reaction: "reaction",
    "transition-state": "ts",
    network: "network",
    "conformer-observation": "species",
    geometry: "species",
    "level-of-theory": "network",
    thermo: "species",
    kinetics: "reaction",
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
    "conformer-observation": <>
        <rect x="1.8" y="1.8" width="10.4" height="10.4" rx="3" fill="none" />
        <circle cx="7" cy="7" r="2" fillOpacity=".2" />
    </>,
    geometry: <path d="M7 1.6 12 4.3v5.4L7 12.4 2 9.7V4.3Z M7 7V12.4 M7 7 2 4.3 M7 7l5-2.7" fill="none" strokeLinejoin="round" />,
    "level-of-theory": <path d="M2 11.5V8.5h2.6v3Zm3.7 0V5.5h2.6v6Zm3.7 0V2.5H12v9Z" fillOpacity=".2" strokeLinejoin="round" />,
    thermo: <path d="M7 1.8a2 2 0 0 1 2 2v4.1a3.2 3.2 0 1 1-4 0V3.8a2 2 0 0 1 2-2Z" fillOpacity=".2" strokeLinejoin="round" />,
    kinetics: <path d="M1.8 7h8.4M7.4 3.6 10.8 7l-3.4 3.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />,
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
