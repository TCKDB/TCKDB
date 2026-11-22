import "../record-blocks.css"

/**
 * Text the depositor typed, quoted and labelled as theirs. Renders nothing when there is none. `label` names what the
 * quote is ("As deposited" beside a warning); the default is the general label.
 */
export function DepositorNote({ children, label = "Note from the depositor" }: {
    children: string | null | undefined
    label?: string
}) {
    if (!children) return null
    return (
        <aside className="depositor-note" aria-label={label}>
            <p className="depositor-note-label">{label}</p>
            <blockquote>{children}</blockquote>
            <p className="depositor-note-origin">
                Written by the person who deposited this record; TCKDB shows it as given and has not checked it.
            </p>
        </aside>
    )
}
