"""Authenticated client for the local Windows SAP2000 STA COM agent."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx


class SapUnavailable(RuntimeError):
    pass


class SapAgentClient:
    """Only talks to a loopback agent; credentials never leave the workstation."""

    def __init__(self) -> None:
        self.url = os.getenv("SAP2000_AGENT_URL", "").rstrip("/")
        self.token = os.getenv("SAP2000_AGENT_TOKEN", "")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    @property
    def available(self) -> bool:
        return self.configured and self._is_loopback_url()

    def _is_loopback_url(self) -> bool:
        parsed = urlparse(self.url)
        return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}

    def _configuration_error(self) -> str:
        if not self.configured:
            return "SAP2000 agent is not configured (SAP2000_AGENT_URL and SAP2000_AGENT_TOKEN required)."
        return "SAP2000_AGENT_URL must be an http loopback URL (localhost, 127.0.0.1, or ::1)."

    def request(self, method: str, path: str, payload: object | None = None) -> dict:
        if not self.available:
            raise SapUnavailable(self._configuration_error())
        try:
            # Disabling environment proxies prevents forwarding the bearer token
            # to a proxy when the agent is intentionally loopback-only.
            with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
                response = client.request(
                    method,
                    f"{self.url}{path}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.token}"},
                )
        except httpx.HTTPError as exc:
            raise SapUnavailable(f"SAP2000 agent unavailable: {exc}") from exc
        if response.status_code >= 400:
            raise SapUnavailable(
                f"SAP2000 agent rejected request ({response.status_code}): {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise SapUnavailable("SAP2000 agent returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise SapUnavailable("SAP2000 agent returned an invalid response")
        return data

    def status(self) -> dict:
        if self.available:
            return self.request("GET", "/v1/sap/status")
        return {
            "platform": os.name,
            "agent_configured": self.configured,
            "connected": False,
            "reason": self._configuration_error() if self.configured else None,
        }

    def model(self) -> dict:
        return self.request("GET", "/v1/sap/model")

    def snapshot(self, groups: list[dict] | None = None) -> dict:
        return self.request("POST", "/v1/sap/snapshot", {} if groups is None else {"groups": groups})
