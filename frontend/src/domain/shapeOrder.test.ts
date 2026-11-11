import { describe, expect, it } from "vitest"
import { shapeOrderSentence } from "./conformerGallery"

const reviewRank = { key: "review_rank", direction: "asc", meaning: "review status: approved first; others after" }

describe("shapeOrderSentence", () => {
    it("states the archive's order and the card order in one statement", () => {
        expect(shapeOrderSentence({ kind: "display_order", keys: [reviewRank] })).toBe(
            "Approved first. Cards by conformer number.",
        )
    })

    it("says so when the reader asked for the order, and still keeps the card order", () => {
        const sentence = shapeOrderSentence({ kind: "requested", requested_by: "sort=review_rank", keys: [reviewRank] })
        expect(sentence).toBe(
            "Order requested (sort=review_rank): approved first. Cards by conformer number.",
        )
    })

    it("names no order when the archive stated none", () => {
        expect(shapeOrderSentence(null)).toBe(
            "Archive order. Cards by conformer number.",
        )
    })
})
