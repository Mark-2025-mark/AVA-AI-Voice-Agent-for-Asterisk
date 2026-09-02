"""HTTP client for FS PBX public API v1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx


@dataclass
class FspbxApiResult:
    ok: bool
    status_code: int
    data: Any
    error: Optional[str] = None


class FspbxApiClient:
    """Minimal FS PBX v1 client for domain listing and connection tests."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_ssl: bool = True,
        timeout_ms: int = 10000,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.token = (token or "").strip()
        self.verify_ssl = bool(verify_ssl)
        self.timeout = max(1.0, float(timeout_ms) / 1000.0)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _url(self, path: str) -> str:
        path = path.lstrip("/")
        return urljoin(f"{self.base_url}/", path)

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None) -> FspbxApiResult:
        if not self.base_url:
            return FspbxApiResult(False, 0, None, error="FS PBX base URL is required.")
        if not self.token:
            return FspbxApiResult(False, 0, None, error="FS PBX API token is required.")

        try:
            with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                response = client.request(
                    method.upper(),
                    self._url(path),
                    headers=self._headers(),
                    params=params or None,
                )
        except httpx.TimeoutException:
            return FspbxApiResult(False, 0, None, error="FS PBX request timed out.")
        except httpx.RequestError as exc:
            return FspbxApiResult(False, 0, None, error=f"FS PBX request failed: {exc}")

        data: Any = None
        text = (response.text or "").strip()
        if text:
            try:
                data = response.json()
            except ValueError:
                data = text

        if response.is_success:
            return FspbxApiResult(True, response.status_code, data)

        error = None
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                error = str(err.get("message") or err.get("code") or "FS PBX request failed.")
            elif isinstance(err, str):
                error = err
        if not error:
            error = f"FS PBX returned HTTP {response.status_code}."
        return FspbxApiResult(False, response.status_code, data, error=error)

    def test_connection(self) -> FspbxApiResult:
        return self._request("GET", "api/v1/domains", params={"limit": 1})

    def list_domains(self, *, limit: int = 100) -> FspbxApiResult:
        domains: List[Dict[str, Any]] = []
        starting_after: Optional[str] = None
        page_limit = max(1, min(int(limit), 100))

        while True:
            params: Dict[str, Any] = {"limit": page_limit}
            if starting_after:
                params["starting_after"] = starting_after
            result = self._request("GET", "api/v1/domains", params=params)
            if not result.ok:
                return result

            payload = result.data if isinstance(result.data, dict) else {}
            rows = payload.get("data") if isinstance(payload.get("data"), list) else []
            for row in rows:
                if isinstance(row, dict):
                    domains.append(row)
            if len(domains) >= limit:
                break
            if not payload.get("has_more"):
                break
            if not rows:
                break
            last = rows[-1]
            starting_after = str(last.get("domain_uuid") or "").strip() or None
            if not starting_after:
                break

        return FspbxApiResult(True, 200, {"object": "list", "data": domains[:limit]})
