import { describe, expect, it } from "vitest"
import { formatBytes } from "./storageFormat"

describe("formatBytes", () => {
    it("keeps sub-kibibyte sizes exact", () => {
        expect(formatBytes(0)).toBe("0 B")
        expect(formatBytes(1)).toBe("1 B")
        expect(formatBytes(1023)).toBe("1023 B")
    })

    it("steps at 1024, not 1000", () => {
        // The boundary is the whole point of choosing binary units: a
        // decimal formatter renders this "1.0 KB" at 1000 and disagrees
        // with every tool the operator would check it against.
        expect(formatBytes(1024)).toBe("1.0 KiB")
        expect(formatBytes(1536)).toBe("1.5 KiB")
        expect(formatBytes(1000)).toBe("1000 B")
    })

    it("climbs through the units", () => {
        expect(formatBytes(1024 ** 2)).toBe("1.0 MiB")
        expect(formatBytes(1024 ** 3)).toBe("1.0 GiB")
        expect(formatBytes(1024 ** 4)).toBe("1.0 TiB")
        expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GiB")
    })

    it("stops at the largest unit rather than inventing one", () => {
        // Without the `unit < UNITS.length - 1` bound this indexes past the
        // end and renders "1.0 undefined".
        expect(formatBytes(1024 ** 6)).toBe("1024.0 PiB")
    })

    it("refuses to present a broken value as a measurement", () => {
        expect(formatBytes(-1)).toBe("unknown")
        expect(formatBytes(Number.NaN)).toBe("unknown")
        expect(formatBytes(Number.POSITIVE_INFINITY)).toBe("unknown")
    })
})
