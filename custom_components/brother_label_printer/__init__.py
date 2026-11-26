"""Home Assistant action for Brother QL and PT network label printers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import ATTR_CONFIG_ENTRY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_HOST,
    CONF_LABEL_SIZE,
    CONF_PORT,
    CONF_PRINTER_MODEL,
    CONF_TAPE_WIDTH_MM,
    DOMAIN,
    DEFAULT_PT_TAPE_MM,
    DEFAULT_QL_MEDIA,
    PT_MODEL,
    QL_MEDIA,
    SERVICE_PRINT_LABEL,
)
from .printing import PT_TAPE_TYPES, print_label_job

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR]
BLOCK_SCHEMA = vol.Schema(
    {
        vol.Required("type"): vol.In(("text", "image", "spacer")),
        vol.Optional("text"): cv.string,
        vol.Optional("image_path"): cv.string,
        vol.Optional("alignment", default="left"): vol.In(("left", "center", "right")),
        vol.Optional("text_size", default="normal"): vol.In(("small", "normal", "large")),
        vol.Optional("bold", default=False): cv.boolean,
        vol.Optional("font", default="sans"): vol.In(("sans", "serif", "mono")),
        vol.Optional("font_path"): cv.string,
        vol.Optional("max_width_percent", default=100): vol.All(cv.positive_int, vol.Range(min=1, max=100)),
        vol.Optional("height_mm", default=3): vol.All(vol.Coerce(float), vol.Range(min=1, max=100)),
        vol.Optional("spacing_after_mm", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=25)),
    },
    extra=vol.PREVENT_EXTRA,
)
PRINT_LABEL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional("text"): cv.string,
        vol.Optional("image_path"): cv.string,
        vol.Optional("blocks"): [BLOCK_SCHEMA],
        vol.Optional("alignment", default="left"): vol.In(("left", "center", "right")),
        vol.Optional("text_size", default="normal"): vol.In(("small", "normal", "large")),
        vol.Optional("bold", default=True): cv.boolean,
        vol.Optional("tape_width_mm"): vol.In(tuple(PT_TAPE_TYPES)),
        vol.Optional("label_size"): vol.In(QL_MEDIA),
        vol.Optional("copies", default=1): vol.All(cv.positive_int, vol.Range(min=1, max=20)),
    }
)


def _get_printer_entry(
    hass: HomeAssistant, entry_id: str
) -> config_entries.ConfigEntry:
    """Resolve a loaded printer entry across Home Assistant versions."""
    get_entry = getattr(service, "async_get_config_entry", None)
    if get_entry is not None:
        return get_entry(hass, DOMAIN, entry_id)

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError("The selected Brother printer entry was not found.")
    if entry.state is not config_entries.ConfigEntryState.LOADED:
        raise ServiceValidationError(
            f"The selected Brother printer {entry.title} is not loaded."
        )
    return entry


def _get_print_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    """Serialize jobs sent to the same printer."""
    locks = hass.data.setdefault(DOMAIN, {}).setdefault("print_locks", {})
    if entry_id not in locks:
        locks[entry_id] = asyncio.Lock()
    return locks[entry_id]


def _read_image(hass: HomeAssistant, relative_path: str) -> bytes:
    """Read only from /config/www to avoid arbitrary filesystem access."""
    root = Path(hass.config.path("www")).resolve()
    requested = Path(relative_path)
    if requested.is_absolute():
        raise ValueError("image_path must be relative to /config/www")
    image_path = (root / requested).resolve()
    if image_path != root and root not in image_path.parents:
        raise ValueError("image_path must stay inside /config/www")
    return image_path.read_bytes()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the print action before any printer is configured."""

    async def async_print_label(call: ServiceCall) -> None:
        entry = _get_printer_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
        text = call.data.get("text", "").strip()
        image_path = call.data.get("image_path", "").strip()
        blocks = call.data.get("blocks")
        input_count = sum((bool(text), bool(image_path), blocks is not None))
        if input_count != 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_print_input",
            )
        if blocks is not None and not blocks:
            raise ServiceValidationError("Provide at least one Brother content block.")

        try:
            image_data = None
            if image_path:
                try:
                    image_data = await hass.async_add_executor_job(_read_image, hass, image_path)
                except (OSError, ValueError) as err:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="invalid_image_path",
                        translation_placeholders={"error": str(err)},
                    ) from err
            prepared_blocks = None
            if blocks is not None:
                prepared_blocks = []
                for index, source in enumerate(blocks, start=1):
                    block = dict(source)
                    if block["type"] == "text":
                        if not block.get("text", "").strip():
                            raise ServiceValidationError(
                                f"Brother text block {index} needs non-empty text."
                            )
                        block["text"] = block["text"].strip()
                        font_path = block.get("font_path", "").strip()
                        if font_path:
                            try:
                                block["font_data"] = await hass.async_add_executor_job(
                                    _read_image, hass, font_path
                                )
                            except (OSError, ValueError) as err:
                                raise ServiceValidationError(
                                    translation_domain=DOMAIN,
                                    translation_key="invalid_asset_path",
                                    translation_placeholders={"error": str(err)},
                                ) from err
                    elif block["type"] == "image":
                        path = block.get("image_path", "").strip()
                        if not path:
                            raise ServiceValidationError(
                                f"Brother image block {index} needs image_path."
                            )
                        try:
                            block["image_data"] = await hass.async_add_executor_job(
                                _read_image, hass, path
                            )
                        except (OSError, ValueError) as err:
                            raise ServiceValidationError(
                                translation_domain=DOMAIN,
                                translation_key="invalid_asset_path",
                                translation_placeholders={"error": str(err)},
                            ) from err
                    prepared_blocks.append(block)
            model = entry.data[CONF_PRINTER_MODEL]
            tape_width = str(call.data.get(CONF_TAPE_WIDTH_MM, entry.data[CONF_TAPE_WIDTH_MM]))
            label_size = str(call.data.get(CONF_LABEL_SIZE, entry.data[CONF_LABEL_SIZE]))
            if model == PT_MODEL and tape_width not in PT_TAPE_TYPES:
                tape_width = DEFAULT_PT_TAPE_MM
            if model != PT_MODEL and label_size not in QL_MEDIA:
                label_size = DEFAULT_QL_MEDIA
            async with _get_print_lock(hass, entry.entry_id):
                await hass.async_add_executor_job(
                    print_label_job,
                    entry.data[CONF_HOST],
                    entry.data[CONF_PORT],
                    model,
                    label_size,
                    tape_width,
                    text or None,
                    image_data,
                    call.data["copies"],
                    call.data["alignment"],
                    call.data["text_size"],
                    call.data["bold"],
                    prepared_blocks,
                )
        except ServiceValidationError:
            raise
        except Exception as err:
            raise HomeAssistantError(f"Could not print to {entry.title}: {err}") from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_PRINT_LABEL,
        async_print_label,
        schema=PRINT_LABEL_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """A config entry represents a single network printer."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Unload one printer entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
