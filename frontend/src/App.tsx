import { lazy, Suspense } from "react"
import { BrowserRouter, Route, Routes, useParams } from "react-router-dom"
import MachineReviewInspectionPage from "./pages/MachineReviewInspectionPage"
import { AppShell } from "./components/AppShell"
import { LoadingPage } from "./components/LoadingPage"
import { BROWSE_KIND_PATHS } from "./api/browseApi"
import { isEntrySection, LEGACY_ENTRY_SECTION_ALIASES } from "./domain/speciesEntrySections"

const ArchiveHomePage = lazy(() => import("./pages/ArchiveHomePage"))
const BrowsePage = lazy(() => import("./pages/BrowsePage"))
const CalculationDetailPage = lazy(() => import("./pages/CalculationDetailPage"))
const ConformerGroupPage = lazy(() => import("./pages/ConformerGroupPage"))
const ConformerObservationPage = lazy(() => import("./pages/ConformerObservationPage"))
const CorrectionSchemePage = lazy(() => import("./pages/CorrectionSchemePage"))
const FrequencyScaleFactorPage = lazy(() => import("./pages/FrequencyScaleFactorPage"))
const GeometryDetailPage = lazy(() => import("./pages/GeometryDetailPage"))
const LevelOfTheoryPage = lazy(() => import("./pages/LevelOfTheoryPage"))
const MethodsIndexPage = lazy(() => import("./pages/MethodsIndexPage"))
const NotFoundPage = lazy(() => import("./pages/NotFoundPage"))
const ReactionEntryPage = lazy(() => import("./pages/ReactionEntryPage"))
const ReactionOverviewPage = lazy(() => import("./pages/ReactionOverviewPage"))
const SpeciesEntryPage = lazy(() => import("./pages/SpeciesEntryPage"))
const SpeciesOverviewPage = lazy(() => import("./pages/SpeciesOverviewPage"))
const TransitionStateEntryPage = lazy(() => import("./pages/TransitionStateEntryPage"))

