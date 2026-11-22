// Writes src/domain/apiVocabulary.json from docs/guides/api_vocabulary.md: the six tables the record pages word their
// trust and reproducibility facts from. Markdown is stripped (`**` removed, backticks unwrapped); nothing is reworded.
// Run from frontend/: node scripts/extract-api-vocabulary.mjs
import { readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const SOURCE = resolve(here, "../../docs/guides/api_vocabulary.md")
const TARGET = resolve(here, "../src/domain/apiVocabulary.json")

const TABLES = {
    reviewStatus: "Review status",
    trustStatus: "Trust status (the evidence badge)",
    checkOutcome: "Check outcomes",
    hardFailReason: "Hard-fail reasons",
    reproducibilityGrade: "Reproducibility grade",
    checkAsks: "Check names",
}

const plain = (text) => text.replaceAll("**", "").replaceAll("`", "").trim()

function tableRows(lines, heading) {
    const start = lines.findIndex((line) => line === `### ${heading}`)
    if (start < 0) throw new Error(`no section "${heading}"`)
    const rows = []
    for (let i = start + 1; i < lines.length && !lines[i].startsWith("#"); i++) {
        if (!lines[i].startsWith("| `")) continue
        rows.push(lines[i].replace(/^\| /, "").replace(/ \|$/, ""))
    }
    return rows
}

export function extract(markdown) {
    const lines = markdown.split("\n")
    const out = {}
    for (const [key, heading] of Object.entries(TABLES)) {
        const entries = {}
        for (const row of tableRows(lines, heading)) {
            if (key === "checkAsks") {
                const [check, rubric, , , asks] = row.split(" | ")
                entries[`${plain(rubric)}:${plain(check)}`] = plain(asks)
            } else {
                const at = row.indexOf(" | ")
                entries[plain(row.slice(0, at))] = plain(row.slice(at + 3))
            }
        }
        if (Object.keys(entries).length === 0) throw new Error(`empty table "${heading}"`)
        out[key] = entries
    }
    return out
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    writeFileSync(TARGET, `${JSON.stringify(extract(readFileSync(SOURCE, "utf8")), null, 2)}\n`)
    console.log(`wrote ${TARGET}`)
}
