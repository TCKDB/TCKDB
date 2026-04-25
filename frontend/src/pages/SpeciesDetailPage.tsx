import { useParams } from "react-router-dom"

function SpeciesDetailPage() {
    const params = useParams()
    const id = Number(params.id)

    const speciesList = [
    { id: 1, name: "H2", smiles: "[H][H]" },
    { id: 2, name: "O2", smiles: "O=O" },
    { id: 3, name: "CH4", smiles: "C" },
  ]

    const species = speciesList.find((s) => s.id == id)

    if (!species) {
        return <p>Species not found!</p>
    }

    return (
        <div>
            <h2>Species Detail Page</h2>
            <p>ID: {species.id}</p>
            <p>Name: {species.name}</p>
            <p>SMILES: {species.smiles}</p>
        </div>
    )
}

export default SpeciesDetailPage
