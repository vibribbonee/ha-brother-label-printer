"""Offline printers fail fast, and settings can change without re-adding."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brother_label_printer import printing
from custom_components.brother_label_printer.const import DOMAIN


def test_unreachable_printer_is_reported_with_its_timeout():
    with patch.object(printing.socket, "create_connection", side_effect=TimeoutError) as connect, \
            pytest.raises(ConnectionError, match="not reachable"):
        printing._ensure_reachable("192.168.0.30", 9100)
    connect.assert_called_once_with(("192.168.0.30", 9100), timeout=5.0)


def test_missing_fonts_fall_back_without_failing():
    with patch.object(printing, "_FONT_DIRS", ("/nonexistent",)):
        assert printing._font(24, True, "mono") is not None


@pytest.fixture
async def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="192.168.0.30:9100", title="Labels",
        data={"name": "Labels", "host": "192.168.0.30", "port": 9100,
              "printer_model": "QL-710W", "label_size": "62", "tape_width_mm": "12"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_offline_printer_reports_a_clear_error(hass, entry):
    with patch.object(
        printing, "_ensure_reachable",
        side_effect=ConnectionError("The printer at 192.168.0.30:9100 is not reachable."),
    ), pytest.raises(HomeAssistantError, match="not reachable"):
        await hass.services.async_call(
            DOMAIN, "print_label",
            {"config_entry_id": entry.entry_id, "text": "Milk"},
            blocking=True,
        )


async def test_reconfigure_changes_address_and_default_media(hass, entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["step_id"] == "reconfigure"
    with patch(
        "custom_components.brother_label_printer.config_flow._can_connect", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.0.31", "port": 9100, "label_size": "29x90", "tape_width_mm": "12"},
        )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "192.168.0.31"
    assert entry.data["label_size"] == "29x90"
    assert entry.data["printer_model"] == "QL-710W"
    assert entry.unique_id == "192.168.0.31:9100"
