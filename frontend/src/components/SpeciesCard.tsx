import { Link } from "react-router-dom"
import type { Species } from "../types/species"

type Props = {
    species: Species
}

function SpeciesCard({ species }: Props ) {
    return (
        <div style={{ marginBottom: "10px" }}>
            <p>
            <Link to={`/species/${species.id}`}><strong>{species.name}</strong></Link>
            </p>
            ({species.smiles})
            <hr style={{ border: "1px solid #797878", margin: "10px 0" }}/>
        </div>
    )
}

export default SpeciesCard
