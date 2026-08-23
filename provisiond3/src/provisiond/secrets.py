"""Secret resolution: env indirection now, Vault (TLS cert auth) when configured."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from provisiond.config import VaultConfig

if TYPE_CHECKING:
    import hvac


class SecretError(Exception):
    pass


class Secrets:
    """Resolves ``{env: NAME}`` / ``{vault: path}`` / literal secret refs.

    The Vault client is created lazily on first ``vault:`` reference, using
    TLS certificate auth (VAULT_CERT / VAULT_KEY env vars, same contract as
    the old daemon). Mount point comes from the env var named by
    ``vault.mount_env``.
    """

    def __init__(self, config: VaultConfig) -> None:
        self._config = config
        self._client: hvac.Client | None = None

    def _vault(self) -> hvac.Client:
        if self._client is None:
            import hvac

            if not self._config.url:
                raise SecretError("vault reference used but no vault.url configured")
            cert = os.environ.get("VAULT_CERT")
            key = os.environ.get("VAULT_KEY")
            client = hvac.Client(url=self._config.url, cert=(cert, key))
            client.auth.cert.login()
            self._client = client
        return self._client

    def _mount(self) -> str:
        mount = os.environ.get(self._config.mount_env)
        if not mount:
            raise SecretError(f"environment variable {self._config.mount_env} not set")
        return mount

    def resolve(self, ref: Any, field: str = "value") -> str:
        if isinstance(ref, str):
            return ref
        if isinstance(ref, dict):
            if "env" in ref:
                value = os.environ.get(ref["env"])
                if value is None:
                    raise SecretError(f"environment variable {ref['env']} not set")
                return value
            if "vault" in ref:
                data = self.read(ref["vault"])
                if field not in data:
                    raise SecretError(f"vault secret {ref['vault']} has no field {field}")
                return str(data[field])
        raise SecretError(f"unsupported secret reference: {ref!r}")

    def read(self, secret: str) -> dict[str, Any]:
        path = f"{self._mount()}/{secret}"
        data = self._vault().read(path)
        if not data or not data.get("data"):
            raise SecretError(f"vault secret {path} not found")
        return dict(data["data"])

    def write(self, secret: str, **kwargs: Any) -> None:
        path = f"{self._mount()}/{secret}"
        self._vault().write(path, **kwargs)
