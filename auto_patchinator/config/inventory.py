"""Team-maintained node inventory - hostname -> role/site/automation exceptions.

This is intentionally separate from the wave Excel file: role and connection details
don't change month to month, while the Excel only tells us which hosts are in scope
for a given wave.

A single hosts.yaml contains both prod and test nodes, each tagged with
`environment: prod` (default) or `environment: test`.  Pass --environment test
at the CLI to filter to test nodes only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from auto_patchinator.actions.sequences import MANUAL_ONLY_IDENTITIES, NodeRole
from auto_patchinator.actions.types import Identity

_DEFAULT_PAS_SPLUNK_SUFFIX = "pas.prd.spk"
_DEFAULT_PAS_ROOT_SUFFIX   = "pas.prd.spk.root"

_UNSET = object()  # sentinel to distinguish "not in YAML" from explicit None/""


@dataclass(frozen=True)
class Host:
    hostname: str
    role: NodeRole
    site: str
    environment: str = "prod"            # "prod" or "test"
    manual_identities: tuple[Identity, ...] = ()
    pas_port: int | None = None          # encoded as #PORT in PAS login username; overrides global
    pas_domain_suffix: str | None = None # overrides global when set (even "" to force no suffix)
    _pas_domain_suffix_set: bool = False # True when per-host value was explicitly given in YAML
    splunk_su_command: str | None = None # overrides the default su command for the splunk identity

    def is_manual_only(self, identity: Identity) -> bool:
        return identity in self.manual_identities

    def effective_pas_domain_suffix(self, global_suffix: str | None) -> str | None:
        return self.pas_domain_suffix if self._pas_domain_suffix_set else global_suffix

    def effective_pas_port(self, global_port: int | None) -> int | None:
        return self.pas_port if self.pas_port is not None else global_port


@dataclass(frozen=True)
class Inventory:
    hosts: dict[str, Host]           # already filtered to the active environment
    environment: str = "prod"        # the active environment ("prod" or "test")
    stretched_sh_sites: tuple[str, str] | None = None
    pas_domain_suffix: str | None = None
    pas_port: int | None = None
    pas_splunk_suffix: str = _DEFAULT_PAS_SPLUNK_SUFFIX
    pas_root_suffix: str = _DEFAULT_PAS_ROOT_SUFFIX
    pas_gateway: str | None = None   # "host" or "host:port"; --pas-gateway overrides

    @property
    def pas_suffixes(self) -> dict[Identity, str]:
        return {Identity.SPLUNK: self.pas_splunk_suffix, Identity.ROOT: self.pas_root_suffix}

    def get(self, hostname: str) -> Host:
        try:
            return self.hosts[hostname]
        except KeyError:
            raise KeyError(
                f"host {hostname!r} not found in inventory - add it before running a plan that targets it"
            ) from None

    def other_site(self, site: str) -> str:
        if not self.stretched_sh_sites or site not in self.stretched_sh_sites:
            raise ValueError(
                f"{site!r} is not one of the configured stretched_sh_sites {self.stretched_sh_sites!r}"
            )
        a, b = self.stretched_sh_sites
        return b if site == a else a

    def stretched_sh_hostnames(self, site: str | None = None) -> list[str]:
        """All search_head_stretched hostnames, optionally filtered to one site, sorted
        for a deterministic pick (e.g. the lowest-numbered host as a concrete example
        in manual captain-transfer instructions)."""
        return sorted(
            h.hostname for h in self.hosts.values()
            if h.role == NodeRole.SEARCH_HEAD_STRETCHED and (site is None or h.site == site)
        )

    def captain_candidate(self, site: str) -> str | None:
        """A concrete, deterministic example host to suggest as the temporary captain
        on the given site (the lowest-numbered stretched SH there), or None if the
        inventory has no stretched SH host on that site."""
        hosts = self.stretched_sh_hostnames(site)
        return hosts[0] if hosts else None


def load_inventory(path: str | Path, environment: str = "prod") -> Inventory:
    raw = yaml.safe_load(Path(path).read_text())
    if not raw or "hosts" not in raw:
        raise ValueError(f"{path}: inventory file must define a top-level 'hosts' mapping")

    hosts: dict[str, Host] = {}
    for hostname, fields in raw["hosts"].items():
        host_env = fields.get("environment", "prod")
        if host_env != environment:
            continue
        role = NodeRole(fields["role"])
        site = fields["site"]
        manual = set(Identity(i) for i in fields.get("manual_identities", []))
        manual |= set(MANUAL_ONLY_IDENTITIES.get(hostname, ()))
        pas_port = fields.get("pas_port")
        per_host_suffix = fields.get("pas_domain_suffix", _UNSET)
        suffix_set = per_host_suffix is not _UNSET
        hosts[hostname] = Host(
            hostname=hostname,
            role=role,
            site=site,
            environment=host_env,
            manual_identities=tuple(sorted(manual, key=lambda i: i.value)),
            pas_port=int(pas_port) if pas_port is not None else None,
            pas_domain_suffix=per_host_suffix if suffix_set else None,
            _pas_domain_suffix_set=suffix_set,
            splunk_su_command=fields.get("splunk_su_command"),
        )

    # Per-environment settings override top-level global ones.
    env_cfg: dict = raw.get("environments", {}).get(environment, {})

    stretched_sites = env_cfg.get("stretched_sh_sites") or raw.get("stretched_sh_sites")
    raw_pas_port = env_cfg.get("pas_port") if "pas_port" in env_cfg else raw.get("pas_port")
    return Inventory(
        hosts=hosts,
        environment=environment,
        stretched_sh_sites=tuple(stretched_sites) if stretched_sites else None,
        pas_domain_suffix=env_cfg.get("pas_domain_suffix", raw.get("pas_domain_suffix")),
        pas_port=int(raw_pas_port) if raw_pas_port is not None else None,
        pas_splunk_suffix=env_cfg.get("pas_splunk_suffix", _DEFAULT_PAS_SPLUNK_SUFFIX),
        pas_root_suffix=env_cfg.get("pas_root_suffix", _DEFAULT_PAS_ROOT_SUFFIX),
        pas_gateway=env_cfg.get("pas_gateway", raw.get("pas_gateway")),
    )
