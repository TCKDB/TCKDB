import { useCallback, useEffect, useId, useRef, useState } from "react"
import "../geometry-viewer.css"
import "../viewer-stage.css"
import { GeometryViewer, type ViewerStatus } from "./GeometryViewer"
import { MolPoster } from "./MolPoster"
import { MAX_BOND_ATOMS } from "../domain/molPosterLayout"
import { useAnnounce } from "../hooks/useAnnounce"
import { useLiveViewerSlot } from "../hooks/useLiveViewerSlot"
import type { GeometryAtom } from "../api/geometryApi"
import type { CoordinateUnitMode } from "../domain/geometryXyz"

type StageMode = "poster" | "live" | "failed"

const BONDS_NOTE =
    "Bonds are drawn where two atoms are close enough to be bonded. The record itself stores only atoms and their positions."
const NO_BONDS_NOTE = "Bonds are not drawn for molecules this large."
const FAILED_NOTE =
    "The interactive 3D view could not start in this browser; the picture shows the same stored coordinates."

/** Starts the 3Dmol download early; the viewer's own import then resolves from the module cache. */
function preloadViewerLibrary() {
    void import("3dmol").catch(() => {})
}

/**
 * A picture first, the interactive viewer on request. Only one stage per page holds a live viewer; opening another
 * returns the first to its picture. If WebGL is missing the picture stays and one sentence says why.
 */
export function ViewerStage({ atoms, formula, xyzText, coordinateUnitMode = "angstrom" }: {
    atoms: GeometryAtom[]
    formula: string
    xyzText: string | null
    coordinateUnitMode?: CoordinateUnitMode
}) {
    const id = useId()
    const slot = useLiveViewerSlot()
    const announce = useAnnounce()
    const [mode, setMode] = useState<StageMode>("poster")
    const stageRef = useRef<HTMLDivElement | null>(null)
    const openRef = useRef<HTMLButtonElement | null>(null)
    const closeRef = useRef<HTMLButtonElement | null>(null)
    const failedRef = useRef<HTMLParagraphElement | null>(null)
    const wantsControlsRef = useRef(false)
    const controlsFocusedRef = useRef(false)
    const refocusOpenRef = useRef(false)
    const name = formula || "this geometry"

    useEffect(() => () => slot?.release(id), [slot, id])

    // Focus follows the stage: the close button while the viewer loads, the controls once ready, the open button after
    // closing by hand, and the failure sentence when the viewer cannot start.
    useEffect(() => {
        if (mode === "live") {
            if (!controlsFocusedRef.current) closeRef.current?.focus()
        }
        else if (mode === "failed") failedRef.current?.focus()
        else if (refocusOpenRef.current) {
            refocusOpenRef.current = false
            openRef.current?.focus()
        }
    }, [mode])

    const open = () => {
        wantsControlsRef.current = true
        controlsFocusedRef.current = false
        slot?.claim(id, () => {
            wantsControlsRef.current = false
            setMode("poster")
            announce(`The 3D view of ${name} was closed because another opened. Its picture is shown.`)
        })
        setMode("live")
    }

    const close = () => {
        slot?.release(id)
        wantsControlsRef.current = false
        refocusOpenRef.current = true
        setMode("poster")
    }

    const onStatusChange = useCallback((status: ViewerStatus) => {
        if (status === "unavailable") {
            slot?.release(id)
            wantsControlsRef.current = false
            setMode("failed")
        } else if (status === "ready" && wantsControlsRef.current) {
            wantsControlsRef.current = false
            controlsFocusedRef.current = true
            stageRef.current?.querySelector<HTMLElement>(".viewer-controls")?.focus()
        }
    }, [slot, id])

    return (
        <div className="viewer-poster-stage" ref={stageRef}>
            {mode === "live" ? (
                <>
                    <button type="button" className="viewer-poster-action" ref={closeRef} onClick={close}>Show picture</button>
                    <GeometryViewer
                        atoms={atoms}
                        formula={formula}
                        xyzText={xyzText}
                        coordinateUnitMode={coordinateUnitMode}
                        onStatusChange={onStatusChange}
                    />
                </>
            ) : (
                <>
                    <div className="viewer-poster-frame">
                        <MolPoster atoms={atoms} formula={formula} />
                    </div>
                    <p className="section-note viewer-poster-note">
                        {atoms.length > MAX_BOND_ATOMS ? NO_BONDS_NOTE : BONDS_NOTE}
                    </p>
                    {mode === "failed" ? (
                        <p className="empty-projection" role="status" tabIndex={-1} ref={failedRef}>{FAILED_NOTE}</p>
                    ) : (
                        <button
                            type="button"
                            className="viewer-poster-action"
                            ref={openRef}
                            onClick={open}
                            onMouseEnter={preloadViewerLibrary}
                            onFocus={preloadViewerLibrary}
                        >
                            Show in 3D
                        </button>
                    )}
                </>
            )}
        </div>
    )
}
