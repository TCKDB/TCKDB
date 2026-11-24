import { describe, expect, it } from "vitest"
import { blockingAvailability, okProviders } from "./machineReviewProviders"
import * as fx from "../test/machineReviewFixtures"
import { ProvidersSchema } from "../types/machineReviewJob"

const read = (over: Record<string, string> = {}) => ProvidersSchema.parse(fx.providers(over)).providers

describe("which reviewers can be asked", () => {
    it("lists the ones the server marks ok and gives no blocking reason", () => {
        expect(okProviders(read()).map((p) => p.provider_key)).toEqual(["fake"])
        expect(blockingAvailability(read())).toBeNull()
    })

    it("explains a switched-off curator by the switch, not by the reviewers that are not set up", () => {
        const off = read({ fake: "curator_requests_disabled", local: "curator_requests_disabled", cloud: "curator_requests_disabled", claude_code: "curator_requests_disabled", codex: "curator_requests_disabled" })
        expect(okProviders(off)).toEqual([])
        expect(blockingAvailability(off)).toBe("curator_requests_disabled")
    })

    it("prefers a deployment-wide reason over a per-reviewer one", () => {
        const mixed = read({ fake: "provider_not_configured", local: "mode_off" })
        expect(blockingAvailability(mixed)).toBe("mode_off")
    })
})
