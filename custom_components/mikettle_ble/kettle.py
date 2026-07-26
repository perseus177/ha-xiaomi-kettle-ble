"""Xiaomi Mi Smart Kettle (yunmi.kettle.*) BLE protocol on top of bleak.

Protocol reverse engineering: https://github.com/aprosvetova/xiaomi-kettle
Reference implementations: drndos/mikettle (bluepy), devbis/ble2mqtt (bleak).

Important: the kettle accepts only ONE GATT connection at a time. While Home
Assistant is connected, the official Xiaomi app cannot connect. That is why there
is no persistent connection here - every operation is
connect -> authenticate -> act -> disconnect.

Equally important: the auth handshake is a PAIRING operation. The kettle stores
the token it is given and the previous owner (the app) loses access. Always
configure the integration with the token from the Mi Home cloud.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

_LOGGER = logging.getLogger(__name__)

# Services
UUID_SERVICE_AUTH = "0000fe95-0000-1000-8000-00805f9b34fb"
UUID_SERVICE_DATA = "01344736-0000-1000-8000-262837236156"

# Characteristics
UUID_AUTH_INIT = "00000010-0000-1000-8000-00805f9b34fb"
UUID_AUTH = "00000001-0000-1000-8000-00805f9b34fb"
UUID_VERSION = "00000004-0000-1000-8000-00805f9b34fb"
UUID_SETUP = "0000aa01-0000-1000-8000-00805f9b34fb"  # [type, temperature]
UUID_STATUS = "0000aa02-0000-1000-8000-00805f9b34fb"  # status notifications
UUID_TIME_LIMIT = "0000aa04-0000-1000-8000-00805f9b34fb"  # hours * 2
UUID_BOIL_MODE = "0000aa05-0000-1000-8000-00805f9b34fb"  # 0/1

KEY1 = bytes([0x90, 0xCA, 0x85, 0xDE])
KEY2 = bytes([0x92, 0xAB, 0x54, 0xFA])

ACTION_MAP = {0: "idle", 1: "heating", 2: "cooling", 3: "keep_warm"}
MODE_MAP = {255: "none", 1: "boil", 2: "keep_warm", 3: "keep_warm"}
# 0 = boil, then let the water cool down to the target temperature
# 1 = heat straight up to the target temperature (no boiling)
KEEP_WARM_TYPE_MAP = {0: "cool_down", 1: "warm_up"}

TEMP_MIN = 40
TEMP_MAX = 95
TIME_LIMIT_MAX_HALF_HOURS = 24  # 12 h

AUTH_TIMEOUT = 10.0
STATUS_TIMEOUT = 10.0
TOKEN_LENGTH = 12


class MiKettleError(Exception):
    """Base error for kettle communication."""


class MiKettleBusyError(MiKettleError):
    """The kettle refused the connection (busy, or off its base)."""


class MiKettleAuthError(MiKettleError):
    """Authentication failed (usually a wrong product ID)."""


@dataclass
class MiKettleState:
    """State read from the kettle."""

    action: str | None = None
    mode: str | None = None
    current_temperature: int | None = None
    target_temperature: int | None = None
    keep_warm_type: str | None = None
    keep_warm_elapsed: int | None = None
    keep_warm_limit: float | None = None  # hours
    turn_off_after_boil: bool | None = None

    @classmethod
    def from_status(cls, data: bytes) -> MiKettleState:
        """Parse a status notification from aa02 (12 bytes)."""
        if len(data) < 7:
            raise MiKettleError(f"Status packet too short: {data.hex()}")
        elapsed: int | None = None
        if len(data) >= 9:
            elapsed = int.from_bytes(data[7:9], "little")
        elif len(data) >= 8:
            elapsed = data[7]
        return cls(
            action=ACTION_MAP.get(data[0]),
            mode=MODE_MAP.get(data[1]),
            target_temperature=data[4],
            current_temperature=data[5],
            keep_warm_type=KEEP_WARM_TYPE_MAP.get(data[6]),
            keep_warm_elapsed=elapsed,
        )


def expand_uuid(value: str) -> str:
    """Expand a short UUID ("aa03") to its full 128-bit form."""
    short = value.strip().lower().removeprefix("0x")
    if len(short) == 4:
        return f"0000{short}-0000-1000-8000-00805f9b34fb"
    return short


def reverse_mac(mac: str) -> bytes:
    """AA:BB:CC:DD:EE:FF -> b'\\xff\\xee\\xdd\\xcc\\xbb\\xaa'."""
    return bytes(int(part, 16) for part in reversed(mac.split(":")))


def mix_a(mac: bytes, product_id: int) -> bytes:
    """First mixing function of the Mi auth handshake."""
    return bytes(
        [
            mac[0],
            mac[2],
            mac[5],
            product_id & 0xFF,
            product_id & 0xFF,
            mac[4],
            mac[5],
            mac[1],
        ]
    )


def mix_b(mac: bytes, product_id: int) -> bytes:
    """Second mixing function (used to verify the kettle's response)."""
    return bytes(
        [
            mac[0],
            mac[2],
            mac[5],
            (product_id >> 8) & 0xFF,
            mac[4],
            mac[0],
            mac[5],
            product_id & 0xFF,
        ]
    )


def _cipher_init(key: bytes) -> bytearray:
    perm = bytearray(range(256))
    key_len = len(key)
    j = 0
    for i in range(256):
        j = (j + perm[i] + key[i % key_len]) & 0xFF
        perm[i], perm[j] = perm[j], perm[i]
    return perm


def cipher(key: bytes, payload: bytes) -> bytes:
    """RC4 variant used by the Mi BLE auth handshake."""
    perm = _cipher_init(key)
    index1 = 0
    index2 = 0
    output = bytearray()
    for byte in payload:
        index1 = (index1 + 1) & 0xFF
        index2 = (index2 + perm[index1]) & 0xFF
        perm[index1], perm[index2] = perm[index2], perm[index1]
        idx = (perm[index1] + perm[index2]) & 0xFF
        output.append(byte ^ perm[idx])
    return bytes(output)


class MiKettleClient:
    """Short-lived (connect -> act -> disconnect) client for the kettle."""

    def __init__(self, address: str, product_id: int, token: bytes) -> None:
        """Initialise the client for one kettle."""
        self._address = address
        self._product_id = product_id
        self._token = token
        self._reversed_mac = reverse_mac(address)

    async def async_read_state(self, device: BLEDevice) -> MiKettleState:
        """Connect, read the state and disconnect right away."""
        return await self.async_apply(device)

    async def async_apply(
        self,
        device: BLEDevice,
        *,
        keep_warm: tuple[int, int] | None = None,
        half_hours: int | None = None,
        turn_off_after_boil: bool | None = None,
    ) -> MiKettleState:
        """Write the given settings and read the state in the same connection.

        One connection per operation keeps the kettle occupied as briefly as
        possible, which matters because it accepts a single connection only.
        """
        if keep_warm is not None:
            keep_warm_type, temperature = keep_warm
            if keep_warm_type not in KEEP_WARM_TYPE_MAP:
                raise ValueError(f"invalid keep-warm type: {keep_warm_type}")
            if not TEMP_MIN <= temperature <= TEMP_MAX:
                raise ValueError(
                    f"temperature out of range {TEMP_MIN}-{TEMP_MAX}: {temperature}"
                )
        if half_hours is not None and not 0 <= half_hours <= TIME_LIMIT_MAX_HALF_HOURS:
            raise ValueError(
                f"time limit out of range 0-{TIME_LIMIT_MAX_HALF_HOURS}: {half_hours}"
            )

        async with self._session(device) as client:
            if keep_warm is not None:
                await client.write_gatt_char(
                    UUID_SETUP, bytes([keep_warm[0], keep_warm[1]]), response=True
                )
            if half_hours is not None:
                await client.write_gatt_char(
                    UUID_TIME_LIMIT, bytes([half_hours]), response=True
                )
            if turn_off_after_boil is not None:
                await client.write_gatt_char(
                    UUID_BOIL_MODE, bytes([1 if turn_off_after_boil else 0]), response=True
                )
            state = await self._read_status(client)
            await self._read_settings(client, state)
            return state

    async def async_probe(
        self,
        device: BLEDevice,
        characteristic: str,
        payload: bytes | None = None,
        listen_seconds: float = 3.0,
    ) -> dict[str, object]:
        """Subscribe to a characteristic and optionally write to it.

        Without a payload this is completely safe (notifications only). With a
        payload it is an experiment on a live appliance - call it deliberately.
        """
        uuid = expand_uuid(characteristic)
        received: list[bytes] = []

        def _on_notify(_characteristic, data: bytearray) -> None:
            received.append(bytes(data))

        async with self._session(device) as client:
            notifying = False
            try:
                await client.start_notify(uuid, _on_notify)
                notifying = True
            except (BleakError, EOFError, ValueError) as err:
                _LOGGER.debug("Cannot subscribe to %s: %s", uuid, err)

            if payload is not None:
                _LOGGER.warning(
                    "MiKettle diagnostics: writing %s to %s", payload.hex(), uuid
                )
                await client.write_gatt_char(uuid, payload, response=True)

            if listen_seconds:
                await asyncio.sleep(listen_seconds)

            if notifying:
                try:
                    await client.stop_notify(uuid)
                except (BleakError, EOFError):
                    pass

            state = await self._read_status(client)
            await self._read_settings(client, state)

        return {
            "characteristic": uuid,
            "written": payload.hex() if payload is not None else None,
            "notifications": [item.hex() for item in received],
            "status": {
                "action": state.action,
                "mode": state.mode,
                "current_temperature": state.current_temperature,
                "target_temperature": state.target_temperature,
                "keep_warm_type": state.keep_warm_type,
                "keep_warm_limit": state.keep_warm_limit,
                "turn_off_after_boil": state.turn_off_after_boil,
            },
        }

    async def async_dump_characteristics(self, device: BLEDevice) -> dict[str, str]:
        """Dump every characteristic and its value.

        Used to hunt for settings the public protocol does not cover (for example
        "Extended Warm Up" in the official app) by diffing before/after.
        """
        dump: dict[str, str] = {}
        async with self._session(device) as client:
            for service in client.services:
                for char in service.characteristics:
                    key = f"{service.uuid}/{char.uuid}"
                    if "read" not in char.properties:
                        dump[key] = f"<{','.join(char.properties)}>"
                        continue
                    try:
                        value = await client.read_gatt_char(char)
                        dump[key] = f"{value.hex()} <{','.join(char.properties)}>"
                    except (BleakError, EOFError, asyncio.TimeoutError) as err:
                        dump[key] = f"<read error: {err}>"
        return dump

    # ---- internals ----

    def _session(self, device: BLEDevice) -> _Session:
        return _Session(self, device)

    async def _authenticate(self, client: BleakClientWithServiceCache) -> None:
        """Run the Mi auth handshake. No cloud call is involved."""
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _on_auth(_characteristic, data: bytearray) -> None:
            queue.put_nowait(bytes(data))

        await client.write_gatt_char(UUID_AUTH_INIT, KEY1, response=True)
        await client.start_notify(UUID_AUTH, _on_auth)
        try:
            await client.write_gatt_char(
                UUID_AUTH,
                cipher(mix_a(self._reversed_mac, self._product_id), self._token),
                response=True,
            )
            try:
                response = await asyncio.wait_for(queue.get(), timeout=AUTH_TIMEOUT)
            except asyncio.TimeoutError as err:
                raise MiKettleAuthError(
                    "the kettle did not respond to authentication"
                ) from err

            expected = cipher(
                mix_b(self._reversed_mac, self._product_id),
                cipher(mix_a(self._reversed_mac, self._product_id), response),
            )
            if expected != self._token:
                # Keep going, but this almost always means a wrong product ID and
                # the next GATT operation will fail with ATT 0x0E.
                _LOGGER.warning(
                    "Auth response verification mismatch (expected %s, got %s) - "
                    "continuing, but check the configured product ID",
                    self._token.hex(),
                    expected.hex(),
                )

            await client.write_gatt_char(UUID_AUTH, cipher(self._token, KEY2), response=True)
            await client.read_gatt_char(UUID_VERSION)
        finally:
            try:
                await client.stop_notify(UUID_AUTH)
            except (BleakError, EOFError):
                pass

    async def _read_status(self, client: BleakClientWithServiceCache) -> MiKettleState:
        """Wait for a single status notification on aa02."""
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def _on_status(_characteristic, data: bytearray) -> None:
            queue.put_nowait(bytes(data))

        await client.start_notify(UUID_STATUS, _on_status)
        try:
            try:
                raw = await asyncio.wait_for(queue.get(), timeout=STATUS_TIMEOUT)
            except asyncio.TimeoutError as err:
                raise MiKettleError("the kettle sent no status notification") from err
            _LOGGER.debug("Kettle status: %s", raw.hex())
            return MiKettleState.from_status(raw)
        finally:
            try:
                await client.stop_notify(UUID_STATUS)
            except (BleakError, EOFError):
                pass

    async def _read_settings(
        self, client: BleakClientWithServiceCache, state: MiKettleState
    ) -> None:
        """Add the settings that are not part of the status packet (aa04, aa05).

        Not readable on every firmware - in that case they are skipped silently
        and the values stay as last written.
        """
        try:
            raw = await client.read_gatt_char(UUID_TIME_LIMIT)
            if raw:
                state.keep_warm_limit = raw[0] / 2
        except (BleakError, EOFError, asyncio.TimeoutError) as err:
            _LOGGER.debug("aa04 (keep-warm time limit) is not readable: %s", err)

        try:
            raw = await client.read_gatt_char(UUID_BOIL_MODE)
            if raw:
                state.turn_off_after_boil = bool(raw[0])
        except (BleakError, EOFError, asyncio.TimeoutError) as err:
            _LOGGER.debug("aa05 (boil mode) is not readable: %s", err)


class _Session:
    """Async context manager: connect + authenticate, always disconnect."""

    def __init__(self, kettle: MiKettleClient, device: BLEDevice) -> None:
        self._kettle = kettle
        self._device = device
        self._client: BleakClientWithServiceCache | None = None

    async def __aenter__(self) -> BleakClientWithServiceCache:
        """Connect and authenticate."""
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._device,
                self._device.name or self._device.address,
                max_attempts=2,
            )
        except (BleakError, asyncio.TimeoutError) as err:
            text = str(err)
            if "connection slot" in text or "Failed to connect" in text:
                # Usually a phone (the Xiaomi app) holds the single connection, or
                # the kettle has been taken off its base.
                raise MiKettleBusyError(text) from err
            raise MiKettleError(text) from err
        self._client = client
        try:
            await self._kettle._authenticate(client)  # noqa: SLF001
        except Exception:
            await self._disconnect()
            raise
        return client

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        """Always disconnect - otherwise the Xiaomi app cannot connect."""
        await self._disconnect()

    async def _disconnect(self) -> None:
        if self._client is None:
            return
        client, self._client = self._client, None
        try:
            await client.disconnect()
        except (BleakError, EOFError) as err:
            _LOGGER.debug("Error while disconnecting: %s", err)
