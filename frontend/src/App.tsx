import { lazy, Suspense } from "react"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import MachineReviewInspectionPage from "./pages/MachineReviewInspectionPage"
import { AppShell } from "./components/AppShell"
import { LoadingPage } from "./components/LoadingPage"

const ArchiveHomePage = lazy(() => import("./pages/ArchiveHomePage"))
const RecordPlaceholderPage = lazy(() => import("./pages/RecordPlaceholderPage"))
const SpeciesEntryPage = lazy(() => import("./pages/SpeciesEntryPage"))

function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<LoadingPage />}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<ArchiveHomePage />} />
            <Route path="/species" element={<RecordPlaceholderPage kind="Species" />} />
            <Route path="/species/:speciesRef" element={<RecordPlaceholderPage kind="Species" refParam="speciesRef" />} />
            <Route path="/species-entries/:entryRef" element={<SpeciesEntryPage />} />
            <Route path="/species-entries/:entryRef/:section" element={<SpeciesEntryPage />} />
            <Route path="/conformer-groups/:groupRef" element={<RecordPlaceholderPage kind="Conformer group" refParam="groupRef" />} />
            <Route path="/conformer-observations/:observationRef" element={<RecordPlaceholderPage kind="Conformer observation" refParam="observationRef" />} />
            <Route path="/calculations/:calculationRef" element={<RecordPlaceholderPage kind="Calculation" refParam="calculationRef" />} />
            <Route path="/geometries/:geometryRef" element={<RecordPlaceholderPage kind="Geometry" refParam="geometryRef" />} />
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
