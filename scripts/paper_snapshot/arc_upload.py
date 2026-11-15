"""Run the ARC adapter's upload sweep on one ARC run folder, against the snapshot instance.

    <adapter python> -P arc_upload.py RUN_DIR CONFIG_FILE PAYLOAD_DIR BASE_URL TIMEOUT_SECONDS

Started by ``source_arc.py`` in a Python (3.11 or later; ``-P`` keeps this directory off ``sys.path``) that imports
``tckdb_arc`` and ARC. ARC's parsers build the Hessian, IRC and
GSM parts of a payload; without them the adapter leaves those parts out, so the runner exits with status 3 before
uploading anything when ARC cannot be imported.

The ``tckdb`` block of ``RUN_DIR/CONFIG_FILE`` is reused with five values replaced: ``base_url`` is ``BASE_URL``,
``payload_dir`` is ``PAYLOAD_DIR``, ``timeout_seconds`` is ``TIMEOUT_SECONDS``, ``upload`` is true, and the API key is
read from the environment variable named by :data:`KEY_ENV`. Every key setting of the block (``api_key_file``,
``api_key_env_file``, an inline key) is dropped. Nothing is written under ``RUN_DIR``.
"""

from __future__ import annotations

import importlib
import os
import sys

KEY_ENV = "TCKDB_SNAPSHOT_API_KEY"


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
    document = {**document, "tckdb": block}
    config = TCKDBConfig.from_dict(block)
    adapter = TCKDBAdapter(config, project_directory=run_dir, input_dict=document)
    run_upload_sweep(adapter=adapter, project_directory=run_dir, tckdb_config=config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
