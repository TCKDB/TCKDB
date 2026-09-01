import { afterEach, describe, expect, it, vi } from "vitest"
import { SectionRegistry } from "./sectionRegistry"

afterEach(() => {
    document.body.innerHTML = ""
})

describe("SectionRegistry", () => {
    it("starts empty", () => {
        expect(new SectionRegistry().getSnapshot()).toEqual([])
    })

    it("registers a section and notifies subscribers", () => {
        const registry = new SectionRegistry()
        const listener = vi.fn()
        registry.subscribe(listener)
        registry.register("thermo-heading", "Thermochemistry")
        expect(registry.getSnapshot()).toEqual([{ id: "thermo-heading", label: "Thermochemistry" }])
        expect(listener).toHaveBeenCalledTimes(1)
    })

    // No `#b`/`#a` element exists anywhere in the document for this test,
    // so document-position sorting can't compare them and falls back to
    // registration order -- this is the "can't place it yet, so leave it
    // where it was" half of the contract, not a claim that registration
    // order is what gets rendered in general. See the "orders by document
    // position" tests below for the actual display-order contract.
    it("falls back to registration order when neither section resolves in the document", () => {
        const registry = new SectionRegistry()
        registry.register("b", "B")
        registry.register("a", "A")
        expect(registry.getSnapshot().map((section) => section.id)).toEqual(["b", "a"])
    })

    it("unregisters on the returned cleanup, and notifies again", () => {
        const registry = new SectionRegistry()
        const listener = vi.fn()
        const unregister = registry.register("geometry-heading", "Geometry")
        registry.subscribe(listener)
        unregister()
        expect(registry.getSnapshot()).toEqual([])
        expect(listener).toHaveBeenCalledTimes(1)
    })

    it("re-registering the same id updates its label in place without duplicating it", () => {
        const registry = new SectionRegistry()
        registry.register("thermo-heading", "Thermochemistry")
        registry.register("thermo-heading", "Thermochemistry (renamed)")
        expect(registry.getSnapshot()).toEqual([{ id: "thermo-heading", label: "Thermochemistry (renamed)" }])
    })

    it("returns a referentially stable snapshot when nothing has changed", () => {
        const registry = new SectionRegistry()
        registry.register("a", "A")
        const first = registry.getSnapshot()
        const second = registry.getSnapshot()
        expect(first).toBe(second)
    })

    it("a listener removed via its own unsubscribe stops receiving notifications", () => {
        const registry = new SectionRegistry()
        const listener = vi.fn()
        const unsubscribe = registry.subscribe(listener)
        unsubscribe()
        registry.register("a", "A")
        expect(listener).not.toHaveBeenCalled()
    })

    // Appends real elements to `document.body` in a chosen visual order,
    // then registers them in a DELIBERATELY different order -- a fixture
    // that already registers in document order can't catch a bug that
    // only shows up when the two orders diverge.
    function appendInDocumentOrder(...ids: string[]) {
        for (const id of ids) {
            const el = document.createElement("div")
            el.id = id
            document.body.appendChild(el)
        }
    }

    it("orders the snapshot by document position, not registration order", () => {
        appendInDocumentOrder("a", "b", "c")
        const registry = new SectionRegistry()
        // Registered in the REVERSE of their document order.
        registry.register("c", "C")
        registry.register("b", "B")
        registry.register("a", "A")
        expect(registry.getSnapshot().map((section) => section.id)).toEqual(["a", "b", "c"])
    })

    it("re-sorts when a differently-ordered section replaces one in the same on-page slot (the conformer-switch bug)", () => {
        // "early" and "late" are permanent fixtures of the page; the slot
        // between them holds whichever conformer group's evidence section
        // is currently mounted.
        appendInDocumentOrder("early", "evidence-cg1", "late")
        const registry = new SectionRegistry()
        registry.register("early", "Early")
        registry.register("evidence-cg1", "Evidence for Conformer Group 1")
        registry.register("late", "Late")
        expect(registry.getSnapshot().map((s) => s.id)).toEqual(["early", "evidence-cg1", "late"])

        // Conformer switch: the old evidence section unmounts...
        const unregisterOldEvidence = registry.register("evidence-cg1", "Evidence for Conformer Group 1")
        unregisterOldEvidence()
        document.getElementById("evidence-cg1")?.remove()

        // ...and a NEW one, under a different id, mounts into the SAME
        // physical slot in the document -- but its `register()` call
        // happens after "late" is already registered, so naive mount-order
        // bookkeeping would append it at the end.
        const newEvidence = document.createElement("div")
        newEvidence.id = "evidence-cg2"
        document.body.insertBefore(newEvidence, document.getElementById("late"))
        registry.register("evidence-cg2", "Evidence for Conformer Group 2")

        expect(registry.getSnapshot().map((s) => s.id)).toEqual(["early", "evidence-cg2", "late"])
    })
})
