"""Config flow for MiKettle BLE."""

from __future__ import annotations

import os
import re
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_POLL_INTERVAL,
    CONF_PRODUCT_ID,
    CONF_TOKEN,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PRODUCT_ID,
    DOMAIN,
    PRODUCT_IDS,
)
from .kettle import TOKEN_LENGTH

MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")

# Xiaomi MiBeacon: bytes 2-3 of the service data are the product ID (little endian).
MIBEACON_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"


def _kettle_product_id(service_info: bluetooth.BluetoothServiceInfoBleak) -> int | None:
    """Return the product ID if this is a supported kettle, otherwise None."""
    data = service_info.service_data.get(MIBEACON_UUID)
    if not data or len(data) < 4:
        return None
    product_id = int.from_bytes(data[2:4], "little")
    return product_id if product_id in PRODUCT_IDS else None


def _generate_token() -> str:
    """Generate a 12 byte token. The kettle accepts any - no cloud needed."""
    return os.urandom(TOKEN_LENGTH).hex()


def _title(address: str) -> str:
    """Build the entry title from the last two bytes of the MAC, like Xiaomi BLE."""
    return f"Mi Kettle {address[-5:].replace(':', '')}"


class MiKettleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for a kettle."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovered: tuple[str, int, str | None] | None = None

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a kettle discovered over Bluetooth.

        The manifest matcher covers every Xiaomi MiBeacon device, so the product ID
        decides whether this really is a kettle. If it is not, the flow aborts
        silently and the user is not bothered.
        """
        product_id = _kettle_product_id(discovery_info)
        if product_id is None:
            return self.async_abort(reason="not_supported")

        address = discovery_info.address.upper()
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        self._discovered = (address, product_id, discovery_info.name)
        self.context["title_placeholders"] = {"name": _title(address)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered kettle - no MAC or model needed.

        The token is optional, but beware: the auth handshake pairs the kettle with
        whatever token it is given. An empty field means a new one is generated and
        the official Xiaomi app loses access until its token is entered here.
        """
        assert self._discovered is not None
        address, product_id, name = self._discovered
        errors: dict[str, str] = {}

        if user_input is not None:
            token = (user_input.get(CONF_TOKEN) or "").strip().replace(":", "")
            if token:
                if not _is_valid_token(token):
                    errors[CONF_TOKEN] = "invalid_token"
            else:
                token = _generate_token()

            if not errors:
                return self.async_create_entry(
                    title=_title(address),
                    data={
                        CONF_ADDRESS: address,
                        CONF_PRODUCT_ID: product_id,
                        CONF_TOKEN: token,
                    },
                )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({vol.Optional(CONF_TOKEN, default=""): str}),
            description_placeholders={
                "name": name or "Mi Kettle",
                "address": address,
                "model": PRODUCT_IDS[product_id],
            },
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address, model or token of an existing kettle."""
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a kettle manually, offering the currently visible BLE devices."""
        errors: dict[str, str] = {}
        reconfigure = self.source == SOURCE_RECONFIGURE
        entry = self._get_reconfigure_entry() if reconfigure else None

        if user_input is not None:
            address = format_mac(user_input[CONF_ADDRESS].strip()).upper()
            if not MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            token = (user_input.get(CONF_TOKEN) or "").strip().replace(":", "")
            if token:
                if not _is_valid_token(token):
                    errors[CONF_TOKEN] = "invalid_token"
            elif entry is not None:
                # Keep the previous token on reconfigure - no need to re-pair.
                token = entry.data[CONF_TOKEN]
            else:
                token = _generate_token()

            if not errors:
                data = {
                    CONF_ADDRESS: address,
                    CONF_PRODUCT_ID: int(user_input[CONF_PRODUCT_ID]),
                    CONF_TOKEN: token,
                }
                if entry is not None:
                    for other in self._async_current_entries():
                        if other.entry_id != entry.entry_id and other.unique_id == address:
                            return self.async_abort(reason="already_configured")
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=address,
                        title=_title(address),
                        data_updates=data,
                    )
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=_title(address), data=data)

        # Offer the visible devices but allow a MAC to be typed in - a kettle that
        # is off its base may not be advertising right now.
        address_selector = SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=address, label=label)
                    for address, label in self._discovered_addresses().items()
                ],
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )

        previous = dict(entry.data) if entry is not None else {}
        default_address = (user_input or previous).get(CONF_ADDRESS, vol.UNDEFINED)
        default_product = (user_input or previous).get(
            CONF_PRODUCT_ID, DEFAULT_PRODUCT_ID
        )

        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS, default=default_address): address_selector,
                    vol.Required(CONF_PRODUCT_ID, default=default_product): vol.In(
                        PRODUCT_IDS
                    ),
                    vol.Optional(CONF_TOKEN, default=""): str,
                }
            ),
            errors=errors,
        )

    def _discovered_addresses(self) -> dict[str, str]:
        """Return visible connectable BLE devices, recognised kettles first."""
        current = self._async_current_ids()
        rows: list[tuple[int, str, str]] = []
        for info in bluetooth.async_discovered_service_info(self.hass, True):
            address = info.address.upper()
            if address in current:
                continue
            product_id = _kettle_product_id(info)
            if product_id is not None:
                label = f"{address} - Mi Kettle, {PRODUCT_IDS[product_id]} ({info.rssi} dBm)"
            else:
                label = f"{address} ({info.name or 'unnamed'}, {info.rssi} dBm)"
            rows.append((0 if product_id is not None else 1, address, label))
        return {address: label for _, address, label in sorted(rows)}

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> MiKettleOptionsFlow:
        """Return the options flow."""
        return MiKettleOptionsFlow()


def _is_valid_token(token: str) -> bool:
    """Return True for a 12 byte hex token."""
    try:
        return len(bytes.fromhex(token)) == TOKEN_LENGTH
    except ValueError:
        return False


class MiKettleOptionsFlow(OptionsFlowWithReload):
    """Options: the polling interval (the base class handles the reload).

    Note: the BLE pause switch writes the options directly via async_update_entry,
    so it does not go through this flow and does not trigger a reload - which is
    exactly what we want.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
                }
            ),
        )
