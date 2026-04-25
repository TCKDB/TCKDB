import { BrowserRouter, Routes, Route, Link } from "react-router-dom"
import SpeciesPage from "./pages/SpeciesPage"
import SpeciesDetailPage from "./pages/SpeciesDetailPage"

function Home() {
  return <h2>Home</h2>
}

function App() {
  return (
    <BrowserRouter>
      <div>
        <nav>
          <Link to="/">Home</Link> | <Link to="/species">Species</Link>
        </nav>

        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/species" element={<SpeciesPage />} />
          <Route path="/species/:id" element={<SpeciesDetailPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App