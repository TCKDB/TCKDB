import "../record-blocks.css"

/** Text the depositor typed, quoted and labelled as theirs. Renders nothing when there is none. */
export function DepositorNote({ children }: { children: string | null | undefined }) {
    if (!children) return null
    return (
        <aside className="depositor-note" aria-label="Note from the depositor">
            <p className="depositor-note-label">Note from the depositor</p>
            <blockquote>{children}</blockquote>
            <p className="depositor-note-origin">
                Written by the person who deposited this record; TCKDB shows it as given and has not checked it.
            </p>
        </aside>
    )
}
