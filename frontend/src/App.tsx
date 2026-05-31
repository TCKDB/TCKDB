import { BrowserRouter, Routes, Route, Link } from "react-router-dom"
import SpeciesPage from "./pages/SpeciesPage"
import SpeciesDetailPage from "./pages/SpeciesDetailPage"
import MachineReviewInspectionPage from "./pages/MachineReviewInspectionPage"

function Home() {
  return <h2>Home</h2>
}

function App() {
  return (
    <BrowserRouter>
      <div>
        <nav>
          <Link to="/">Home</Link> | <Link to="/species">Species</Link> |{" "}
          <Link to="/admin/machine-review-inspection">Machine-Review Inspection (admin)</Link>
        </nav>

        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/species" element={<SpeciesPage />} />
          <Route path="/species/:id" element={<SpeciesDetailPage />} />
          {/* Admin-only diagnostic view; backend enforces require_admin. */}
          <Route
            path="/admin/machine-review-inspection"
            element={<MachineReviewInspectionPage />}
          />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App