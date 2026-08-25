import pathlib

import pytest

from deployd import config

SAMPLE = pathlib.Path(__file__).parent.parent / "config.yaml.sample"


def test_load_sample():
    cfg = config.load(str(SAMPLE))
    assert cfg.redis.host == "deploy.tech.dreamhack.se"
    assert cfg.redis.ssl is True
    assert len(cfg.managers) == 2
    pve = cfg.managers[0]
    assert pve.type == "proxmox"
    assert pve.deploy_vlan == 100
    assert pve.token_secret == {"env": "PVE_TOKEN_SECRET"}
    vmw = cfg.managers[1]
    assert vmw.type == "vmware"
    assert vmw.deploy_vlan == 509


def test_rejects_unknown_manager_type(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("redis: {host: x}\nmanagers:\n  - {name: a, type: xen}\n")
    with pytest.raises(config.ConfigError):
        config.load(str(p))


def test_rejects_incomplete_proxmox(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("redis: {host: x}\nmanagers:\n  - {name: a, type: proxmox}\n")
    with pytest.raises(config.ConfigError):
        config.load(str(p))


def test_rejects_duplicate_names(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "redis: {host: x}\n"
        "managers:\n"
        "  - {name: a, type: proxmox, api_url: u, token_id: t, token_secret: s}\n"
        "  - {name: a, type: proxmox, api_url: u, token_id: t, token_secret: s}\n"
    )
    with pytest.raises(config.ConfigError):
        config.load(str(p))
