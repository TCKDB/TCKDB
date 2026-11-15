"""Run the ARC adapter's upload sweep on one ARC run folder, against the snapshot instance.

    <adapter python> -P arc_upload.py RUN_DIR CONFIG_FILE PAYLOAD_DIR BASE_URL TIMEOUT_SECONDS

Started by ``source_arc.py`` in a Python (3.11 or later; ``-P`` keeps this directory off ``sys.path``) that imports
``tckdb_arc`` and ARC. ARC's parsers build the Hessian, IRC and
GSM parts of a payload; without them the adapter leaves those parts out, so the runner exits with status 3 before
uploading anything when ARC cannot be imported.

The ``tckdb`` block of ``RUN_DIR/CONFIG_FILE`` is reused with five values replaced: ``base_url`` is ``BASE_URL``,
``payload_dir`` is ``PAYLOAD_DIR``, ``timeout_seconds`` is ``TIMEOUT_SECONDS``, ``upload`` is true, and the API key is
read from the environment variable named by :data:`KEY_ENV`. Every key setting of the block (``api_key_file``,
``api_key_env_file``, an inline key) is dropped. When :data:`RIGHTS_ENV` holds a JSON object it becomes the block's
``rights``, sent as every upload's ``rights`` fragment; an adapter that does not carry it is refused (status 4). Nothing
is written under ``RUN_DIR``.
"""

from __future__ import annotations

import importlib
import json
import os
import sys

KEY_ENV = "TCKDB_SNAPSHOT_API_KEY"
RIGHTS_ENV = "TCKDB_SNAPSHOT_RIGHTS"


def main(argv: list[str]) -> int:
    run_dir, config_name, payload_dir, base_url, timeout = argv
    try:
        importlib.import_module("arc.parser.parser")
    except Exception as exc:
        print(f"ARC is not importable ({type(exc).__name__}: {exc}); nothing was uploaded", file=sys.stderr)
        return 3
    from tckdb_arc._vendor import read_yaml_file
    from tckdb_arc.adapter import TCKDBAdapter
    from tckdb_arc.config import TCKDBConfig
    from tckdb_arc.sweep import run_upload_sweep

    document = read_yaml_file(path=os.path.join(run_dir, config_name))
    block = {k: v for k, v in dict(document["tckdb"]).items() if "key" not in k.lower()}
    block.update(
        base_url=base_url, upload=True, payload_dir=payload_dir, api_key_env=KEY_ENV, timeout_seconds=float(timeout)
    )
    rights = json.loads(os.environ[RIGHTS_ENV]) if os.environ.get(RIGHTS_ENV) else None
    if rights is not None:
        block["rights"] = rights
    document = {**document, "tckdb": block}
    config = TCKDBConfig.from_dict(block)
    if rights is not None and getattr(config, "rights", None) != rights:
        print("this tckdb_arc does not send a rights fragment (needs 0.15.6 or later); nothing was uploaded", file=sys.stderr)
        return 4
    adapter = TCKDBAdapter(config, project_directory=run_dir, input_dict=document)
    run_upload_sweep(adapter=adapter, project_directory=run_dir, tckdb_config=config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
