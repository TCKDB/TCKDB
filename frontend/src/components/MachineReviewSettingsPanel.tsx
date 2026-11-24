import { useEffect, useId, useState } from "react"
import { getMachineReviewSettings, updateMachineReviewSettings } from "../api/machineReviewJobsApi"
import type { MachineReviewSettings } from "../types/machineReviewJob"

type Field = "machine_review_enabled" | "curator_requests_enabled"

const MODE_WORDS: Record<string, string> = {
    off: "off",
    test: "the scripted test reviewer",
    local: "a local model",
    cloud: "a cloud model",
}

/**
 * The two machine-review switches, for the admin page.
 *
 * "Machine review on" gates every request and every queued review as it starts; "Curators may request reviews" gates
 * curators only, and an administrator is never held by it. Each box saves by itself and sends only its own field.
 * A deployment setting can override a stored switch: when the stored switch is on but the deployment does not allow
 * it, the panel says so, because the box would otherwise promise something the archive will not do.
 */
export function MachineReviewSettingsPanel() {
    const [settings, setSettings] = useState<MachineReviewSettings | null>(null)
    const [loadFailed, setLoadFailed] = useState(false)
    const [saving, setSaving] = useState<Field | null>(null)
    const [saved, setSaved] = useState("")
    const [error, setError] = useState("")
    const ids = { review: useId(), curators: useId() }

    useEffect(() => {
        let live = true
        getMachineReviewSettings().then(
            (value) => live && setSettings(value),
            () => live && setLoadFailed(true),
        )
        return () => {
            live = false
        }
    }, [])

    async function save(field: Field, value: boolean) {
        if (saving !== null) return
        setSaving(field)
        setSaved("")
        setError("")
        try {
            const next = await updateMachineReviewSettings({ [field]: value })
            setSettings(next)
            setSaved(
                field === "machine_review_enabled"
                    ? `Machine review is now ${value ? "on" : "off"}.`
                    : `Curators may ${value ? "now" : "no longer"} request reviews.`,
            )
        } catch {
            setError("The setting could not be saved.")
        } finally {
            setSaving(null)
        }
    }

    return (
        <section aria-labelledby="machine-review-settings-heading">
            <h2 id="machine-review-settings-heading">Machine review</h2>
            {loadFailed && <p className="auth-error" role="alert">The machine-review settings could not be loaded.</p>}
            {settings === null && !loadFailed && <p>Loading the machine-review settings…</p>}
            {settings !== null && (
                <>
                    <p className="admin-hint">
                        Reviewer in this deployment: {MODE_WORDS[settings.assistant_mode] ?? settings.assistant_mode}.
                    </p>
                    <p>
                        <input
                            id={ids.review}
                            type="checkbox"
                            checked={settings.machine_review_enabled}
                            aria-disabled={saving !== null ? "true" : undefined}
                            onChange={(e) => void save("machine_review_enabled", e.target.checked)}
                        />{" "}
                        <label htmlFor={ids.review}>Machine review on</label>
                    </p>
                    {settings.machine_review_enabled && !settings.review_allowed && (
                        <p className="admin-hint">
                            This is on, but the deployment setting overrides it, so no review can be requested.
                        </p>
                    )}
                    <p>
                        <input
                            id={ids.curators}
                            type="checkbox"
                            checked={settings.curator_requests_enabled}
                            aria-disabled={saving !== null || !settings.machine_review_enabled ? "true" : undefined}
                            aria-describedby={!settings.machine_review_enabled ? `${ids.curators}-why` : undefined}
                            onChange={(e) => {
                                if (settings.machine_review_enabled) void save("curator_requests_enabled", e.target.checked)
                            }}
                        />{" "}
                        <label htmlFor={ids.curators}>Curators may request reviews</label>
                    </p>
                    {!settings.machine_review_enabled && (
                        <p id={`${ids.curators}-why`} className="admin-hint">
                            Machine review is off, so this switch has nothing to decide.
                        </p>
                    )}
                    {settings.machine_review_enabled && settings.curator_requests_enabled && !settings.curator_allowed && (
                        <p className="admin-hint">
                            This is on, but the deployment setting overrides it, so curators cannot request reviews.
                        </p>
                    )}
                </>
            )}
            <div role="status" aria-label="machine review settings saved">{saved}</div>
            {error !== "" && <p className="auth-error" role="alert">{error}</p>}
        </section>
    )
}
