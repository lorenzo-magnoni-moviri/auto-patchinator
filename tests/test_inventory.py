from pathlib import Path

import pytest

from auto_patchinator.actions.types import Identity
from auto_patchinator.config.inventory import load_inventory

YAML = """
stretched_sh_sites: [milano, roma]
pas_port: 10100
pas_gateway: pas.sky.local

environments:
  prod:
    pas_splunk_suffix: pas.prd.spk
    pas_root_suffix: pas.prd.spk.root
  test:
    pas_splunk_suffix: pas.tst.spk
    pas_root_suffix: pas.tst.spk.root

hosts:
  prdhost01:
    role: deployer
    site: milano
  prdhost02:
    role: indexer
    site: roma
    pas_domain_suffix: .sky.local
    manual_identities: [splunk]
  tsthost01:
    role: forwarder
    site: milano
    environment: test
    splunk_su_command: "sudo su - splunk"
"""


@pytest.fixture
def hosts_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "hosts.yaml"
    path.write_text(YAML)
    return path


def test_environment_filtering(hosts_yaml: Path):
    prod = load_inventory(hosts_yaml, "prod")
    test = load_inventory(hosts_yaml, "test")
    assert set(prod.hosts) == {"prdhost01", "prdhost02"}
    assert set(test.hosts) == {"tsthost01"}


def test_per_environment_pas_suffixes(hosts_yaml: Path):
    assert load_inventory(hosts_yaml, "prod").pas_suffixes[Identity.SPLUNK] == "pas.prd.spk"
    assert load_inventory(hosts_yaml, "test").pas_suffixes[Identity.SPLUNK] == "pas.tst.spk"


def test_global_settings_and_overrides(hosts_yaml: Path):
    inv = load_inventory(hosts_yaml, "prod")
    assert inv.pas_gateway == "pas.sky.local"
    assert inv.pas_port == 10100
    host2 = inv.get("prdhost02")
    assert host2.effective_pas_domain_suffix(inv.pas_domain_suffix) == ".sky.local"
    assert host2.is_manual_only(Identity.SPLUNK)
    assert not host2.is_manual_only(Identity.ROOT)


def test_unknown_host_raises_actionable_error(hosts_yaml: Path):
    inv = load_inventory(hosts_yaml, "prod")
    with pytest.raises(KeyError, match="not found in inventory"):
        inv.get("nosuchhost")


def test_other_site(hosts_yaml: Path):
    inv = load_inventory(hosts_yaml, "prod")
    assert inv.other_site("milano") == "roma"
    assert inv.other_site("roma") == "milano"
    with pytest.raises(ValueError):
        inv.other_site("paris")
