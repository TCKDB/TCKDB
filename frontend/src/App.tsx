import { lazy, Suspense } from "react"
import { BrowserRouter, Route, Routes, useParams } from "react-router-dom"
import MachineReviewInspectionPage from "./pages/MachineReviewInspectionPage"
import { AppShell } from "./components/AppShell"
import { LoadingPage } from "./components/LoadingPage"
import { isEntrySection } from "./domain/speciesEntrySections"

const ArchiveHomePage = lazy(() => import("./pages/ArchiveHomePage"))
const BrowsePage = lazy(() => import("./pages/BrowsePage"))
const CalculationDetailPage = lazy(() => import("./pages/CalculationDetailPage"))
const ConformerGroupPage = lazy(() => import("./pages/ConformerGroupPage"))
const ConformerObservationPage = lazy(() => import("./pages/ConformerObservationPage"))
const GeometryDetailPage = lazy(() => import("./pages/GeometryDetailPage"))
const NotFoundPage = lazy(() => import("./pages/NotFoundPage"))
const RecordPlaceholderPage = lazy(() => import("./pages/RecordPlaceholderPage"))
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
            <Route path="/species" element={<BrowsePage />} />
            <Route path="/species/:speciesRef" element={<SpeciesOverviewPage />} />
            <Route path="/species-entries/:entryRef" element={<SpeciesEntryPage />} />
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
            <Route path="/transition-state-entries/:entryRef" element={<TransitionStateEntryPage />} />
            <Route path="/reactions" element={<RecordPlaceholderPage kind="Reactions" />} />
            <Route path="/reactions/:reactionRef" element={<RecordPlaceholderPage kind="Reaction" refParam="reactionRef" />} />
            <Route path="/methods" element={<RecordPlaceholderPage kind="Methods" />} />
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
  if (section !== undefined && !isEntrySection(section) && section !== "calculations") {
    return <NotFoundPage />
  }
  return <SpeciesEntryPage />
}

export default App
