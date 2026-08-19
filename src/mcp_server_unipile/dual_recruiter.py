"""Explicit Unipile v1/v2 Recruiter migration bridge.

The bridge never falls back between versions. V1 is available only for
read-only historical audits, while V2 remains the sole possible writer.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .recruiter_client import RecruiterClient, V1RecruiterClient


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "unipile-recruiter" / "dual.json"


def default_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "write_backend": "disabled",
        "accounts": {"v1": None, "v2": None},
        "projects": {},
    }


def load_dual_config(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config = default_config()
    if path.exists():
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            raise ValueError("Dual configuration must be a JSON object")
        config.update(loaded)
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported dual configuration schema_version")
    if config.get("write_backend") not in {"disabled", "v2"}:
        raise ValueError("write_backend must be disabled or v2")
    return config


@dataclass
class Backend:
    version: str
    client: RecruiterClient | V1RecruiterClient
    account_id: Optional[str] = None

    def resolve_account(self) -> str:
        if self.account_id:
            return self.account_id
        self.account_id = self.client.discover_linkedin_account()
        return self.account_id


class DualRecruiterGateway:
    """Hold both clients while requiring an explicit version for every read."""

    def __init__(
        self,
        backends: Mapping[str, Backend],
        config: Mapping[str, Any],
        config_path: Path = DEFAULT_CONFIG_PATH,
    ) -> None:
        self.backends = dict(backends)
        self.config = dict(config)
        self.config_path = config_path
        for version, backend in self.backends.items():
            if (
                version not in {"v1", "v2"}
                or backend.version != version
                or backend.client.api_version != version
            ):
                raise ValueError("Backend keys and client versions must match")

    @classmethod
    def from_env(
        cls, config_path: Path = DEFAULT_CONFIG_PATH
    ) -> "DualRecruiterGateway":
        config = load_dual_config(config_path)
        accounts = config.get("accounts") or {}
        backends: dict[str, Backend] = {}
        v1_base = (
            os.getenv("UNIPILE_V1_BASE_URL")
            or os.getenv("UNIPILE_V1_DSN")
            or os.getenv("UNIPILE_BASE_URL")
            or os.getenv("UNIPILE_DSN")
        )
        v1_key = os.getenv("UNIPILE_V1_API_KEY") or os.getenv("UNIPILE_API_KEY")
        if v1_base and v1_key:
            backends["v1"] = Backend(
                "v1",
                V1RecruiterClient(api_key=v1_key, base_url=v1_base),
                os.getenv("UNIPILE_V1_LINKEDIN_ACCOUNT_ID") or accounts.get("v1"),
            )
        v2_key = os.getenv("UNIPILE_V2_API_KEY")
        if v2_key:
            backends["v2"] = Backend(
                "v2",
                RecruiterClient(
                    api_key=v2_key,
                    base_url=os.getenv("UNIPILE_V2_BASE_URL", "https://api.unipile.com"),
                ),
                os.getenv("UNIPILE_V2_LINKEDIN_ACCOUNT_ID") or accounts.get("v2"),
            )
        return cls(backends, config, config_path)

    def backend(self, version: str) -> Backend:
        if version not in {"v1", "v2"}:
            raise ValueError("version must be explicitly set to v1 or v2")
        backend = self.backends.get(version)
        if not backend:
            raise ValueError(f"{version} backend is not configured")
        return backend

    def status(self, live: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "config_path": str(self.config_path),
            "automatic_fallback": False,
            "write_backend": self.config["write_backend"],
            "backends": {},
        }
        for version in ("v1", "v2"):
            backend = self.backends.get(version)
            state: dict[str, Any] = {"configured": backend is not None}
            if backend and live:
                try:
                    account_id = backend.resolve_account()
                    projects = backend.client.list_projects(account_id, limit=1)
                    state.update(
                        {
                            "reachable": True,
                            "account_resolved": True,
                            "recruiter_projects_accessible": True,
                            "project_count": projects.get("total_count")
                            or (projects.get("paging") or {}).get("total"),
                        }
                    )
                except Exception as error:
                    state.update({"reachable": False, "error": type(error).__name__})
            result["backends"][version] = state
        result["writes_ready"] = (
            self.config["write_backend"] == "v2" and "v2" in self.backends
        )
        return result

    @staticmethod
    def _items(data: Mapping[str, Any]) -> list[dict[str, Any]]:
        return list(data.get("items") or data.get("data") or [])

    def all_projects(self, version: str) -> list[dict[str, Any]]:
        backend = self.backend(version)
        account_id = backend.resolve_account()
        items: list[dict[str, Any]] = []
        if version == "v1":
            cursor: Optional[str] = None
            seen: set[str] = set()
            while True:
                page = backend.client.list_projects(
                    account_id, limit=100, cursor=cursor
                )
                items.extend(self._items(page))
                next_cursor = page.get("next_cursor") or page.get("cursor")
                if not next_cursor:
                    break
                cursor = str(next_cursor)
                if cursor in seen:
                    raise ValueError("v1 project pagination repeated a cursor")
                seen.add(cursor)
        else:
            offset = 0
            while True:
                page = backend.client.list_projects(
                    account_id, limit=100, offset=offset
                )
                batch = self._items(page)
                items.extend(batch)
                if len(batch) < 100:
                    break
                offset += len(batch)
        return items

    @staticmethod
    def _normalize_name(value: str) -> str:
        return " ".join(value.casefold().split())

    def compare_projects(self) -> dict[str, Any]:
        v1 = self.all_projects("v1")
        v2 = self.all_projects("v2")
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for version, rows in (("v1", v1), ("v2", v2)):
            for item in rows:
                name = self._normalize_name(str(item.get("name", "")))
                grouped.setdefault(name, {"v1": [], "v2": []})[version].append(item)
        matched: list[dict[str, Any]] = []
        only_v1: list[dict[str, Any]] = []
        only_v2: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []
        for name in sorted(grouped):
            left, right = grouped[name]["v1"], grouped[name]["v2"]
            summary = {
                "name": (left or right)[0].get("name"),
                "v1_ids": [item.get("id") for item in left],
                "v2_ids": [item.get("id") for item in right],
            }
            if len(left) > 1 or len(right) > 1:
                ambiguous.append(summary)
            elif left and right:
                matched.append(summary)
            elif left:
                only_v1.append(summary)
            else:
                only_v2.append(summary)
        return {
            "counts": {
                "v1": len(v1),
                "v2": len(v2),
                "matched": len(matched),
                "only_v1": len(only_v1),
                "only_v2": len(only_v2),
                "ambiguous": len(ambiguous),
            },
            "matched": matched,
            "only_v1": only_v1,
            "only_v2": only_v2,
            "ambiguous": ambiguous,
        }

    def list_applicants(
        self,
        version: str,
        resource_id: str,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        backend = self.backend(version)
        account_id = backend.resolve_account()
        if version == "v1":
            if body:
                raise ValueError("v1 applicant reads do not accept a v2 filter body")
            assert isinstance(backend.client, V1RecruiterClient)
            return backend.client.list_job_applicants(
                account_id, resource_id, cursor=cursor, limit=limit or 250
            )
        assert isinstance(backend.client, RecruiterClient)
        return backend.client.list_project_applicants(
            account_id, resource_id, body, cursor=cursor, limit=limit or 100
        )

    def write_backend(self) -> Backend:
        if self.config.get("write_backend") != "v2":
            raise ValueError("Writes are disabled; v2 must be explicitly configured")
        return self.backend("v2")
