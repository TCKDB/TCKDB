import { describe, expect, it } from "vitest"
import { shapeOrderSentence } from "./conformerGallery"

const reviewRank = { key: "review_rank", direction: "asc", meaning: "review status: approved first; others after" }

describe("shapeOrderSentence", () => {
    it("states the archive's order and the card order in one statement", () => {
        expect(shapeOrderSentence({ kind: "display_order", keys: [reviewRank] })).toBe(
            "Pages follow the archive's display order: approved first. The cards within a page are listed by conformer number, then label, then reference.",
        )
    })

    it("says so when the reader asked for the order, and still keeps the card order", () => {
        const sentence = shapeOrderSentence({ kind: "requested", requested_by: "sort=review_rank", keys: [reviewRank] })
        expect(sentence).toBe(
            "Pages follow the order requested (sort=review_rank): approved first. The cards within a page are listed by conformer number, then label, then reference.",
        )
    })

    it("names no order when the archive stated none", () => {
        expect(shapeOrderSentence(null)).toBe(
            "Pages follow the archive's display order. The cards within a page are listed by conformer number, then label, then reference.",
        )
    })
})
