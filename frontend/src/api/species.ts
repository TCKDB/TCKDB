import type { Species } from "../types/species"


/**
 * Fetch all species from the backend API
 * 
 * @returns a promise resolving to an array of Species objects
 * @throws Error if the HTTP response is not successful
 */
export async function fetchSpecies(): Promise<Species[]> {
    const response = await fetch("http:")

}