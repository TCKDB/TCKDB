import { describe, expect, it, vi } from "vitest"
import { SectionRegistry } from "./sectionRegistry"

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

    it("preserves registration order across multiple sections", () => {
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
})
