"""``tckdb_storage_hostfw.sh`` closes the object store's internal ports to the host (#548).

SeaweedFS's filer, master and volume ports, and their gRPC ports, take no
credentials. The compose ``storage`` network keeps other containers away from
them (#545); the host's own network namespace can still route to the bridge.
The script inserts one rule at the top of the host's OUTPUT chain rejecting
new TCP from the host to the storage subnet, except to the S3 port. What is
pinned here:

* the rule names the network's subnet, leaves the S3 port (and only it)
  open, carries the tag, and goes to the top of OUTPUT;
* applying is idempotent (one rule however often it runs), and a changed
  subnet replaces the old rule rather than joining it;
* a missing or ambiguous network fails loudly and changes nothing -- the
  last good rule stays;
* an IPv6 subnet gets an ``ip6tables`` rule, and a network without one says
  so;
* ``--check`` changes nothing and fails unless the rule matches the current
  subnet, is alone, and comes first; ``--remove`` deletes every tagged rule
  in both families.

The real iptables is never run. ``docker``, ``iptables`` and ``ip6tables``
are fakes on ``PATH`` that log their calls and keep the OUTPUT chain in a
file, printing it back the way ``iptables -S`` does.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "backend/scripts/ops/tckdb_storage_hostfw.sh"
TAG = "tckdb-548-storage-hostfw"
NET = "tckdbv2_storage"
V4 = "172.20.0.0/16"
V6 = "fd00:548::/64"


def _rule(subnet: str, ports: str = "9000") -> str:
    """The rule as ``iptables -S OUTPUT`` lists it."""
    match = f"-m tcp ! --dport {ports}" if "," not in ports else f"-m multiport ! --dports {ports}"
    return (
        f"-A OUTPUT -d {subnet} -p tcp {match} -m conntrack --ctstate NEW "
        f"-m comment --comment {TAG} -j REJECT --reject-with tcp-reset"
    )


#: Networks are files under $FAKE_NETWORKS: ``<name>`` holds its subnets, one
#: per line; ``<name>.label`` holds the compose network key it was made for.
#: ``FAKE_APPEAR_AFTER=N`` hides every network for the first N inspects, to
#: stand in for Docker still starting at boot.
_FAKE_DOCKER = r"""#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
[[ -n "${FAKE_DOCKER_DOWN:-}" ]] && { echo "Cannot connect to the Docker daemon" >&2; exit 1; }
if [[ "$1 $2" == "network ls" ]]; then
  want="${4#label=com.docker.compose.network=}"
  for f in "$FAKE_NETWORKS"/*.label; do
    [[ -e "$f" ]] || continue
    [[ "$(cat "$f")" == "$want" ]] && basename "$f" .label
  done
  exit 0
fi
if [[ "$1 $2" == "network inspect" ]]; then
  if [[ -n "${FAKE_APPEAR_AFTER:-}" ]]; then
    n=$(( $(cat "$FAKE_NETWORKS/.inspects" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$FAKE_NETWORKS/.inspects"
    (( n > FAKE_APPEAR_AFTER )) || { echo "Error: No such network: $3" >&2; exit 1; }
  fi
  [[ -f "$FAKE_NETWORKS/$3" ]] || { echo "Error: No such network: $3" >&2; exit 1; }
  cat "$FAKE_NETWORKS/$3"
  exit 0
fi
echo "fake docker: unexpected call" >&2
exit 99
"""

#: One script serves as iptables and ip6tables; the chain file is chosen by
#: the name it was called as. Rules are compared as exact strings, which is
#: sound because the script inserts, checks and deletes one canonical form.
_FAKE_IPTABLES = r"""#!/usr/bin/env bash
fam="$(basename "$0")"
chain="$FAKE_CHAINS/$fam"
touch "$chain"
echo "$fam $*" >> "$FAKE_LOG"
[[ "$2" == OUTPUT ]] || { echo "fake $fam: only OUTPUT" >&2; exit 99; }
op="$1"; shift 2
case "$op" in
  -S) echo "-P OUTPUT ACCEPT"; cat "$chain" ;;
  -I) [[ "$1" == 1 ]] || exit 99; shift
      { echo "-A OUTPUT $*"; cat "$chain"; } > "$chain.new"; mv "$chain.new" "$chain" ;;
  -A) echo "-A OUTPUT $*" >> "$chain" ;;
  -C) grep -qxF -- "-A OUTPUT $*" "$chain" || { echo "iptables: Bad rule (does a matching rule exist in that chain?)." >&2; exit 1; } ;;
  -D) line=$(grep -nxF -- "-A OUTPUT $*" "$chain" | head -n1 | cut -d: -f1)
      [[ -n "$line" ]] || { echo "iptables: Bad rule (does a matching rule exist in that chain?)." >&2; exit 1; }
      sed -i "${line}d" "$chain" ;;
  *) echo "fake $fam: unexpected $op" >&2; exit 99 ;;
esac
"""


class Host:
    """A fake host: a Docker with networks, and OUTPUT chains per family."""

    def __init__(self, root: Path, *, ip6tables: bool = True) -> None:
        self.root = root
        self.bin = root / "bin"
        self.networks = root / "networks"
        self.chains = root / "chains"
        self.log = root / "calls.log"
        for d in (self.bin, self.networks, self.chains):
            d.mkdir()
        self._install("docker", _FAKE_DOCKER)
        self._install("iptables", _FAKE_IPTABLES)
        if ip6tables:
            self._install("ip6tables", _FAKE_IPTABLES)
        # Only what the script needs, so a real iptables/ip6tables elsewhere
        # on PATH can never be reached -- not even a missing fake falls
        # through to it.
        for tool in ("sh", "bash", "cat", "grep", "sed", "tr", "head", "cut", "mv", "touch", "sleep", "basename"):
            found = _which(tool)
            target = self.bin / tool
            if not target.exists():
                target.symlink_to(found)

    def _install(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def add_network(self, name: str, *subnets: str, label: str | None = "storage") -> None:
        (self.networks / name).write_text("".join(f"{s}\n" for s in subnets))
        if label is not None:
            (self.networks / f"{name}.label").write_text(label)

    def chain(self, family: str = "iptables") -> list[str]:
        path = self.chains / family
        return path.read_text().splitlines() if path.exists() else []

    def set_chain(self, lines: list[str], family: str = "iptables") -> None:
        (self.chains / family).write_text("".join(f"{line}\n" for line in lines))

    def calls(self, prefix: str = "") -> list[str]:
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [line for line in lines if line.startswith(prefix)]

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        full_env = {
            "PATH": str(self.bin),
            "FAKE_LOG": str(self.log),
            "FAKE_NETWORKS": str(self.networks),
            "FAKE_CHAINS": str(self.chains),
            **env,
        }
        return subprocess.run(
            ["/bin/sh", str(SCRIPT), *args],
            env=full_env,
            capture_output=True,
            text=True,
            timeout=30,
        )


def _which(tool: str) -> str:
    for directory in os.environ.get("PATH", "/usr/bin:/bin").split(os.pathsep):
        candidate = Path(directory) / tool
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    # Not a skip: a skipped test here is silence reading as success.
    pytest.fail(f"{tool} is not installed, so the script cannot be exercised")


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def _mutating(host: Host) -> list[str]:
    return [
        c
        for c in host.calls()
        if c.split()[:2] in (["iptables", "-I"], ["iptables", "-D"], ["ip6tables", "-I"], ["ip6tables", "-D"])
    ]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def test_the_rule_names_the_subnet_leaves_s3_open_and_goes_first(host: Host) -> None:
    host.add_network(NET, V4)
    host.set_chain(["-A OUTPUT -o lo -j ACCEPT"])
    result = host.run("--network", NET)
    assert result.returncode == 0, result.stderr
    assert host.chain() == [_rule(V4), "-A OUTPUT -o lo -j ACCEPT"]
    # Exactly the rule, spelled out, so a reader of the test sees the claim.
    assert host.chain()[0] == (
        "-A OUTPUT -d 172.20.0.0/16 -p tcp -m tcp ! --dport 9000 -m conntrack "
        "--ctstate NEW -m comment --comment tckdb-548-storage-hostfw "
        "-j REJECT --reject-with tcp-reset"
    )
    assert f"host -> {V4} tcp (except 9000) rejected" in result.stdout


def test_rerunning_leaves_exactly_one_rule(host: Host) -> None:
    host.add_network(NET, V4)
    for _ in range(3):
        result = host.run("--network", NET)
        assert result.returncode == 0, result.stderr
    assert host.chain() == [_rule(V4)]
    # The second and third run each deleted the previous rule first.
    assert len(host.calls("iptables -D OUTPUT")) == 2


def test_a_changed_subnet_replaces_the_old_rule(host: Host) -> None:
    host.add_network(NET, V4)
    assert host.run("--network", NET).returncode == 0
    host.add_network(NET, "172.31.0.0/16")  # the network was recreated
    result = host.run("--network", NET)
    assert result.returncode == 0, result.stderr
    assert host.chain() == [_rule("172.31.0.0/16")]


def test_only_tagged_rules_are_touched(host: Host) -> None:
    host.add_network(NET, V4)
    others = [
        "-A OUTPUT -d 172.20.0.0/16 -p tcp -m tcp --dport 22 -j ACCEPT",
        f"-A OUTPUT -m comment --comment {TAG}-something-else -j ACCEPT",
    ]
    host.set_chain(others)
    assert host.run("--network", NET).returncode == 0
    assert host.run("--network", NET).returncode == 0
    assert host.chain() == [_rule(V4), *others]


def test_the_allowed_port_is_configurable(host: Host) -> None:
    host.add_network(NET, V4)
    assert host.run("--network", NET, "--allow-ports", "9100").returncode == 0
    assert host.chain() == [_rule(V4, "9100")]
    result = host.run("--network", NET, TCKDB_STORAGE_ALLOW_PORTS="9000,9333,9340")
    assert result.returncode == 0, result.stderr
    assert host.chain() == [_rule(V4, "9000,9333,9340")]


@pytest.mark.parametrize("ports", ["", "9000,", "abc", "0", "70000", "9000,,9333"])
def test_a_bad_port_list_changes_nothing(host: Host, ports: str) -> None:
    host.add_network(NET, V4)
    result = host.run("--network", NET, "--allow-ports", ports)
    assert result.returncode != 0
    assert "port" in result.stderr
    assert not _mutating(host)


# ---------------------------------------------------------------------------
# Which network
# ---------------------------------------------------------------------------


def test_the_network_comes_from_the_environment(host: Host) -> None:
    host.add_network("labnet_storage", V4, label=None)
    result = host.run(TCKDB_STORAGE_NETWORK="labnet_storage")
    assert result.returncode == 0, result.stderr
    assert host.chain() == [_rule(V4)]


def test_the_compose_default_is_project_storage(host: Host) -> None:
    host.add_network("myproj_storage", V4, label=None)
    result = host.run(COMPOSE_PROJECT_NAME="myproj")
    assert result.returncode == 0, result.stderr
    assert "docker network inspect myproj_storage" in "\n".join(host.calls())
    assert host.chain() == [_rule(V4)]


def test_the_one_compose_storage_network_is_found_by_label(host: Host) -> None:
    host.add_network(NET, V4)
    host.add_network("tckdbv2_default", "172.19.0.0/16", label="default")
    result = host.run()
    assert result.returncode == 0, result.stderr
    assert host.chain() == [_rule(V4)]


def test_two_compose_storage_networks_are_refused_not_guessed(host: Host) -> None:
    host.add_network(NET, V4)
    host.add_network("scratch_storage", "172.30.0.0/16")
    host.set_chain([_rule(V4)])
    result = host.run("--wait", "5")
    assert result.returncode != 0
    assert "refusing to guess" in result.stderr
    assert NET in result.stderr and "scratch_storage" in result.stderr
    assert not _mutating(host)
    assert host.chain() == [_rule(V4)]
    # Ambiguity does not resolve itself, so it is not waited on.
    assert len(host.calls("docker network ls")) == 1


def test_a_missing_network_fails_loudly_and_keeps_the_last_rule(host: Host) -> None:
    host.set_chain([_rule(V4)])
    result = host.run("--network", NET)
    assert result.returncode != 0
    assert f"network {NET} not found" in result.stderr
    assert "nothing was changed" in result.stderr
    assert not _mutating(host)
    assert host.chain() == [_rule(V4)]


def test_no_compose_storage_network_fails_loudly(host: Host) -> None:
    result = host.run()
    assert result.returncode != 0
    assert "no Compose network labelled 'storage'" in result.stderr
    assert not _mutating(host)


def test_docker_down_fails_loudly(host: Host) -> None:
    host.add_network(NET, V4)
    result = host.run("--network", NET, FAKE_DOCKER_DOWN="1")
    assert result.returncode != 0
    assert "not found" in result.stderr
    assert not _mutating(host)


def test_wait_covers_a_network_that_appears_during_boot(host: Host) -> None:
    host.add_network(NET, V4)
    result = host.run("--network", NET, "--wait", "10", FAKE_APPEAR_AFTER="2")
    assert result.returncode == 0, result.stderr
    assert host.chain() == [_rule(V4)]
    assert len(host.calls("docker network inspect")) == 3


def test_wait_gives_up(host: Host) -> None:
    result = host.run("--network", NET, "--wait", "1")
    assert result.returncode != 0
    assert len(host.calls("docker network inspect")) == 2
    assert not _mutating(host)


# ---------------------------------------------------------------------------
# IPv6
# ---------------------------------------------------------------------------


def test_an_ipv6_subnet_gets_an_ip6tables_rule(host: Host) -> None:
    host.add_network(NET, V4, V6)
    result = host.run("--network", NET)
    assert result.returncode == 0, result.stderr
    assert host.chain("iptables") == [_rule(V4)]
    assert host.chain("ip6tables") == [_rule(V6)]
    # And re-running still leaves one of each.
    assert host.run("--network", NET).returncode == 0
    assert host.chain("ip6tables") == [_rule(V6)]


def test_no_ipv6_subnet_is_said_and_clears_an_old_ipv6_rule(host: Host) -> None:
    host.add_network(NET, V4)
    host.set_chain([_rule(V6)], family="ip6tables")  # the network used to have IPv6
    result = host.run("--network", NET)
    assert result.returncode == 0, result.stderr
    assert "no IPv6 subnet; no ip6tables rule" in result.stdout
    assert host.chain("ip6tables") == []


def test_an_ipv6_subnet_without_ip6tables_changes_nothing(tmp_path: Path) -> None:
    host = Host(tmp_path, ip6tables=False)
    host.add_network(NET, V4, V6)
    result = host.run("--network", NET)
    assert result.returncode != 0
    assert "ip6tables is not installed" in result.stderr
    assert not _mutating(host)


def test_no_ipv6_and_no_ip6tables_is_fine(tmp_path: Path) -> None:
    host = Host(tmp_path, ip6tables=False)
    host.add_network(NET, V4)
    result = host.run("--network", NET)
    assert result.returncode == 0, result.stderr
    assert "ip6tables not installed" in result.stdout
    assert host.chain() == [_rule(V4)]


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


def test_check_passes_when_applied_and_changes_nothing(host: Host) -> None:
    host.add_network(NET, V4, V6)
    assert host.run("--network", NET).returncode == 0
    before = len(_mutating(host))
    result = host.run("--network", NET, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
    assert len(_mutating(host)) == before


def test_check_fails_with_no_rule(host: Host) -> None:
    host.add_network(NET, V4)
    result = host.run("--network", NET, "--check")
    assert result.returncode != 0
    assert "MISSING" in result.stdout
    assert not _mutating(host)
    assert host.chain() == []


def test_check_fails_on_a_stale_subnet(host: Host) -> None:
    host.add_network(NET, "172.31.0.0/16")
    host.set_chain([_rule(V4)])  # made before the network was recreated
    result = host.run("--network", NET, "--check")
    assert result.returncode != 0
    assert "MISSING" in result.stdout and "stale" in result.stdout
    assert not _mutating(host)


def test_check_fails_on_a_duplicate(host: Host) -> None:
    host.add_network(NET, V4)
    host.set_chain([_rule(V4), _rule(V4)])
    result = host.run("--network", NET, "--check")
    assert result.returncode != 0
    assert "stale" in result.stdout


def test_check_fails_when_another_rule_comes_first(host: Host) -> None:
    host.add_network(NET, V4)
    host.set_chain(["-A OUTPUT -j ufw-before-output", _rule(V4)])
    result = host.run("--network", NET, "--check")
    assert result.returncode != 0
    assert "precedes" in result.stdout


def test_check_fails_on_a_missing_ipv6_rule(host: Host) -> None:
    host.add_network(NET, V4, V6)
    host.set_chain([_rule(V4)])
    result = host.run("--network", NET, "--check")
    assert result.returncode != 0
    assert "ip6tables: MISSING" in result.stdout


def test_check_with_a_missing_network_fails(host: Host) -> None:
    result = host.run("--network", NET, "--check")
    assert result.returncode != 0
    assert not _mutating(host)


# ---------------------------------------------------------------------------
# --remove
# ---------------------------------------------------------------------------


def test_remove_deletes_every_tagged_rule_in_both_families_and_nothing_else(host: Host) -> None:
    other = "-A OUTPUT -o lo -j ACCEPT"
    host.set_chain([_rule(V4), other, _rule("172.31.0.0/16")])
    host.set_chain([_rule(V6)], family="ip6tables")
    # No network, no Docker: removing must work even after the stack is gone.
    result = host.run("--remove", FAKE_DOCKER_DOWN="1")
    assert result.returncode == 0, result.stderr
    assert host.chain() == [other]
    assert host.chain("ip6tables") == []
    assert "removed 2 iptables rule(s)" in result.stdout
    assert "removed 1 ip6tables rule(s)" in result.stdout
    assert not host.calls("docker")


def test_remove_with_nothing_to_remove_is_fine(host: Host) -> None:
    result = host.run("--remove")
    assert result.returncode == 0, result.stderr
    assert "removed 0 iptables rule(s)" in result.stdout


def test_check_and_remove_together_are_refused(host: Host) -> None:
    result = host.run("--check", "--remove")
    assert result.returncode != 0
    assert "cannot be combined" in result.stderr


# ---------------------------------------------------------------------------
# The unit
# ---------------------------------------------------------------------------


def test_the_unit_runs_the_script_after_docker_at_boot() -> None:
    unit = (REPO_ROOT / "backend/scripts/ops/tckdb-storage-hostfw.service").read_text()
    lines = {line.strip() for line in unit.splitlines() if line and not line.lstrip().startswith("#")}
    for expected in (
        "Type=oneshot",
        "RemainAfterExit=yes",
        "After=docker.service network-online.target",
        "Requires=docker.service",
        "WantedBy=multi-user.target",
        "ExecStart=/usr/local/sbin/tckdb_storage_hostfw.sh --wait 60",
    ):
        assert expected in lines, expected