function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<LoadingPage />}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<ArchiveHomePage />} />
            {/* Every browse kind renders the SAME `BrowsePage`, at its own
                path (owner decision, replacing the earlier single `/species
                ?kind=` surface) -- built from `BROWSE_KIND_PATHS` rather
                than four literal path strings so this table and
                `browseKindForPath`'s reverse lookup (which `BrowsePage`
                itself uses to fix its kind from the route) cannot drift
                apart. `BrowsePage` is the exact same component reference at
                all four paths, which matters beyond DRY: React Router
                reconciles the ONE matched route's `element` like any other
                subtree (see `SpeciesEntrySectionRoute`'s doc comment below
                for the general rule) -- same type at the same position
                (the `AppShell` `Outlet`'s one child slot) keeps the
                component instance, so navigating between kinds via
                `BrowseKindSelector` carries filter state across without any
                type-identity remount. `/species` doubles as the legacy
                `?kind=` redirect target (`BrowsePage`'s own effect) since
                it is the one path every pre-existing browse link used. */}
            <Route path={BROWSE_KIND_PATHS.species} element={<BrowsePage />} />
            <Route path="/species/:speciesRef" element={<SpeciesOverviewPage />} />
            {/* Both the sectionless and `:section` routes render the SAME
                element type, `SpeciesEntrySectionRoute` -- this is not
                cosmetic. React Router matches a Route by *config entry*,
                and `<Routes>` reconciles the matched `element` like any
                other React subtree: same type at the same position keeps
                the component instance (and its state) across a navigation;
                a different type unmounts the old instance and mounts a
                fresh one. Before this fix, the sectionless route above
                rendered `<SpeciesEntryPage />` directly while the `:section`
                route below rendered the DIFFERENT `<SpeciesEntrySectionRoute
                />` wrapper -- so the very first tab click (which moves the
                URL from `/species-entries/:entryRef` to
                `/species-entries/:entryRef/:section`) forced a full
                unmount+remount of `SpeciesEntryPage`, throwing away
                `useSpeciesEntry`'s already-loaded state and refiring its
                three requests (entry, conformers, single-point energies)
                from scratch -- visible to the user as the whole page
                reloading, even though it never left the SPA. Every
                SUBSEQUENT tab click stays within the `:section` route (same
                element type both sides), so it never remounted -- matching
                the reported "first click reloads, later clicks don't".
                Sharing one element type here means neither route ever
                remounts `SpeciesEntryPage` on its own account again; a
                real re-fetch now only happens when `entryRef` itself
                changes. `dedupedFetch` (`api/requestCache.ts`,
                `hooks/useSpeciesEntry.ts`) is the second layer, covering
                the case that DOES still remount the page (browser
                Back/forward across an unrelated page in between). */}
            <Route path="/species-entries/:entryRef" element={<SpeciesEntrySectionRoute />} />
            {/* The `:section` param is kept as a real route param -- not
                flattened into literal per-tab routes -- because
                `SpeciesEntryPage` itself reads it via `useParams` to pick
                the active tab and to canonicalize the legacy `/calculations`
                alias to the default tab's own path (its own effect names
                that alias case by name). Flattening the param away would
                leave every literal-segment route with no `:section` to
                read, so the page could no longer tell `/sp` from `/thermo`
                from the default -- every tab would silently render Geometry
                regardless of the URL, the exact bug finding #12 was about,
                just moved one level down instead of fixed.
                `SpeciesEntrySectionRoute` below is the gate finding #12
                actually asks for: an unrecognized segment (a stale link, a
                mistyped guess like `/single-point`) renders the not-found
                page instead of reaching `SpeciesEntryPage` at all, while a
                recognized section OR the `calculations` alias still reaches
                it so its own canonicalization effect can run. */}
            <Route path="/species-entries/:entryRef/:section" element={<SpeciesEntrySectionRoute />} />
            <Route path="/conformer-groups/:groupRef" element={<ConformerGroupPage />} />
            <Route path="/conformer-observations/:observationRef" element={<ConformerObservationPage />} />
            <Route path="/calculations/:calculationRef" element={<CalculationDetailPage />} />
            <Route path="/geometries/:geometryRef" element={<GeometryDetailPage />} />
            <Route path={BROWSE_KIND_PATHS.vdw} element={<BrowsePage />} />
            <Route path={BROWSE_KIND_PATHS.transition_state} element={<BrowsePage />} />
            <Route path="/transition-state-entries/:entryRef" element={<TransitionStateEntryPage />} />
            <Route path="/reaction-entries/:entryRef" element={<ReactionEntryPage />} />
            {/* Was `<RecordPlaceholderPage kind="Reactions" />` -- a dead
                end the site nav's "Reactions" link pointed straight at,
                while the working reaction browse was reachable only via
                `/species?kind=reaction`. Now the SAME `BrowsePage` every
                other kind uses. */}
            <Route path={BROWSE_KIND_PATHS.reaction} element={<BrowsePage />} />
            <Route path="/reactions/:reactionRef" element={<ReactionOverviewPage />} />
            {/* Was `<RecordPlaceholderPage kind="Methods" />` -- the last
                consumer of that component (see its own file: every other
                route that used to render it now has a real page). Three
                more routes below it: `/methods/:lotRef` is the real
                level-of-theory record page (methods-surface plan §4.2);
                `/methods/schemes/:ecsRef` and
                `/methods/frequency-scale-factors/:fsfRef` are the thin
                anchor pages §4.3 gives the two other dead-ref kinds found
                on this site (an energy-correction-scheme ref, a
                frequency-scale-factor ref). No ordering hazard between
                `/methods/:lotRef` and the two three-segment routes below
                it -- `/methods/schemes/ecs_…` has one more path segment
                than `/methods/:lotRef` can ever match, so React Router's
                ranked matching (specificity, not declaration order) sends
                it to the right route regardless of which is listed first;
                listed in this order only because it reads best next to
                the index above it. */}
            <Route path="/methods" element={<MethodsIndexPage />} />
            <Route path="/methods/schemes/:ecsRef" element={<CorrectionSchemePage />} />
            <Route path="/methods/frequency-scale-factors/:fsfRef" element={<FrequencyScaleFactorPage />} />
            <Route path="/methods/:lotRef" element={<LevelOfTheoryPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
          <Route
            path="/admin/machine-review-inspection"
            element={<MachineReviewInspectionPage />}
          />
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}

// Gate for the one route where "unrecognized" has two different answers.
// `calculations` is a stale link from the earlier chapter-nav design that
// `SpeciesEntryPage` itself still knows how to redirect to the default tab
// (see its own effect); every OTHER unrecognized segment -- a mistyped
// guess like `/single-point`, never a URL this app served -- is finding
// #12's silent-Geometry-tab bug and gets the not-found page instead.
function SpeciesEntrySectionRoute() {
  const { section } = useParams<{ section?: string }>()
  if (section !== undefined && !isEntrySection(section) && !LEGACY_ENTRY_SECTION_ALIASES.has(section)) {
    return <NotFoundPage />
  }
  return <SpeciesEntryPage />
}

export default App
