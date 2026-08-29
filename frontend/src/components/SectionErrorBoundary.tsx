import { Component, type ErrorInfo, type ReactNode } from "react"

/**
 * A small class-based error boundary (React requires a class for this —
 * there is no hook equivalent) scoped to one page section, not the whole
 * route.
 *
 * Built for `GeometryDetailPage`'s structure projection: that section
 * mounts a hand-rolled SVG renderer with no dependency to fall back on if
 * its own math throws (a NaN/Infinity from a degenerate geometry, a
 * malformed atom row that slipped past schema validation, etc). Before
 * this boundary existed, an uncaught render error in `GeometryViewer`
 * unmounted the *entire* route — the coordinate table, the raw XYZ block,
 * and both provenance tables disappeared along with the picture that
 * failed, which is the exact failure this project's plan was written
 * against: a decorative visual must never be able to take the actual
 * data down with it. See `GeometryDetailPage.test.tsx`'s
 * "a broken projection leaves the rest of the page standing" test.
 */
export class SectionErrorBoundary extends Component<
    { fallback: ReactNode; children: ReactNode },
    { hasError: boolean }
> {
    state = { hasError: false }

    static getDerivedStateFromError() {
        return { hasError: true }
    }

    componentDidCatch(error: Error, info: ErrorInfo) {
        // The only place this project logs a caught render error; no
        // telemetry sink exists yet.
        console.error("SectionErrorBoundary caught a render error:", error, info.componentStack)
    }

    render() {
        if (this.state.hasError) return this.props.fallback
        return this.props.children
    }
}
