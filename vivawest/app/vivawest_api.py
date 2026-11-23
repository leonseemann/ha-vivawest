"""
Client für die REST-API des Vivawest Kundenportals.

Ablauf: POST /rest/login → sessionId → Header X-Session-Id für alle weiteren
Anfragen → PUT /rest/logout.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

log = logging.getLogger("vivawest")

BASE_URL = "https://api.kundenportal.vivawest.de/rest/"
# Version des Web-Frontends, die das Portal bei jeder Anfrage mitschickt.
WEB_VERSION = "2.11.0"


class VivawestError(Exception):
    """Fehlerantwort der Vivawest-API (enthält Status und Fehlermeldung des Portals)."""


class VivawestClient:
    def __init__(self, login: str, password: str, timeout: int = 15) -> None:
        self._login = login
        self._password = password
        self._timeout = timeout
        self._http = requests.Session()
        self._http.headers.update({
            "Accept": "application/json",
            "X-Web-Version": WEB_VERSION,
            "Origin": "https://kundenportal.vivawest.de",
        })

    # -- Kontextmanager: Login beim Betreten, Logout beim Verlassen ---------
    def __enter__(self) -> "VivawestClient":
        self.login()
        return self

    def __exit__(self, *exc) -> None:
        self.logout()
        self._http.close()

    # -- HTTP ----------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> Any:
        resp = self._http.request(method, BASE_URL + path, timeout=self._timeout, **kwargs)
        if not resp.ok:
            detail = ""
            try:
                body = resp.json()
                detail = f" ({body.get('exception')}: {body.get('message') or '-'})"
            except ValueError:
                pass
            raise VivawestError(f"{method} {path} → HTTP {resp.status_code}{detail}")
        return resp.json() if resp.content else None

    # -- Session ---------------------------------------------------------------
    def login(self) -> None:
        log.info("Melde am Kundenportal an...")
        data = self._request("POST", "login", json={"email": self._login, "password": self._password})
        session_id = (data or {}).get("sessionId")
        if not session_id:
            raise VivawestError("sessionId fehlt in der Login-Antwort")
        self._http.headers["X-Session-Id"] = session_id
        log.info("Anmeldung erfolgreich.")

    def logout(self) -> None:
        if "X-Session-Id" not in self._http.headers:
            return
        try:
            self._request("PUT", "logout")
        except Exception as e:  # Logout-Fehler sind unkritisch
            log.debug("Logout fehlgeschlagen: %s", e)
        self._http.headers.pop("X-Session-Id", None)

    # -- UVI (unterjährige Verbrauchsinformation) ------------------------------
    def get_current(self) -> dict:
        """Letzte Monatswerte aller Verbrauchsarten.

        Rückgabe: {"types": [...], "messwerte": {typ: {...}}, "heizenergieEinheit": "ve"}
        Die Vertragsdaten (persönliche Daten) werden verworfen.
        """
        data = self._request("GET", "uvi/current") or {}
        data.pop("contract", None)
        return data

    def get_history(self, uvi_type: str) -> list[dict]:
        """Monatsverbräuche einer Verbrauchsart (neueste zuerst), Summe über alle Zähler.

        Jeder Eintrag: {"monat", "jahr", "verbrauchswert", ["kwh"], "ablesedatum", ...}
        """
        data = self._request("GET", f"uvi/current/{uvi_type}") or {}
        return data.get("messwerte") or []


def month_values(history: list[dict], field: str) -> dict[tuple[int, int], float]:
    """Wandelt eine Historie in {(jahr, monat): wert} um; fehlende Werte werden ausgelassen."""
    result: dict[tuple[int, int], float] = {}
    for entry in history:
        value: Optional[float] = entry.get(field)
        year, month = entry.get("jahr"), entry.get("monat")
        if value is None or not year or not month:
            continue
        result[(int(year), int(month))] = float(value)
    return result
