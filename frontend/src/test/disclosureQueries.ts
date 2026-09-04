/**
 * `screen.getByText` (`@testing-library/dom`) matches a node's DIRECT
 * text-node children only, not text belonging to descendant elements --
 * see `getNodeText` in that package. `components/Disclosure.tsx` (design/
 * foundations) renders its optional trailing count in a nested
 * `<span className="disclosure-count">`, so a summary reading
 * "References (4)" is now split across two text nodes: the plain text
 * node `"References"` and, inside the count span, `" (4)"`. A bare
 * `getByText("References (4)")` can no longer find it -- MEASURED across
 * four test files (`App.test.tsx`, `CalculationDetailPage.test.tsx`,
 * `SpeciesEntryPage.test.tsx`, `TransitionStateEntryPage.test.tsx`) that
 * queried a `RefsDisclosure` summary this way before this component
 * existed.
 *
 * This matcher checks the `<summary>` element's full `textContent`
 * instead (which DOES include descendant text), scoped to `tagName ===
 * "SUMMARY"` so it cannot also match an ancestor `<details>` whose own
 * (much longer) `textContent` happens to contain the same substring.
 */
export function bySummaryText(match: string | RegExp) {
    return (_content: string, element: Element | null): boolean => {
        if (!element || element.tagName !== "SUMMARY") return false
        const text = element.textContent ?? ""
        return typeof match === "string" ? text === match : match.test(text)
    }
}
