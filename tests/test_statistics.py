"""Tests für Verteilung und Import der Langzeitstatistik."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.vivawest.statistics import (
    async_import_statistics,
    distribute,
    month_hours,
    plan_import,
)

from .conftest import make_data

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.mark.parametrize(("month", "hours"), [((2026, 10), 745), ((2026, 3), 743), ((2026, 2), 672)])
def test_month_hours_dst(month, hours) -> None:
    assert len(month_hours(month, BERLIN)) == hours


def test_distribute() -> None:
    rows = distribute((2026, 10), 3.55, 10.0, BERLIN)
    assert rows[0]["start"] == datetime(2026, 10, 1, tzinfo=BERLIN)
    assert rows[-1]["sum"] == pytest.approx(13.55)


def test_plan_import() -> None:
    values = {(2026, 8): 1.0, (2026, 9): 2.0, (2026, 10): 3.0}
    # leer → alles ab Startsumme 0
    assert plan_import(values, {}) == (sorted(values.items()), 0.0)
    # alles vorhanden → nichts
    assert plan_import(values, {(2026, 7): 5.0, (2026, 8): 6.0, (2026, 9): 8.0, (2026, 10): 11.0}) == ([], 0.0)
    # neuer Monat → nur dieser, Startsumme = Ende Vormonat
    plan, start = plan_import(values, {(2026, 8): 1.0, (2026, 9): 3.0})
    assert plan == [((2026, 10), 3.0)] and start == 3.0
    # Korrektur im September → September und Oktober neu
    plan, start = plan_import(values, {(2026, 8): 1.0, (2026, 9): 2.5, (2026, 10): 5.5})
    assert [m for m, _ in plan] == [(2026, 9), (2026, 10)] and start == 1.0


async def _monthly(hass: HomeAssistant, statistic_id: str) -> dict:
    from homeassistant.components.recorder import get_instance
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2026, 1, 1, tzinfo=BERLIN), None,
        {statistic_id}, "month", None, {"sum", "change"})
    return {datetime.fromtimestamp(r["start"], BERLIN).month: (round(r["change"], 4), round(r["sum"], 4))
            for r in rows.get(statistic_id, [])}


async def test_import_roundtrip(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    await hass.config.async_update(time_zone="Europe/Berlin")
    data = make_data()

    await async_import_statistics(hass, data.history)
    await async_wait_recording_done(hass)
    assert await _monthly(hass, "vivawest:kaltwasser") == {8: (3.4, 3.4), 9: (1.6, 5.0), 10: (3.5, 8.5)}
    assert (await _monthly(hass, "vivawest:heizenergie"))[10] == (82.0, 82.0)

    # Zweiter Lauf ohne Änderung schreibt nichts
    from unittest.mock import patch
    with patch("custom_components.vivawest.statistics.async_add_external_statistics") as add:
        await async_import_statistics(hass, data.history)
    add.assert_not_called()

    # Neuer Monat + Korrektur im September
    hist = data.history["kaltwasser"]
    hist[1]["verbrauchswert"] = 2.0
    hist.insert(0, {"jahr": 2026, "monat": 11, "verbrauchswert": 4.0})
    await async_import_statistics(hass, {"kaltwasser": hist})
    await async_wait_recording_done(hass)
    assert await _monthly(hass, "vivawest:kaltwasser") == {
        8: (3.4, 3.4), 9: (2.0, 5.4), 10: (3.5, 8.9), 11: (4.0, 12.9)}


async def test_cost_statistics(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    await hass.config.async_update(time_zone="Europe/Berlin")
    data = make_data()
    prices = {"preis_wasser": 5.72, "preis_heizenergie": 0.12}

    await async_import_statistics(hass, data.history, prices)
    await async_wait_recording_done(hass)
    assert await _monthly(hass, "vivawest:kaltwasser_kosten") == {
        8: (19.448, 19.448), 9: (9.152, 28.6), 10: (20.02, 48.62)}
    assert (await _monthly(hass, "vivawest:heizenergie_kosten"))[10] == (9.84, 9.84)
    assert await _monthly(hass, "vivawest:warmwasser_energie_kosten") == {}  # kein Preis

    # Preisänderung → alle Monate neu berechnet
    await async_import_statistics(hass, data.history, {"preis_wasser": 6.0})
    await async_wait_recording_done(hass)
    assert (await _monthly(hass, "vivawest:kaltwasser_kosten"))[10] == (21.0, 51.0)
