"""Deprecated legacy v1/v2 bridge.

This module is intentionally excluded from the package's supported surface.
Use :mod:`mcp_server_unipile.recruiter_client` and the v2-only
``unipile-recruiter`` CLI instead.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .recruiter_client import RecruiterClient


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "unipile-recruiter" / "dual.json"


def default_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "read_preference": "v2",
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
    if config.get("read_preference") not in {"v1", "v2"}:
        raise ValueError("read_preference must be v1 or v2")
    if config.get("write_backend") not in {"disabled", "v1", "v2"}:
        raise ValueError("write_backend must be disabled, v1, or v2")
    return config


@dataclass
class Backend:
    version: str
    client: RecruiterClient
    account_id: Optional[str] = None

    def resolve_account(self) -> str:
        if self.account_id:
            return self.account_id
        self.account_id = self.client.discover_linkedin_account()
        return self.account_id


class DualRecruiterGateway:
    """Retained migration evidence; construction is permanently disabled."""

    def __init__(
        self,
        backends: Mapping[str, Backend],
        config: Mapping[str, Any],
        config_path: Path = DEFAULT_CONFIG_PATH,
    ) -> None:
        raise RuntimeError(
            "The dual V1/V2 gateway is retired; use the V2-only RecruiterClient"
        )
        self.backends = dict(backends)
        self.config = dict(config)
        self.config_path = config_path

    @classmethod
    def from_env(
        cls, config_path: Path = DEFAULT_CONFIG_PATH
    ) -> "DualRecruiterGateway":
        raise RuntimeError(
            "The dual V1/V2 gateway is retired; use the V2-only RecruiterClient"
        )
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
                RecruiterClient(v1_base, v1_key, "v1"),
                os.getenv("UNIPILE_V1_LINKEDIN_ACCOUNT_ID") or accounts.get("v1"),
            )

        v2_key = os.getenv("UNIPILE_V2_API_KEY")
        if v2_key:
            backends["v2"] = Backend(
                "v2",
                RecruiterClient(
                    os.getenv("UNIPILE_V2_BASE_URL", "https://api.unipile.com"),
                    v2_key,
                    "v2",
                ),
                os.getenv("UNIPILE_V2_LINKEDIN_ACCOUNT_ID") or accounts.get("v2"),
            )
        return cls(backends, config, config_path)

    def status(self, live: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "config_path": str(self.config_path),
            "read_preference": self.config["read_preference"],
            "write_backend": self.config["write_backend"],
            "one_writer_enforced": True,
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
                            "account_resolved": bool(account_id),
                            "recruiter_projects_accessible": True,
                            "project_count": projects.get("total_count")
                            or (projects.get("paging") or {}).get("total"),
                        }
                    )
                except Exception as error:
                    state.update(
                        {
                            "reachable": False,
                            "error": type(error).__name__,
                        }
                    )
            result["backends"][version] = state
        writer = self.config["write_backend"]
        result["writes_ready"] = writer in self.backends
        if writer == "disabled":
            result["writes_blocked_reason"] = "write_backend_is_disabled"
        elif writer not in self.backends:
            result["writes_blocked_reason"] = f"{writer}_credentials_not_configured"
        return result

    def read_backend(self, preferred: Optional[str] = None) -> Backend:
        order = [preferred or self.config["read_preference"]]
        order.append("v1" if order[0] == "v2" else "v2")
        for version in order:
            if version in self.backends:
                return self.backends[version]
        raise ValueError("No Unipile backend is configured")

    def write_backend(self) -> Backend:
        version = self.config["write_backend"]
        if version == "disabled":
            raise ValueError("Writes are disabled; set write_backend to v1 or v2 after validation")
        backend = self.backends.get(version)
        if not backend:
            raise ValueError(f"Configured writer {version} has no credentials")
        return backend

    def open_to_work(
        self, identifier: str, preferred: Optional[str] = None
    ) -> dict[str, Any]:
        """Route a Recruiter Open-to-Work read through the preferred backend."""
        backend = self.read_backend(preferred)
        result = backend.client.open_to_work(backend.resolve_account(), identifier)
        return {"reader": backend.version, **result}

    @staticmethod
    def _project_items(data: Mapping[str, Any]) -> list[dict[str, Any]]:
        return list(data.get("items") or data.get("data") or [])

    def all_projects(self, version: str) -> list[dict[str, Any]]:
        backend = self.backends.get(version)
        if not backend:
            raise ValueError(f"{version} backend is not configured")
        account_id = backend.resolve_account()
        items: list[dict[str, Any]] = []
        if version == "v1":
            cursor: Optional[str] = None
            while True:
                page = backend.client.list_projects(account_id, limit=100, cursor=cursor)
                items.extend(self._project_items(page))
                cursor = page.get("cursor")
                if not cursor:
                    break
        else:
            offset = 0
            while True:
                page = backend.client.list_projects(account_id, limit=100, offset=offset)
                batch = self._project_items(page)
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
        by_v1: dict[str, list[dict[str, Any]]] = {}
        by_v2: dict[str, list[dict[str, Any]]] = {}
        for item in v1:
            by_v1.setdefault(self._normalize_name(str(item.get("name", ""))), []).append(item)
        for item in v2:
            by_v2.setdefault(self._normalize_name(str(item.get("name", ""))), []).append(item)
        names = set(by_v1) | set(by_v2)
        matched = []
        only_v1 = []
        only_v2 = []
        ambiguous = []
        for name in sorted(names):
            left, right = by_v1.get(name, []), by_v2.get(name, [])
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

    def project_binding(self, project_key: str) -> dict[str, Any]:
        projects = self.config.get("projects") or {}
        binding = projects.get(project_key)
        if not isinstance(binding, dict):
            raise ValueError(f"Unknown logical project key: {project_key}")
        return binding

    def resolve_bound_project(self, version: str, project_key: str) -> tuple[str, dict[str, Any]]:
        binding = self.project_binding(project_key)
        ids = binding.get("ids") or {}
        project_id = ids.get(version)
        if not project_id:
            raise ValueError(f"Project {project_key} has no {version} ID mapping")
        backend = self.backends.get(version)
        if not backend:
            raise ValueError(f"{version} backend is not configured")
        project = backend.client.get_project(backend.resolve_account(), str(project_id))
        return str(project_id), project

    def save_plan(
        self, identifier: str, project_key: str, stage_key: str
    ) -> dict[str, Any]:
        backend = self.write_backend()
        version = backend.version
        binding = self.project_binding(project_key)
        project_id, project = self.resolve_bound_project(version, project_key)
        stages = binding.get("stages") or {}
        stage_binding = stages.get(stage_key) or {}
        stage = stage_binding.get(version)
        if not stage:
            raise ValueError(
                f"Project {project_key} stage {stage_key} has no {version} mapping"
            )
        profile, profile_calls = backend.client.resolve_recruiter_profile(
            backend.resolve_account(), identifier
        )
        candidate_id = (
            profile.get("candidate_id")
            or profile.get("provider_id")
            or profile.get("id")
        )
        if not candidate_id:
            raise ValueError("Recruiter profile did not return a candidate/profile ID")
        return {
            "writer": version,
            "candidate_id": str(candidate_id),
            "identity": {
                "first_name": profile.get("first_name"),
                "last_name": profile.get("last_name"),
                "public_identifier": profile.get("public_identifier"),
            },
            "project_key": project_key,
            "project_id": project_id,
            "project_name": project.get("name"),
            "stage_key": stage_key,
            "stage": str(stage),
            "profile_calls": profile_calls,
        }

    def execute_save(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        backend = self.write_backend()
        if backend.version != plan.get("writer"):
            raise ValueError("Write backend changed after plan generation")
        result = backend.client.save_candidate(
            backend.resolve_account(),
            str(plan["project_id"]),
            str(plan["candidate_id"]),
            str(plan["stage"]),
        )
        return {
            "success": True,
            "writer": backend.version,
            "result": result,
        }
