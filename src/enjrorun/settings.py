"""Server configuration stored in config.json, plus API key helpers.

Fields in FILE_ONLY_FIELDS can name executables or filesystem locations, so they
are never writable through the HTTP API (only by editing the file or via the CLI).
"""

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields

from enjrorun import paths

FILE_ONLY_FIELDS = frozenset({"host", "public_hosts", "binary_path", "models_dirs", "extra_scan_dirs"})
# Managed through dedicated endpoints, not the generic settings update.
_PRIVATE_FIELDS = frozenset({"api_key_hash"})

KEY_PREFIX = "sk-enjro-"


class SettingsError(ValueError):
    pass


def _default_models_dirs() -> list[str]:
    return [str(paths.default_models_dir())]


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8790
    api_key_hash: str | None = None
    trust_loopback: bool = True
    # HTTPS hostnames a reverse proxy or tunnel serves Enjro Run at (e.g. a Cloudflare
    # Tunnel). Requests naming one never get loopback trust, so they always need the key.
    public_hosts: list[str] = field(default_factory=list)
    models_dirs: list[str] = field(default_factory=_default_models_dirs)
    extra_scan_dirs: list[str] = field(default_factory=list)
    binary_tag: str | None = None
    binary_variant: str | None = None
    binary_path: str | None = None
    log_body_ring_size: int = 50
    log_retention_days: int = 30
    restore_loaded: bool = True
    upstream_idle_timeout_s: int = 120

    def validate(self) -> None:
        if not is_loopback_host(self.host) and not self.api_key_hash:
            raise SettingsError(
                f"host {self.host!r} exposes Enjro Run beyond this machine but no API key is set. "
                "Run `enjrorun key set` first, or use host 127.0.0.1."
            )
        if self.public_hosts and not self.api_key_hash:
            raise SettingsError(
                "public_hosts exposes Enjro Run to the internet but no API key is set. "
                "Run `enjrorun key set` first."
            )
        if not 1 <= self.port <= 65535:
            raise SettingsError(f"port {self.port} is out of range")
        if self.log_body_ring_size < 0 or self.log_retention_days < 1:
            raise SettingsError("log_body_ring_size must be >= 0 and log_retention_days >= 1")
        if self.upstream_idle_timeout_s < 1:
            raise SettingsError("upstream_idle_timeout_s must be >= 1")

    def public_dict(self) -> dict:
        d = asdict(self)
        d.pop("api_key_hash")
        d["api_key_set"] = self.api_key_hash is not None
        d["file_only"] = sorted(FILE_ONLY_FIELDS)
        return d


def normalize_public_host(value: str) -> str:
    """Reduce 'https://Enjro.Example.com/' to 'enjro.example.com'."""
    value = value.strip().lower()
    value = value.split("://", 1)[-1]
    return value.split("/", 1)[0]


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SettingsStore:
    """Loads and atomically saves Settings; notifies listeners on change."""

    def __init__(self) -> None:
        self._listeners: list[Callable[[Settings], None]] = []
        self.current: Settings | None = None

    def on_change(self, fn: Callable[[Settings], None]) -> None:
        self._listeners.append(fn)

    def load(self) -> Settings:
        known = {f.name for f in fields(Settings)}
        path = paths.config_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        self.current = Settings(**{k: v for k, v in data.items() if k in known})
        return self.current

    def save(self, s: Settings) -> None:
        path = paths.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(s), f, indent=2)
        os.replace(tmp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
        self.current = s
        for fn in self._listeners:
            fn(s)

    def _get(self) -> Settings:
        return self.current or self.load()

    def update_from_api(self, patch: dict) -> Settings:
        known = {f.name for f in fields(Settings)}
        for key in patch:
            if key in FILE_ONLY_FIELDS:
                raise SettingsError(f"{key} is file-only; change it with `enjrorun config set`")
            if key not in known or key in _PRIVATE_FIELDS:
                raise SettingsError(f"unknown setting {key!r}")
        s = Settings(**{**asdict(self._get()), **patch})
        s.validate()
        self.save(s)
        return s

    def set_api_key_hash(self, key_hash: str | None) -> Settings:
        s = Settings(**{**asdict(self._get()), "api_key_hash": key_hash})
        s.validate()
        self.save(s)
        return s


def generate_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + key).encode()).hexdigest()
    return f"{salt}${digest}"


def verify_api_key(key: str, key_hash: str | None) -> bool:
    if not key_hash or "$" not in key_hash:
        return False
    salt, digest = key_hash.split("$", 1)
    candidate = hashlib.sha256((salt + key).encode()).hexdigest()
    return hmac.compare_digest(candidate, digest)
