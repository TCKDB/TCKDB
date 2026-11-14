import { createContext, useContext } from "react"
import type { RecordNumbering } from "../domain/recordNumbers"

export const RecordNumbersContext = createContext<RecordNumbering>(new Map())

/** The numbering of the section the caller sits in; empty outside one. */
export const useRecordNumbers = (): RecordNumbering => useContext(RecordNumbersContext)
