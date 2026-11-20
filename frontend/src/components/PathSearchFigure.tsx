import { loadPathSearchPoints } from "../api/calculationPathsApi"
import { pathSearchCaption, pathSearchProfile } from "../domain/pathProfile"
import { useLoaded } from "../hooks/useLoaded"
import { PathFigure } from "./PathFigure"

/** Reads every stored point of one path search and draws energy along the path. */
export function PathSearchFigure({ calculationRef }: { calculationRef: string }) {
    const state = useLoaded(loadPathSearchPoints, calculationRef)
    if (state.status === "rate-limited") return <p className="note" role="alert">The archive is busy, so the points of this path were not loaded. Reload the page to try again.</p>
    if (state.status === "failed") return <p className="note" role="alert">The points of this path could not be loaded.</p>
    if (state.status !== "ready") return <p className="note" role="status">Loading the points of this path.</p>
    if (state.data.points.length === 0) return <p className="empty-projection">No point of this path was stored.</p>
    const profile = pathSearchProfile(state.data)
    return <PathFigure profile={profile} label="Energy along the path" caption={pathSearchCaption(profile)} />
}
