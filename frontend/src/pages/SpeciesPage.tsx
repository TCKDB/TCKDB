import type { Species } from "../types/species"
import { speciesList } from "../data/speciesData"
import SpeciesCard from "../components/SpeciesCard"

function SpeciesPage() {

    // need to return in a jsx format
    return (
        <div>
            <h2>Species</h2>
            {speciesList.map((s) => (
                <SpeciesCard key={s.id} species={s} />
            ))}
            </div>
            )
}

export default SpeciesPage
