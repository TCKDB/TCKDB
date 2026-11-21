import { http, HttpResponse } from "msw"

// Catalogue cards shaped like `GET /scientific/methods` and `/basis-sets` answers, for page and component tests.

/** Empty catalogue lists, for tests of the methods index that are about something else. */
export function emptyCatalogueHandlers() {
    const empty = { request: { include: [] }, review_summary: { approved: 0, under_review: 0, not_reviewed: 0, deprecated: 0, rejected: 0, total: 0 }, records: [] }
    return [
        http.get("/api/v1/scientific/methods", () => HttpResponse.json(empty)),
        http.get("/api/v1/scientific/basis-sets", () => HttpResponse.json(empty)),
    ]
}

export function gfn2Reference(overrides: Record<string, unknown> = {}) {
    return {
        literature_ref: "lit_gfn2",
        role: "original",
        position: 0,
        title: "GFN2-xTB: an accurate and broadly parametrized self-consistent tight-binding method",
        title_source: "resolved_metadata",
        journal: "Journal of Chemical Theory and Computation",
        year: 2019,
        volume: "15",
        issue: "3",
        pages: "1652-1671",
        authors: ["Bannwarth", "Ehlert", "Grimme"],
        doi: "10.1021/acs.jctc.8b01176",
        ...overrides,
    }
}

export function gfn2Card(overrides: Record<string, unknown> = {}) {
    return {
        catalogue_ref: "mcat_gfn2",
        key: "gfn2-xtb",
        keys: ["gfn2-xtb", "gfn2"],
        kind: "method",
        short_name: "GFN2-xTB",
        full_name: "Geometry, Frequency, Noncovalent, eXtended Tight Binding, second parametrisation",
        description: "A self-consistent tight-binding method with multipole electrostatics.",
        family: "semiempirical",
        dft_rung: null,
        references: [
            gfn2Reference(),
            gfn2Reference({
                literature_ref: "lit_wires",
                role: "implementation",
                position: 1,
                title: "Extended tight-binding quantum chemistry methods",
                journal: "WIREs Computational Molecular Science",
                year: 2021,
                volume: "11",
                pages: "e1493",
                authors: ["Bannwarth", "Caldeweyher", "Ehlert", "Hansen", "Pracht", "Seibert", "Spicher", "Grimme"],
                doi: "10.1002/wcms.1493",
            }),
        ],
        spellings_seen: ["gfn2"],
        usage: { level_usage_count: 1, calculation_usage_count: 2 },
        ...overrides,
    }
}

export function b3lypCard(overrides: Record<string, unknown> = {}) {
    return {
        catalogue_ref: "mcat_b3lyp",
        key: "b3lyp",
        keys: ["b3lyp"],
        kind: "method",
        short_name: "B3LYP",
        full_name: "Becke three-parameter hybrid exchange with Lee-Yang-Parr correlation",
        description: "A global hybrid density functional.",
        family: "dft",
        dft_rung: 4,
        references: [gfn2Reference({ literature_ref: "lit_stephens", title: "Ab initio calculation of vibrational spectra", journal: "The Journal of Physical Chemistry", year: 1994, volume: "98", pages: "11623-11627", authors: ["Stephens", "Devlin", "Chabalowski", "Frisch"], doi: "10.1021/j100096a001" })],
        spellings_seen: ["b3lyp"],
        usage: { level_usage_count: 4, calculation_usage_count: 416 },
        ...overrides,
    }
}

export function def2tzvpCard(overrides: Record<string, unknown> = {}) {
    return {
        catalogue_ref: "bcat_def2tzvp",
        key: "def2-tzvp",
        keys: ["def2-tzvp"],
        role: "orbital",
        family: "Karlsruhe def2",
        short_name: "def2-TZVP",
        full_name: "Karlsruhe def2 triple-zeta valence polarised basis set",
        description: "The triple-zeta valence basis set with polarisation functions of the Karlsruhe def2 family.",
        references: [gfn2Reference({ literature_ref: "lit_weigend", title: "Balanced basis sets of split valence, triple zeta valence and quadruple zeta valence quality", journal: "Physical Chemistry Chemical Physics", year: 2005, volume: "7", pages: "3297", authors: ["Weigend", "Ahlrichs"], doi: "10.1039/b508541a" })],
        spellings_seen: ["def2tzvp"],
        usage: { level_usage_count: 6, calculation_usage_count: 500 },
        ...overrides,
    }
}

export function auxCard(overrides: Record<string, unknown> = {}) {
    return {
        catalogue_ref: "bcat_augccpvtzc",
        key: "aug-cc-pvtz/c",
        keys: ["aug-cc-pvtz/c"],
        role: "auxiliary_correlation",
        family: "Dunning correlation-consistent",
        short_name: "aug-cc-pVTZ/C",
        full_name: "Augmented correlation-consistent polarised valence triple-zeta RI-MP2 fitting basis",
        description: "The auxiliary basis set for resolution-of-identity fitting of the correlation treatment.",
        references: [],
        spellings_seen: ["aug-cc-pvtz/c"],
        usage: { level_usage_count: 3, calculation_usage_count: 20 },
        ...overrides,
    }
}
