# tckdb-client

Python client for the [TCKDB](https://github.com/TCKDB/TCKDB) thermochemistry and kinetics database.

- A small synchronous HTTP client (`TCKDBClient`) for reading from and uploading to a TCKDB instance.
- A builder layer (`tckdb_client.builders`, public beta) that turns scientific Python objects
  (species, calculations, thermo, kinetics) into validated upload payloads.
- No chemistry dependencies. Only `httpx` and `tckdb-schemas`, the wire-contract package.

## Install

```bash
pip install tckdb-client
```

That also installs `tckdb-schemas`, the wire-contract package. For the unreleased `main`, install the two from Git, schemas first:

```bash
pip install "git+https://github.com/TCKDB/TCKDB.git#subdirectory=schemas/python/tckdb-schemas"
pip install "git+https://github.com/TCKDB/TCKDB.git#subdirectory=clients/python"
```

Python 3.11 or newer. Editable install from a clone: `pip install -e ./schemas/python/tckdb-schemas -e ./clients/python`.

## Point it at an instance

```bash
export TCKDB_BASE_URL="http://localhost:8010/api/v1"
export TCKDB_API_KEY="tck_replace_me"   # not needed for public reads
```

Keys are minted on the instance you target; they do not carry over to another one. Reads are anonymous-friendly,
uploads need a key. Details: [`docs/configuration.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/docs/configuration.md).

## Read from Python

```python
import os
from tckdb_client import TCKDBClient

with TCKDBClient(os.environ["TCKDB_BASE_URL"]) as client:
    print(client.health())
    print(client.search_species(smiles="O"))
```

Thermo, kinetics, geometry and calculation reads, filters and result profiles:
[`docs/reads.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/docs/reads.md).

## Upload with the builders

```python
import os
from tckdb_client import TCKDBClient
from tckdb_client.builders import (
    Calculation, ComputedSpeciesUpload, Geometry, LevelOfTheory, SoftwareRelease, Species,
)

xyz = """3
water
O  0.000000  0.000000  0.117790
H  0.000000  0.755450 -0.471160
H  0.000000 -0.755450 -0.471160
"""

software = SoftwareRelease(software="Gaussian", version="16", revision="C.01")
level = LevelOfTheory(method="B3LYP", basis="6-31G(d)")
opt = Calculation.opt(software, level, output_geometry=Geometry.from_xyz(xyz),
                      final_energy_hartree=-76.4, converged=True)
freq = Calculation.freq(software, level, input_geometry=Geometry.from_xyz(xyz),
                        n_imag=0, zpe_hartree=0.0214, depends_on=opt)

upload = ComputedSpeciesUpload(
    species=Species(smiles="O", charge=0, multiplicity=1),
    calculations=[opt, freq],
)

for diagnostic in upload.emission_diagnostics():   # what will and will not reach the wire
    print(diagnostic.level, diagnostic.code, diagnostic.message)

with TCKDBClient(os.environ["TCKDB_BASE_URL"], api_key=os.environ["TCKDB_API_KEY"]) as client:
    print(client.upload(upload))
```

The builders cover computed-species and computed-reaction uploads (thermo, statmech, kinetics, artifacts).
Everything else about them is in [`docs/builders.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/docs/builders.md). Raw-payload uploads, idempotency keys and
contribution bundles: [`docs/uploads_and_idempotency.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/docs/uploads_and_idempotency.md). Error and refusal
codes: [`docs/errors.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/docs/errors.md).

## Adapters

Producer adapters read a tool's own output and build the payloads, so a run reaches TCKDB without hand-written
mapping code. They sit above this client and are not part of it.

- **ARC**: the [`tckdb-adapters`](https://github.com/calvinp0/tckdb-adapters) repository holds `tckdb_arc`,
  which reads ARC's `output.yml` and uploads through this client (`tckdb-arc-upload`).
- **Chemkin** and **QCSchema**: [`adapters/`](https://github.com/TCKDB/TCKDB/tree/main/clients/python/adapters/) in this repository.
- Writing your own: [`docs/adapter_authoring_quickstart.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/docs/adapter_authoring_quickstart.md).

## More

- [`examples/`](https://github.com/TCKDB/TCKDB/tree/main/clients/python/examples/): runnable scripts and notebooks (offline by default; they upload only when
  `TCKDB_BASE_URL` and `TCKDB_API_KEY` are set).
- [`docs/`](https://github.com/TCKDB/TCKDB/tree/main/clients/python/docs/): configuration, reads, uploads, errors, builders, design notes and the API stability policy
  ([`docs/builder_api_stability.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/docs/builder_api_stability.md)).
- [`CHANGELOG.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/CHANGELOG.md), and [`docs/development.md`](https://github.com/TCKDB/TCKDB/blob/main/clients/python/docs/development.md) for tests.
