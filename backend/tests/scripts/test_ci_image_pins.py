"""CI pulls its service images from the GHCR mirror, pinned by digest.

``mirror-ci-images.yml`` copies two Docker Hub images into ``ghcr.io/tckdb``
and lists each with its index digest. These tests check that no workflow
pulls those images from Docker Hub any more, that every GHCR reference is
pinned to the digest the mirror lists, and that ``docker-compose.yml`` (which
stays on Docker Hub) pins the same digests.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
MIRROR = WORKFLOW_DIR / "mirror-ci-images.yml"
COMPOSE = REPO_ROOT / "docker-compose.yml"

DIGEST = r"sha256:[0-9a-f]{64}"


def _mirror_images() -> dict[str, tuple[str, str]]:
    """Map image name -> (source repository, digest) from the IMAGES block."""
    images: dict[str, tuple[str, str]] = {}
    for dest, source, digest in re.findall(
        rf"^\s+(ghcr\.io/tckdb/\S+) docker\.io/(\S+) ({DIGEST})\s*$",
        MIRROR.read_text(),
        flags=re.M,
    ):
        name = dest.split("/")[-1].split(":")[0]
        images[name] = (source, digest)
    return images


def _workflow_files() -> list[Path]:
    return sorted(p for p in WORKFLOW_DIR.glob("*.y*ml") if p != MIRROR)


def test_mirror_lists_both_images() -> None:
    images = _mirror_images()
    assert set(images) == {"rdkit-cartridge-debian", "seaweedfs"}
    assert images["rdkit-cartridge-debian"][0] == "informaticsmatters/rdkit-cartridge-debian"
    assert images["seaweedfs"][0] == "chrislusf/seaweedfs"


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_workflow_does_not_pull_mirrored_images_from_docker_hub(path: Path) -> None:
    text = path.read_text()
    for source, _ in _mirror_images().values():
        assert source not in text, f"{path.name} pulls {source} from Docker Hub"


def test_every_ghcr_reference_is_pinned_to_the_mirrored_digest() -> None:
    images = _mirror_images()
    seen = {name: 0 for name in images}
    for path in _workflow_files():
        for name, tail in re.findall(r"ghcr\.io/tckdb/([a-z0-9-]+)(\S*)", path.read_text()):
            if name not in images:
                continue
            seen[name] += 1
            assert tail == f"@{images[name][1]}", f"{path.name}: {name}{tail} is not pinned to the mirrored digest"
    assert all(count > 0 for count in seen.values()), seen


def test_compose_pins_the_same_digests_on_docker_hub() -> None:
    text = COMPOSE.read_text()
    for name, (source, digest) in _mirror_images().items():
        assert re.search(rf"image:\s*{re.escape(source)}:\S+@{digest}\s*$", text, flags=re.M), name
