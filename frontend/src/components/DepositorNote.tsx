import "../record-blocks.css"

/**
 * Text the depositor typed, quoted and labelled as theirs. Renders nothing when there is none. `label` names what the
 * quote is ("As deposited" beside a warning); the default is the general label. `lines` quotes several labelled texts
 * under one label and one origin sentence; `origin` replaces the default sentence about who wrote it. `inline` renders a
 * plain block for use inside a list item, where a landmark per note would crowd the page's landmark list.
 */
export function DepositorNote({ children, label = "Note from the depositor", lines, origin, inline = false }: {
    children?: string | null | undefined
    label?: string
    lines?: Array<{ label: string; text: string }>
    origin?: string
    inline?: boolean
}) {
    const quoted = lines ?? (children ? [{ label: "", text: children }] : [])
    if (quoted.length === 0) return null
    const body = (
        <>
            <p className="depositor-note-label">{label}</p>
            {quoted.map((line, index) => (
                <div className="depositor-note-line" key={`${line.label}-${index}`}>
                    {line.label && <p className="depositor-note-line-label">{line.label}</p>}
                    <blockquote>{line.text}</blockquote>
                </div>
            ))}
            <p className="depositor-note-origin">
                {origin ?? "Written by the person who deposited this record; TCKDB shows it as given and has not checked it."}
            </p>
        </>
    )
    return inline
        ? <div className="depositor-note" role="group" aria-label={label}>{body}</div>
        : <aside className="depositor-note" aria-label={label}>{body}</aside>
}
