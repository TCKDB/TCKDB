import { createContext, useContext, useEffect, useSyncExternalStore } from "react"
import { SectionRegistry } from "../domain/sectionRegistry"
import type { RegisteredSection } from "../domain/sectionRegistry"

// A plain (non-component) module, split out from `components/PageSections.tsx`
// so that file can stay component-only -- react-refresh's
// `only-export-components` rule (rightly) objects to a `.tsx` file mixing
// hook and component exports, since Fast Refresh cannot preserve state
// across an edit to a file shaped like that.
export const SectionRegistryContext = createContext<SectionRegistry | null>(null)

const EMPTY_SECTIONS: RegisteredSection[] = []
const noopSubscribe = () => () => {}
const emptySnapshot = () => EMPTY_SECTIONS

/**
 * The list of sections currently mounted under the nearest
 * `PageSectionsProvider`, live -- re-renders the caller whenever a
 * section registers or unregisters. Outside any provider this is always
 * `[]`, never a thrown error, so a component using `SectionHeading` does
 * not require a provider to render correctly (it just never becomes ToC
 * -eligible).
 */
export function usePageSections(): RegisteredSection[] {
    const registry = useContext(SectionRegistryContext)
    return useSyncExternalStore(
        registry?.subscribe ?? noopSubscribe,
        registry?.getSnapshot ?? emptySnapshot,
    )
}

/**
 * Registers one section with the nearest `PageSectionsProvider` for as
 * long as the calling component stays mounted, and unregisters it on
 * unmount (a tab switch, a conditional empty state, a lazy disclosure
 * closing). Prefer `SectionHeading` (`components/PageSections.tsx`) over
 * calling this directly -- it wires the same registration to the `<h2>`
 * itself, so a heading and its ToC entry can never drift apart.
 */
export function useRegisteredSection(id: string, label: string): void {
    const registry = useContext(SectionRegistryContext)
    useEffect(() => {
        if (!registry) return
        return registry.register(id, label)
    }, [registry, id, label])
}
