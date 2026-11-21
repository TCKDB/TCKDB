# Runbook — publishing the Python packages to PyPI

Audience: a TCKDB maintainer releasing one of the Python distributions. This is
the procedure; the workflow is `.github/workflows/publish-python.yml`.

| Distribution | Directory | Import name |
|---|---|---|
| `tckdb-schemas` | `schemas/python/tckdb-schemas` | `tckdb_schemas` |
| `tckdb-client` | `clients/python` | `tckdb_client` |
| `tckdb-mcp` | `integrations/mcp` | `tckdb_mcp` |

The list is `PACKAGES` in `backend/scripts/check_package_version_bump.py`; the
workflow reads it through `backend/scripts/python_release.py`. `tckdb-backend`
is not published to PyPI: it ships as the Docker image. The file-format
adapters `tckdb-chemkin` and `tckdb-qcschema`, and the ARC adapter, are
published from the [tckdb-adapters](https://github.com/TCKDB/tckdb-adapters)
repository by its own workflow (`docs/publishing.md` there).

---

## 0. One-time setup

Uploads use PyPI trusted publishing: GitHub Actions proves its identity with an
OpenID Connect token, and no API token or secret is stored anywhere.

**PyPI and TestPyPI accounts.** One account on each of https://pypi.org and
https://test.pypi.org, both with two-factor authentication.

**Pending trusted publishers.** On each index: Account settings → Publishing →
"Add a new pending publisher" → GitHub. One entry per distribution, with these
values:

| Field | Value |
|---|---|
| PyPI Project Name | `tckdb-schemas`, `tckdb-client`, `tckdb-mcp` (one entry each) |
| Owner | `TCKDB` |
| Repository name | `TCKDB` |
| Workflow name | `publish-python.yml` |
| Environment name | `pypi` on pypi.org; `testpypi` on test.pypi.org |

A pending publisher creates the project on its first upload; nothing has to be
uploaded by hand first.

**GitHub environments.** In `TCKDB/TCKDB` → Settings → Environments, create
`testpypi` and `pypi`. On `pypi`, add a required reviewer, and limit deployment
to tags matching `tckdb-*-v*`. Neither environment needs a secret.

---

## 1. Tags

A release tag is `<dist-name>-v<version>`, for example `tckdb-client-v0.208.3`.
The workflow refuses a tag when:

- its name is not one of the three distributions;
- its version differs from `[project].version` in that package's
  `pyproject.toml` at the tagged commit;
- the tagged commit is not on `main`.

The version guard (`check_package_version_bump.py`) already makes every
version on `main` unique, so a tag names exactly one content. Not every
version bump is published: tag only the versions you mean to release. Backend
image tags (`v*`) are a separate scheme and do not start this workflow.

---

## 2. What a run does

1. **Build, check and smoke-test.** Resolve the package, build the sdist and
   wheel (`python -m build`), run `twine check --strict`, build the first-party
   packages it requires (`tckdb-schemas`, `tckdb-client`) from the same commit,
   install everything into a fresh virtual environment with the rest from PyPI,
   import the package from site-packages, compare `__version__` and the
   installed version with the tag, and load each console script.
2. **Publish to TestPyPI** (environment `testpypi`). A version already there is
   skipped, so a dry run followed by the tag does not fail.
3. **Publish to PyPI** (environment `pypi`), only for a pushed tag and only
   after step 2 succeeded. It waits for the environment's reviewer.

Because step 1 installs the package with the `tckdb-client` and
`tckdb-schemas` built from the same commit, a requirement window that does not
contain those versions fails the run.

---

## 3. Order

Each package must be on PyPI before anything that depends on it is uploaded:

1. `tckdb-schemas`
2. `tckdb-client`
3. `tckdb-mcp` depends on neither and may go at any point.

The adapters in tckdb-adapters (`tckdb-adapters-core`, `tckdb-arc`,
`tckdb-chemkin`, `tckdb-qcschema`) depend on `tckdb-client` and
`tckdb-schemas`, so they are released after step 2.

Wait until a release is visible on https://pypi.org/project/<name>/ before
pushing the next tag.

---

## 4. Dry run on TestPyPI

Actions → "Publish Python package" → "Run workflow", choose the branch (normally
`main`) and the package. The run builds, checks and smoke-tests the package at
that ref and uploads it to TestPyPI only; it never publishes to PyPI.
Dry-run from `main`: TestPyPI keeps the first upload of a version, so a dry
run from another ref at an unchanged version turns the later tag's TestPyPI
upload into a no-op (`skip-existing`), and TestPyPI then holds the other ref's
build.

Then install it in a clean environment. TestPyPI does not hold the third-party
dependencies, so they come from PyPI:

```bash
python -m venv /tmp/tckdb-dry && . /tmp/tckdb-dry/bin/activate
python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  tckdb-client==<version>
python -c "import tckdb_client; print(tckdb_client.__version__)"
```

For `tckdb-client`, upload `tckdb-schemas` to TestPyPI first: the client's
schemas requirement has to resolve from TestPyPI too.

---

## 5. Releasing

```bash
git fetch origin
git tag tckdb-schemas-v<version> origin/main
git push origin tckdb-schemas-v<version>
```

Approve the `pypi` deployment when GitHub asks, wait for the release to appear
on PyPI, then tag the next package in the order above.

---

## 6. Mistakes

- **A bad release.** A version on PyPI can never be uploaded again, even after
  deleting it. Yank it on PyPI (project → Manage → the release → Options →
  Yank), fix the problem, bump the version, and release the new number. A
  yanked release stays installable by an exact pin and is skipped by
  everything else.
- **A tag on the wrong commit or with the wrong version.** The run fails before
  anything is uploaded. Delete the tag (`git push origin :refs/tags/<tag>`),
  then tag the right commit.
- **A schemas minor that breaks the client.** A published client keeps its
  `tckdb-schemas` window forever. See "The `tckdb-schemas` window" in
  `clients/python/docs/development.md` before publishing such a schemas
  release.
