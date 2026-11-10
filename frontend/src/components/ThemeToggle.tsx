import { useRef, type KeyboardEvent } from "react"
import { useTheme, type ThemePreference } from "../hooks/useTheme"

const OPTIONS: { value: ThemePreference; label: string }[] = [
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
    { value: "system", label: "System" },
]

/**
 * Three-way Light/Dark/System control, rendered in `AppShell.tsx`'s
 * header alongside primary navigation — the one place every route shares,
 * so the toggle is reachable no matter which record a reader is on rather
 * than being buried on a settings page this archive does not otherwise
 * have. A `role="radiogroup"` of three `role="radio"` buttons: exactly
 * one of the three is ever the active choice, which is what the toggle
 * IS (never none, never more than one) — closer to the truth than a
 * checkbox-shaped on/off control would be for a three-state preference.
 */
export function ThemeToggle() {
    const { preference, setPreference } = useTheme()
    const group = useRef<HTMLDivElement>(null)

    /** Arrow keys move the choice and focus together, as a radio group does; the group is one Tab stop. */
    function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
        const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0
        if (step === 0) return
        event.preventDefault()
        const next = (index + step + OPTIONS.length) % OPTIONS.length
        setPreference(OPTIONS[next].value)
        group.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus()
    }

    return (
        <div className="theme-toggle" role="radiogroup" aria-label="Theme" ref={group}>
            {OPTIONS.map(({ value, label }, index) => (
                <button
                    key={value}
                    tabIndex={preference === value ? 0 : -1}
                    onKeyDown={(event) => onKeyDown(event, index)}
                    type="button"
                    role="radio"
                    aria-checked={preference === value}
                    className="theme-toggle-option"
                    data-active={preference === value}
                    onClick={() => setPreference(value)}
                >
                    {label}
                </button>
            ))}
        </div>
    )
}
