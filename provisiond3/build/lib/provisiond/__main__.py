"""Entry point: python -m provisiond --config /etc/provision/config.yaml"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

import redis

from provisiond import config as config_mod
from provisiond.backends import create_backend
from provisiond.backends.base import HwProvisioner
from provisiond.daemon import HwManagerLoop, VmManagerLoop
from provisiond.secrets import Secrets

log = logging.getLogger("provisiond")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provisiond")
    parser.add_argument("--config", default="/etc/provision/config.yaml")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = config_mod.load(args.config)
    secrets = Secrets(cfg.vault)

    redis_kwargs: dict[str, object] = {
        "host": cfg.redis.host,
        "port": cfg.redis.port,
        "db": cfg.redis.db,
        "ssl": cfg.redis.ssl,
    }
    if cfg.redis.password is not None:
        redis_kwargs["password"] = secrets.resolve(cfg.redis.password, field="password")
    conn = redis.Redis(**redis_kwargs)  # type: ignore[arg-type]

    if not cfg.managers:
        log.error("no managers configured, nothing to do")
        return 1

    for manager in cfg.managers:
        backend = create_backend(manager, secrets)
        loop: VmManagerLoop | HwManagerLoop
        if isinstance(backend, HwProvisioner):
            loop = HwManagerLoop(backend, conn)
        else:
            loop = VmManagerLoop(
                backend, conn, deploy_vlan=manager.deploy_vlan, fqdn=manager.fqdn
            )
        log.info("starting manager loop %s (%s)", manager.name, manager.type)
        loop.start()

    signal.pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())
