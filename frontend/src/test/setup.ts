import "@testing-library/jest-dom/vitest"
import { configure } from "@testing-library/react"

/**
 * Testing Library's default `asyncUtilTimeout` is 1000ms, which is tuned for
 * a component resolving a promise, not for this suite's shape: render →
 * React Query → MSW intercept → resolve → re-render, in jsdom, under vitest's
 * file-level parallelism.
 *
 * That budget is comfortable on a 20-core workstation and not on a 4-vCPU CI
 * runner. The symptom is a `findBy*` timing out with the page still showing
 * its loading state — the data was on its way, the window closed first. It was
 * observed four times on 2026-08-30 in four different files (`SpeciesEntryPage`,
 * `SpeciesOverviewPage`, and twice under full-suite load), never reproducibly
 * in isolation, and once in CI on a PR whose own tests were unrelated. See
 * issue #286.
 *
 * This raises the window; it does not weaken any assertion. Every query still
 * has to find its element and every expectation still has to hold — a genuinely
 * broken render fails at 5000ms exactly as it failed at 1000ms, just later.
 * What it stops is a *passing* render being reported as a failure because a
 * shared runner was busy. These are jsdom tests against a mocked network, so
 * the number measures scheduler latency, not application performance, and
 * treating it as a performance guard would be reading it for something it
 * cannot tell you.
 */
configure({ asyncUtilTimeout: 5000 })
