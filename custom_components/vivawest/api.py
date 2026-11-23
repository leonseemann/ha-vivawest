"""Asynchroner Client für die (inoffizielle) REST-API des Vivawest Kundenportals.

Ablauf: POST /rest/login → sessionId → Header X-Session-Id → PUT /rest/logout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import LOGGER

BASE_URL = "https://api.kundenportal.vivawest.de/rest/"
# Version des Web-Frontends, die das Portal bei jeder Anfrage mitschickt.
WEB_VERSION = "2.11.0"
TIMEOUT = aiohttp.ClientTimeout(total=30)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slim_meter(entry: dict[str, Any]) -> dict[str, Any]:
    """Zähler ohne den verschachtelten Wohnungs-/Adressblock."""
    geraet = {k: v for k, v in entry["geraet"].items() if k != "wohneinheit"}
    messwerte = [
        {k: v for k, v in m.items() if k != "geraet"} for m in entry.get("messwerte") or []
    ]
    return {"geraet": geraet, "messwerte": messwerte}


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
    # Einzelzähler je Verbrauchsart: {"kaltwasser": [{"geraet": {...}, "messwerte": [...]}]}
    meters: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Neueste Nebenkostenabrechnung je Art: {"Betriebskosten": {settlement-Objekt}}
    settlements: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Optionale Zusatzdaten; None = nicht abrufbar
    unread_messages: int | None = None
    open_damages: list[dict[str, Any]] | None = None
    notifications: int | None = None


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

    async def _request(
        self, method: str, path: str, json: Any = None, params: dict[str, Any] | None = None
    ) -> Any:
        try:
            async with self._session.request(
                method, BASE_URL + path, json=json, params=params,
                headers=self._headers(), timeout=TIMEOUT,
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

    async def _optional(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Abruf, dessen Fehlschlagen die übrigen Daten nicht verhindern soll."""
        try:
            return await self._request("GET", path, params=params)
        except VivawestAuthError:
            raise
        except VivawestError as err:
            LOGGER.debug("Optionaler Abruf %s fehlgeschlagen: %s", path, err)
            return None

    async def _fetch_settlements(self) -> dict[str, dict[str, Any]]:
        """Neueste Abrechnung je Art (Betriebs-, Heiz-, Wasserkosten)."""
        result: dict[str, dict[str, Any]] = {}
        overview = await self._optional("additional-costs/settlements")
        for kind in overview if isinstance(overview, list) else []:
            periods = (kind.get("BillingOverview2Periods") or {}).get("results") or []
            periods = [p for p in periods if p.get("Ident")]
            if not kind.get("Type") or not periods:
                continue
            newest = max(periods, key=lambda p: p.get("AbrechnungBis") or "")
            detail = await self._optional(f"additional-costs/details/{newest['Ident']}")
            if isinstance(detail, dict) and isinstance(detail.get("settlement"), dict):
                result[kind["Type"]] = detail["settlement"]
        return result

    async def fetch_all(self) -> VivawestData:
        """Login, alle Daten abrufen, Logout."""
        partner_id = await self.login()
        try:
            current = await self._request("GET", "uvi/current") or {}
            data = VivawestData(
                partner_id=partner_id,
                current=current.get("messwerte") or {},
                heating_unit=current.get("heizenergieEinheit"),
            )
            for uvi_type in current.get("types") or []:
                detail = await self._request("GET", f"uvi/current/{uvi_type}") or {}
                data.history[uvi_type] = detail.get("messwerte") or []
                data.meters[uvi_type] = [
                    _slim_meter(g) for g in detail.get("geraete") or [] if g.get("geraet")
                ]

            conditions = await self._optional("contracts/conditions")
            data.conditions = conditions if isinstance(conditions, list) else []
            data.settlements = await self._fetch_settlements()

            unread = await self._optional("postbox/unread")
            if isinstance(unread, dict):
                data.unread_messages = _int(unread.get("UnreadPostbox"))
            damages = await self._optional(
                "damages/reports", {"status": "progress", "limit": 50, "offset": 0}
            )
            if isinstance(damages, dict):
                data.open_damages = damages.get("results") or []
            badges = await self._optional("notification-badges")
            if isinstance(badges, dict):  # {<Vertragsnummer>: {"count": n, "badges": [...]}}
                data.notifications = sum(
                    _int(v.get("count")) or 0 for v in badges.values() if isinstance(v, dict)
                )
            return data
        finally:
            await self.logout()
