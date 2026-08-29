import { lazy, Suspense } from "react"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import MachineReviewInspectionPage from "./pages/MachineReviewInspectionPage"
import { AppShell } from "./components/AppShell"
import { LoadingPage } from "./components/LoadingPage"

const ArchiveHomePage = lazy(() => import("./pages/ArchiveHomePage"))
const CalculationDetailPage = lazy(() => import("./pages/CalculationDetailPage"))
const ConformerGroupPage = lazy(() => import("./pages/ConformerGroupPage"))
const ConformerObservationPage = lazy(() => import("./pages/ConformerObservationPage"))
const GeometryDetailPage = lazy(() => import("./pages/GeometryDetailPage"))
const RecordPlaceholderPage = lazy(() => import("./pages/RecordPlaceholderPage"))
const SpeciesEntryPage = lazy(() => import("./pages/SpeciesEntryPage"))
const SpeciesOverviewPage = lazy(() => import("./pages/SpeciesOverviewPage"))

function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<LoadingPage />}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<ArchiveHomePage />} />
            <Route path="/species" element={<RecordPlaceholderPage kind="Species" />} />
            <Route path="/species/:speciesRef" element={<SpeciesOverviewPage />} />
            <Route path="/species-entries/:entryRef" element={<SpeciesEntryPage />} />
            <Route path="/species-entries/:entryRef/:section" element={<SpeciesEntryPage />} />
            <Route path="/conformer-groups/:groupRef" element={<ConformerGroupPage />} />
            <Route path="/conformer-observations/:observationRef" element={<ConformerObservationPage />} />
            <Route path="/calculations/:calculationRef" element={<CalculationDetailPage />} />
            <Route path="/geometries/:geometryRef" element={<GeometryDetailPage />} />
            <Route path="/reactions" element={<RecordPlaceholderPage kind="Reactions" />} />
            <Route path="/reactions/:reactionRef" element={<RecordPlaceholderPage kind="Reaction" refParam="reactionRef" />} />
            <Route path="/methods" element={<RecordPlaceholderPage kind="Methods" />} />
          </Route>
          <Route
            path="/admin/machine-review-inspection"
            element={<MachineReviewInspectionPage />}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}

export default App
