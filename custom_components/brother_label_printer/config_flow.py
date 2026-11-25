"""Config flow for QL and P-touch printers on the local network."""

from __future__ import annotations

import asyncio

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # Home Assistant before the 2026.1 service-info move.
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_LABEL_SIZE,
    CONF_PRINTER_MODEL,
    CONF_TAPE_WIDTH_MM,
    DEFAULT_PORT,
    DEFAULT_PT_TAPE_MM,
    DEFAULT_QL_MEDIA,
    DOMAIN,
    PT_MODEL,
    PT_TAPE_WIDTHS,
    QL_MEDIA,
    QL_MODEL,
)


async def _can_connect(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


class BrotherLabelPrinterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Add one Brother network label printer."""

    VERSION = 1

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ):
        """Offer to configure a supported Brother printer discovered by mDNS."""
        details = " ".join(
            [
                discovery_info.name,
                *(
                    f"{key} {value.decode(errors='ignore') if isinstance(value, bytes) else value}"
                    for key, value in discovery_info.properties.items()
                ),
            ]
        ).lower()
        if "ql-710w" in details:
            model = QL_MODEL
        elif "pt-e550w" in details:
            model = PT_MODEL
        else:
            return self.async_abort(reason="unsupported_device")

        host = str(discovery_info.host)
        port = discovery_info.port
        await self.async_set_unique_id(f"{host.lower()}:{port}")
        self._abort_if_unique_id_configured()
        name = f"{model} at {host}"
        self.context["title_placeholders"] = {"name": name}
        self._discovered_data = {
            CONF_NAME: name,
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_PRINTER_MODEL: model,
            CONF_LABEL_SIZE: DEFAULT_QL_MEDIA,
            CONF_TAPE_WIDTH_MM: DEFAULT_PT_TAPE_MM,
        }
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        """Confirm adding the discovered printer."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_data[CONF_NAME], data=self._discovered_data
            )
        self._set_confirm_only()
        return self.async_show_form(step_id="confirm")

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            if not await _can_connect(host, port):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{host.lower()}:{port}")
                self._abort_if_unique_id_configured()
                name = user_input[CONF_NAME].strip()
                return self.async_create_entry(
                    title=name or f"{user_input[CONF_PRINTER_MODEL]} at {host}",
                    data={
                        CONF_NAME: name,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_PRINTER_MODEL: user_input[CONF_PRINTER_MODEL],
                        CONF_LABEL_SIZE: user_input[CONF_LABEL_SIZE],
                        CONF_TAPE_WIDTH_MM: user_input[CONF_TAPE_WIDTH_MM],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Brother label printer"): TextSelector(),
                vol.Required(CONF_HOST): TextSelector(),
                vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
                    NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_PRINTER_MODEL, default=QL_MODEL): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": QL_MODEL, "label": QL_MODEL},
                            {"value": PT_MODEL, "label": PT_MODEL},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_LABEL_SIZE, default=DEFAULT_QL_MEDIA): SelectSelector(
                    SelectSelectorConfig(
                        options=[{"value": size, "label": f"{size} mm/media"} for size in QL_MEDIA],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_TAPE_WIDTH_MM, default=DEFAULT_PT_TAPE_MM): SelectSelector(
                    SelectSelectorConfig(
                        options=[{"value": width, "label": f"{width} mm TZe tape"} for width in PT_TAPE_WIDTHS],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        """Change the address or the loaded media without re-adding the printer."""
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            if not await _can_connect(host, port):
                errors["base"] = "cannot_connect"
            else:
                unique_id = f"{host.lower()}:{port}"
                if any(
                    other.unique_id == unique_id and other.entry_id != entry.entry_id
                    for other in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    data_updates={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_LABEL_SIZE: user_input[CONF_LABEL_SIZE],
                        CONF_TAPE_WIDTH_MM: user_input[CONF_TAPE_WIDTH_MM],
                    },
                )
        data = entry.data
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=data[CONF_HOST]): TextSelector(),
                vol.Required(CONF_PORT, default=data[CONF_PORT]): NumberSelector(
                    NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_LABEL_SIZE, default=data.get(CONF_LABEL_SIZE, DEFAULT_QL_MEDIA)): SelectSelector(
                    SelectSelectorConfig(
                        options=[{"value": size, "label": f"{size} mm/media"} for size in QL_MEDIA],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_TAPE_WIDTH_MM, default=data.get(CONF_TAPE_WIDTH_MM, DEFAULT_PT_TAPE_MM)): SelectSelector(
                    SelectSelectorConfig(
                        options=[{"value": width, "label": f"{width} mm TZe tape"} for width in PT_TAPE_WIDTHS],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)
