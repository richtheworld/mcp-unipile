"""Guarded LinkedIn Recruiter operations for the Unipile v1 and v2 APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

import requests


READ_METHODS = {"GET", "HEAD", "OPTIONS"}
DEFAULT_BASE_URL = "https://api.unipile.com"


@dataclass
class UnipileAPIError(RuntimeError):
    """A credential-safe Unipile API failure."""

    status_code: int
    error_type: Optional[str]
    detail: str
    retry_after: Optional[str] = None
    request_id: Optional[str] = None

    def __str__(self) -> str:
        label = self.error_type or "unipile_api_error"
        return f"{self.status_code} {label}: {self.detail}"

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status_code": self.status_code,
            "type": self.error_type,
            "detail": self.detail,
        }
        if self.retry_after:
            result["retry_after"] = self.retry_after
        if self.request_id:
            result["request_id"] = self.request_id
        return result


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError("UNIPILE_V2_BASE_URL is not a valid host")
    return value


class RecruiterClient:
    """Small requests-based client for the Recruiter sourcing surface."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        host = urlparse(self.base_url).hostname or ""
        if host != "api.unipile.com":
            raise ValueError("Unipile v2 requires base URL https://api.unipile.com")
        self.api_version = "v2"
        self.headers = {"X-API-KEY": api_key, "accept": "application/json"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        response = self.session.request(
            method.upper(),
            f"{self.base_url}{path}",
            headers=self.headers,
            params=dict(params or {}),
            json=dict(body) if body is not None else None,
            timeout=self.timeout,
        )
        if not response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            detail = (
                payload.get("detail")
                or payload.get("title")
                or response.reason
                or "Unipile request failed"
            )
            raise UnipileAPIError(
                status_code=response.status_code,
                error_type=payload.get("type"),
                detail=str(detail),
                retry_after=response.headers.get("Retry-After"),
                request_id=payload.get("req_id") or response.headers.get("X-Request-ID"),
            )
        if response.status_code == 204 or not response.content:
            return {"success": True}
        return response.json()

    def get_accounts(self) -> list[dict[str, Any]]:
        data = self._request(
            "GET", "/v2/accounts/", params={"provider": "linkedin", "limit": 100}
        )
        return list(data.get("items") or data.get("data") or [])

    def discover_linkedin_account(self) -> str:
        accounts = [
            a
            for a in self.get_accounts()
            if str(a.get("type") or a.get("provider") or "").lower() == "linkedin"
        ]
        healthy = []
        for account in accounts:
            if str(account.get("status", "")).lower() != "running":
                continue
            product_status = (
                account.get("metadata", {}).get("products_connection_status", {})
                if isinstance(account.get("metadata"), dict)
                else {}
            )
            recruiter_status = str(product_status.get("recruiter") or "").lower()
            if recruiter_status and recruiter_status != "running":
                continue
            healthy.append(account)
        if len(healthy) != 1:
            raise ValueError(
                f"Expected exactly one healthy LinkedIn account; found {len(healthy)}"
            )
        return str(healthy[0]["id"])

    def get_linkedin_contracts(self, account_id: str) -> dict[str, Any]:
        account_id = self._account(account_id)
        return self._request("GET", f"/v2/{account_id}/linkedin/contracts")

    def _account(self, account_id: str) -> str:
        if not account_id.startswith("acc_"):
            raise ValueError("Unipile v2 account IDs must start with acc_")
        return account_id

    def get_profile(
        self, account_id: str, identifier: str, variant: str = "recruiter"
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        variant_name = variant if variant.startswith("linkedin_") else f"linkedin_{variant}"
        return self._request(
            "GET",
            f"/v2/{account_id}/users/{identifier}",
            params={"variant": variant_name},
        )

    def resolve_recruiter_profile(
        self, account_id: str, identifier: str
    ) -> tuple[dict[str, Any], int]:
        """Return a Recruiter profile and the number of provider GETs used."""
        if identifier.startswith(("AEM", "AE")):
            return self.get_profile(account_id, identifier, "recruiter"), 1
        try:
            return self.get_profile(account_id, identifier, "recruiter"), 1
        except UnipileAPIError as error:
            if error.status_code not in {404, 422}:
                raise
        classic = self.get_profile(account_id, identifier, "classic")
        provider_id = classic.get("provider_id") or classic.get("id")
        if not provider_id:
            raise ValueError("Classic profile did not return a provider identifier")
        return self.get_profile(account_id, str(provider_id), "recruiter"), 2

    def open_to_work(self, account_id: str, identifier: str) -> dict[str, Any]:
        profile, calls = self.resolve_recruiter_profile(account_id, identifier)
        return {
            "provider_id": profile.get("provider_id") or profile.get("id"),
            "public_identifier": profile.get("public_identifier"),
            "first_name": profile.get("first_name"),
            "last_name": profile.get("last_name"),
            "is_open_to_work": profile.get("is_open_to_work"),
            "profile_calls": calls,
        }

    def list_projects(
        self,
        account_id: str,
        *,
        limit: int = 100,
        cursor: Optional[str] = None,
        offset: Optional[int] = None,
        keywords: Optional[str] = None,
        status: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if offset is not None:
            params["offset"] = offset
        if keywords:
            params["keywords"] = keywords
        if status:
            params["status"] = status
        return self._request(
            "GET", f"/v2/{account_id}/linkedin/recruiter/projects", params=params
        )

    def get_project(self, account_id: str, project_id: str) -> dict[str, Any]:
        account_id = self._account(account_id)
        return self._request(
            "GET", f"/v2/{account_id}/linkedin/recruiter/projects/{project_id}"
        )

    def create_project(
        self, account_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        return self._request(
            "POST", f"/v2/{account_id}/linkedin/recruiter/projects", body=body
        )

    def edit_project(
        self, account_id: str, project_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        return self._request(
            "PATCH",
            f"/v2/{account_id}/linkedin/recruiter/projects/{project_id}",
            body=body,
        )

    def search_people(
        self,
        account_id: str,
        body: Mapping[str, Any],
        *,
        cursor: Optional[str] = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request(
            "POST",
            f"/v2/{account_id}/linkedin/recruiter/search/people",
            params=params,
            body=body,
        )

    def search_from_url(
        self,
        account_id: str,
        url: str,
        *,
        cursor: Optional[str] = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request(
            "POST",
            f"/v2/{account_id}/linkedin/recruiter/search",
            params=params,
            body={"url": url},
        )

    def list_pipeline(
        self,
        account_id: str,
        project_id: str,
        body: Optional[Mapping[str, Any]] = None,
        *,
        cursor: Optional[str] = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request(
            "POST",
            f"/v2/{account_id}/linkedin/recruiter/projects/{project_id}/pipeline",
            params=params,
            body=body or {},
        )

    def list_project_applicants(
        self,
        account_id: str,
        project_id: str,
        body: Optional[Mapping[str, Any]] = None,
        *,
        cursor: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List a v2 project's applicant talent pool.

        This provider read uses POST. It is intentionally a dedicated method so
        callers do not have to weaken the generic mutation guard.
        """
        if not 1 <= limit <= 100:
            raise ValueError("Unipile v2 applicant limit must be between 1 and 100")
        account_id = self._account(account_id)
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return self._request(
            "POST",
            f"/v2/{account_id}/linkedin/recruiter/projects/{project_id}/talent-pool/applicants",
            params=params,
            body=body or {},
        )

    def save_candidate(
        self,
        account_id: str,
        project_id: str,
        candidate_id: str,
        stage: str,
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        return self._request(
            "POST",
            f"/v2/{account_id}/linkedin/recruiter/projects/{project_id}/pipeline/candidate/save",
            body={"candidate_id": candidate_id, "stage_id": stage},
        )

    def find_candidate_in_pipeline(
        self, account_id: str, project_id: str, candidate_id: str
    ) -> Optional[dict[str, Any]]:
        """Find an exact candidate ID across every page of a v2 project pipeline."""
        profile = self.get_profile(account_id, candidate_id, "recruiter")
        full_name = " ".join(
            part for part in (profile.get("first_name"), profile.get("last_name")) if part
        )
        body = {"keywords": full_name} if full_name else {}
        cursor: Optional[str] = None
        seen_cursors: set[str] = set()
        while True:
            data = self.list_pipeline(
                account_id, project_id, body, cursor=cursor, limit=100
            )
            items = data.get("data") or data.get("items") or []
            for item in items:
                candidate = item.get("profile") or item.get("candidate") or item
                known_ids = {
                    str(value)
                    for value in (
                        candidate.get("id"),
                        candidate.get("candidate_id"),
                        candidate.get("provider_id"),
                    )
                    if value
                }
                if candidate_id in known_ids:
                    return item

            raw_paging = data.get("paging")
            paging: dict[str, Any] = raw_paging if isinstance(raw_paging, dict) else {}
            next_cursor = (
                data.get("cursor")
                or data.get("next_cursor")
                or paging.get("cursor")
                or paging.get("next_cursor")
            )
            if not next_cursor or str(next_cursor) in seen_cursors:
                break
            cursor = str(next_cursor)
            seen_cursors.add(cursor)
        return None

    def list_search_parameters(
        self,
        account_id: str,
        parameter_type: str,
        keywords: Optional[str] = None,
        *,
        source: str = "SEARCH",
        project_id: Optional[str] = None,
        stage_id: Optional[str] = None,
        offset: Optional[int] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Resolve Recruiter filter IDs using the v2 POST-body contract."""
        account_id = self._account(account_id)
        body: dict[str, Any] = {"source": source, "type": parameter_type}
        if keywords:
            body["keywords"] = keywords
        if project_id:
            body["project_id"] = project_id
        if stage_id:
            body["stage_id"] = stage_id
        params: dict[str, Any] = {"limit": limit}
        if offset is not None:
            params["offset"] = offset
        return self._request(
            "POST",
            f"/v2/{account_id}/linkedin/recruiter/search/parameters",
            params=params,
            body=body,
        )

    def get_inmail_credits(self, account_id: str) -> dict[str, Any]:
        account_id = self._account(account_id)
        return self._request("GET", f"/v2/{account_id}/linkedin/inmail-credits")

    def proxy_request(
        self, account_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        account_id = self._account(account_id)
        return self._request("POST", f"/v2/{account_id}/linkedin", body=payload)

    def direct_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("path must be an absolute API path beginning with one slash")
        return self._request(method, path, params=params, body=body)


class V1RecruiterClient:
    """Read-only client for explicit historical Recruiter migration audits.

    V1 is never selected implicitly and this class exposes no mutation method.
    Version-specific account, project, and job IDs must be supplied as V1 IDs.
    """

    api_version = "v1"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float = 60,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        self.headers = {"X-API-KEY": api_key, "accept": "application/json"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if method.upper() not in READ_METHODS:
            raise ValueError("Unipile v1 is read-only; mutation requests are disabled")
        response = self.session.request(
            method.upper(),
            f"{self.base_url}{path}",
            headers=self.headers,
            params=dict(params or {}),
            timeout=self.timeout,
        )
        if not response.ok:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            detail = (
                payload.get("detail")
                or payload.get("title")
                or response.reason
                or "Unipile request failed"
            )
            raise UnipileAPIError(
                status_code=response.status_code,
                error_type=payload.get("type"),
                detail=str(detail),
                retry_after=response.headers.get("Retry-After"),
                request_id=payload.get("req_id") or response.headers.get("X-Request-ID"),
            )
        if response.status_code == 204 or not response.content:
            return {"success": True}
        return response.json()

    @staticmethod
    def _account(account_id: str) -> str:
        if not account_id:
            raise ValueError("Unipile v1 account ID must not be empty")
        return account_id

    def get_accounts(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/v1/accounts", params={"limit": 100})
        if isinstance(data, list):
            return list(data)
        return list(data.get("items") or data.get("data") or [])

    def discover_linkedin_account(self) -> str:
        accounts = [
            account
            for account in self.get_accounts()
            if str(account.get("type") or account.get("provider") or "").lower()
            == "linkedin"
        ]
        if len(accounts) != 1:
            raise ValueError(
                f"Expected exactly one v1 LinkedIn account; found {len(accounts)}"
            )
        account_id = accounts[0].get("id")
        if not account_id:
            raise ValueError("The v1 LinkedIn account has no ID")
        return str(account_id)

    def list_projects(
        self,
        account_id: str,
        *,
        limit: int = 100,
        cursor: Optional[str] = None,
        offset: Optional[int] = None,
        keywords: Optional[str] = None,
        status: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        if offset is not None:
            raise ValueError("Unipile v1 project pagination uses cursor, not offset")
        params: dict[str, Any] = {
            "account_id": self._account(account_id),
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        if keywords:
            params["keywords"] = keywords
        if status:
            params["status"] = ",".join(status)
        return self._request("GET", "/api/v1/linkedin/projects", params=params)

    def get_project(self, account_id: str, project_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/linkedin/projects/{project_id}",
            params={"account_id": self._account(account_id)},
        )

    def list_job_applicants(
        self,
        account_id: str,
        job_id: str,
        *,
        cursor: Optional[str] = None,
        limit: int = 250,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 250:
            raise ValueError("Unipile v1 applicant limit must be between 1 and 250")
        params: dict[str, Any] = {
            "account_id": self._account(account_id),
            "service": "RECRUITER",
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        return self._request(
            "GET", f"/api/v1/linkedin/jobs/{job_id}/applicants", params=params
        )

    def direct_request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        if body:
            raise ValueError("Unipile v1 read-only requests do not accept a JSON body")
        if not path.startswith("/api/v1/"):
            raise ValueError("Unipile v1 request paths must begin with /api/v1/")
        return self._request(method, path, params=params)
