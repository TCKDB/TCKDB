import { describe, expect, it } from "vitest"
import {
    absentReasonText,
    describeSetting,
    isAssumedState,
    levelRoleLabel,
    parameterStatement,
    publicSettingValue,
    reportedEntryText,
    settingStateLabel,
} from "./effectiveSettings"

describe("settingStateLabel", () => {
    it("maps each state the server names to its words", () => {
        expect(settingStateLabel("reported")).toBe("Reported by the program")
        expect(settingStateLabel("requested")).toBe("Requested")
        expect(settingStateLabel("assumed_default")).toBe("Assumed program default")
        expect(settingStateLabel("assumed_default_no_output_log")).toBe("Assumed program default (no output log)")
        expect(settingStateLabel("not_stated")).toBe("Not stated")
    })

    it("reads an unrecognised state as not stated, never as the raw token", () => {
        expect(settingStateLabel("DEPOSITOR_SENTINEL_state")).toBe("Not stated")
    })

    it("treats exactly the two default states as assumed", () => {
        expect(["reported", "requested", "assumed_default", "assumed_default_no_output_log", "not_stated"].filter(isAssumedState)).toEqual([
            "assumed_default",
            "assumed_default_no_output_log",
        ])
    })
})

describe("publicSettingValue", () => {
    it("prints a closed-set grid or convergence token and nothing else", () => {
        expect(publicSettingValue("grid", "ultrafine")).toBe("ultrafine")
        expect(publicSettingValue("grid", "DefGrid2")).toBe("defgrid2")
        expect(publicSettingValue("grid", "99590")).toBe("99590")
        expect(publicSettingValue("scf_convergence", "very_tight")).toBe("very tight")
        expect(publicSettingValue("scf_convergence", "tight")).toBe("tight")
        expect(publicSettingValue("grid", "DEPOSITOR_SENTINEL_grid")).toBeNull()
        expect(publicSettingValue("scf_convergence", "ultrafine")).toBeNull()
        expect(publicSettingValue("grid", "PISS")).toBeNull()
        expect(publicSettingValue("other", "tight")).toBeNull()
        expect(publicSettingValue("grid", null)).toBeNull()
    })
})

describe("reportedEntryText", () => {
    it("labels a known key from a fixed table and prints only a numeric value", () => {
        expect(reportedEntryText("grid.angular_max", "302")).toBe("largest angular grid 302")
        expect(reportedEntryText("scf.tol_energy_eh", "1e-08")).toBe("energy tolerance (Eh) 1e-08")
        expect(reportedEntryText("IRadAn", "0")).toBe("Gaussian IRadAn code 0")
        expect(reportedEntryText("grid.angular_max", "DEPOSITOR_SENTINEL_value")).toBe("largest angular grid recorded")
        expect(reportedEntryText("DEPOSITOR_SENTINEL_key", "1")).toBe("a recorded value")
    })
})

describe("describeSetting", () => {
    it("marks an assumed default as assumed and gives it the default's value", () => {
        const shown = describeSetting({ setting: "grid", state: "assumed_default", assumed_value: "defgrid2" })
        expect(shown).toMatchObject({ kind: "assumed", assumed: true, valueText: "defgrid2", stateLabel: "Assumed program default" })
        const noLog = describeSetting({ setting: "scf_convergence", state: "assumed_default_no_output_log", assumed_value: "tight" })
        expect(noLog).toMatchObject({ kind: "assumed", assumed: true, valueText: "tight", stateLabel: "Assumed program default (no output log)" })
    })

    it("never marks an observation as assumed", () => {
        const reported = describeSetting({ setting: "grid", state: "reported", reported: [{ key: "grid.angular_max", value: "302" }] })
        expect(reported).toMatchObject({ kind: "reported", assumed: false, valueText: null, reportedTexts: ["largest angular grid 302"] })
        const requested = describeSetting({ setting: "grid", state: "requested", requested_value: "ultrafine", requested_from: "input" })
        expect(requested).toMatchObject({ kind: "requested", assumed: false, valueText: "ultrafine", sourceText: "input file" })
    })

    it("withholds the value of a conflicting request and says so", () => {
        const shown = describeSetting({ setting: "grid", state: "requested", requested_value: null, requested_from: "typed", conflicting: true })
        expect(shown).toMatchObject({ kind: "requested", conflicting: true, valueText: null, sourceText: "stated by the depositor" })
    })

    it("says nothing about a value that is not stated", () => {
        expect(describeSetting({ setting: "grid", state: "not_stated" })).toMatchObject({ kind: "not_stated", valueText: null, reportedTexts: [] })
    })

    it("does not print a typed value outside the closed set", () => {
        const shown = describeSetting({ setting: "grid", state: "requested", requested_value: "DEPOSITOR_SENTINEL_value", requested_from: "typed" })
        expect(shown.valueText).toBe("recorded")
        expect(JSON.stringify(shown)).not.toContain("DEPOSITOR_SENTINEL")
    })
})

describe("parameterStatement", () => {
    it("labels a reported row as reported, whatever else it carries", () => {
        expect(parameterStatement({ source: "parser", artifact_kind: "output_log", statement_kind: "reported" })).toEqual({
            kind: "reported",
            label: "Reported · output log",
        })
    })

    it("labels a requested row by where the request was read", () => {
        expect(parameterStatement({ source: "parser", artifact_kind: "output_log", statement_kind: "requested" }).label).toBe("Requested · echoed in output log")
        expect(parameterStatement({ source: "parser", artifact_kind: "input", statement_kind: "requested" }).label).toBe("Requested · input file")
        expect(parameterStatement({ source: "upload" }).label).toBe("Requested · depositor")
        expect(parameterStatement({ source: "curated" })).toEqual({ kind: "curated", label: "Curated" })
        expect(parameterStatement({})).toEqual({ kind: "unknown", label: "Source not recorded" })
    })
})

describe("level words", () => {
    it("names the three levels and the reasons a level has no calculation", () => {
        expect(["geometry", "frequency", "energy"].map(levelRoleLabel)).toEqual(["Geometry", "Frequencies", "Energy"])
        expect(absentReasonText("not_linked")).toBe("No calculation linked")
        expect(absentReasonText("ambiguous")).toMatch(/disagree/)
        expect(absentReasonText("composite_recipe")).toMatch(/recipe/)
        expect(absentReasonText("DEPOSITOR_SENTINEL_reason")).toBe("No calculation linked")
    })
})
