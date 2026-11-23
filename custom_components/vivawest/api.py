"""Asynchroner Client für die (inoffizielle) REST-API des Vivawest Kundenportals.

Ablauf: POST /rest/login → sessionId → Header X-Session-Id → PUT /rest/logout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aiohttp

BASE_URL = "https://api.kundenportal.vivawest.de/rest/"
# Version des Web-Frontends, die das Portal bei jeder Anfrage mitschickt.
WEB_VERSION = "2.11.0"
TIMEOUT = aiohttp.ClientTimeout(total=30)


class VivawestError(Exception):
    """Allgemeiner Fehler beim Zugriff auf das Kundenportal."""


class VivawestAuthError(VivawestError):
    """Zugangsdaten ungültig."""


@dataclass
class VivawestData:
    """Alle Daten eines Abrufs."""

    partner_id: str
    # Letzter Monatswert je Verbrauchsart: {"kaltwasser": {...}, ...}
    current: dict[str, dict[str, Any]] = field(default_factory=dict)
    heating_unit: str | None = None
    # Monatsverlauf je Verbrauchsart (neueste zuerst)
    history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Mietkonditionen als Zeiträume
    conditions: list[dict[str, Any]] = field(default_factory=list)


class VivawestClient:
    """Client für ein Kundenportal-Konto."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._session_id: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Web-Version": WEB_VERSION,
            "Origin": "https://kundenportal.vivawest.de",
        }
        if self._session_id:
            headers["X-Session-Id"] = self._session_id
        return headers

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        try:
            async with self._session.request(
                method, BASE_URL + path, json=json, headers=self._headers(), timeout=TIMEOUT
            ) as resp:
                body: Any = None
                if resp.content_type == "application/json":
                    body = await resp.json()
                if resp.status in (401, 403):
                    message = (body or {}).get("message") if isinstance(body, dict) else None
                    raise VivawestAuthError(message or f"HTTP {resp.status}")
                if resp.status >= 400:
                    exc = (body or {}).get("exception") if isinstance(body, dict) else None
                    raise VivawestError(f"{method} {path}: HTTP {resp.status} ({exc or '-'})")
                return body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise VivawestError(f"{method} {path}: {err!r}") from err

    async def login(self) -> str:
        """Meldet an und gibt die Partner-ID (eindeutig je Konto) zurück."""
        data = await self._request(
            "POST", "login", json={"email": self._email, "password": self._password}
        )
        if not isinstance(data, dict) or not data.get("sessionId"):
            raise VivawestError("sessionId fehlt in der Login-Antwort")
        self._session_id = data["sessionId"]
        return str(data.get("partnerId") or "")

    async def logout(self) -> None:
        if not self._session_id:
            return
        try:
            await self._request("PUT", "logout")
        except VivawestError:
            pass  # unkritisch
        self._session_id = None

    async def fetch_all(self) -> VivawestData:
        """Login, alle Daten abrufen, Logout."""
        partner_id = await self.login()
        try:
            current = await self._request("GET", "uvi/current") or {}
            types: list[str] = current.get("types") or []
            history = {}
            for uvi_type in types:
                detail = await self._request("GET", f"uvi/current/{uvi_type}") or {}
                history[uvi_type] = detail.get("messwerte") or []
            try:
                conditions = await self._request("GET", "contracts/conditions") or []
            except VivawestAuthError:
                raise
            except VivawestError:
                conditions = []  # Kosten sind optional
            return VivawestData(
                partner_id=partner_id,
                current=current.get("messwerte") or {},
                heating_unit=current.get("heizenergieEinheit"),
                history=history,
                conditions=conditions if isinstance(conditions, list) else [],
            )
        finally:
            await self.logout()
