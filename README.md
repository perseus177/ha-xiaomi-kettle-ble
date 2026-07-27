# Xiaomi Kettle BLE

Local Bluetooth control of the **Xiaomi / Viomi Mi Smart Kettle** from Home Assistant —
not just reading the temperature, but **writing the settings** the official Mi Home app
offers: keep-warm temperature, keep-warm mode, keep-warm time limit and "do not boil again".

No cloud, no Xiaomi account at runtime, no MQTT bridge. Everything runs over the
Home Assistant Bluetooth stack (built-in adapter or an ESPHome Bluetooth proxy).

> ### ⚠️ Read this before you install
>
> The Mi auth handshake used by this kettle is a **pairing** operation: the kettle stores
> the token it is given and the previous owner loses access. If you let this integration
> generate its own token, **the official Mi Home app will stop connecting** to your kettle.
>
> To keep both working, extract the kettle's token from the Mi Home cloud with
> [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor)
> and paste it into the config flow (`TOKEN:` line, 24 hex characters). If you already
> generated a random one, simply reconfigure with the cloud token and refresh once —
> the kettle pairs back and the app works again.
>
> **Also:** you cannot start boiling remotely. The kettle only heats after its physical
> button is pressed; this integration configures parameters, it does not switch it on.

## Supported devices

| Model | Product ID | Status |
|---|---|---|
| `yunmi.kettle.v1` | 131 | **tested**, sold as YM-K1501 |
| `yunmi.kettle.v2` | 275 | should work, untested |
| `yunmi.kettle.v7` | 1116 | should work, untested |

The product ID **must match your kettle**, because it is part of the auth handshake —
a wrong value fails with `ATT 0x0E (Unlikely Error)`. Take it from the `MODEL:` line of
Xiaomi-cloud-tokens-extractor, or read characteristic `2a24` from the device. Note that
being sold as "YM-K1501" does **not** imply `v2`.

## Requirements

- Home Assistant **2026.3** or newer
- A Bluetooth adapter reachable from Home Assistant, or an ESPHome Bluetooth proxy with
  active connections near the kettle
- The kettle on its base (off the base it does not advertise and cannot be connected to).
  Note that even *on* its base an idle kettle advertises only rarely — see
  [Troubleshooting](#troubleshooting).

## Tested on

Everything in this repository was developed and verified against exactly one setup:

| | |
|---|---|
| Home Assistant | **2026.7.4**, Home Assistant OS 18.1, Supervisor 2026.07.3 |
| Hardware | **Raspberry Pi 4, 8 GB** |
| Bluetooth | **built-in adapter of the Pi 4** (Raspberry Pi Trading `bcm43438-bt`), no proxy |
| Kettle | **Xiaomi / Viomi Mi Smart Kettle**, sold as **YM-K1501** |
| Kettle model ID | `yunmi.kettle.v1` → product ID **131** (characteristic `2a24`) |
| Kettle firmware | **6.2.1.6** (as shown in the Mi Home app; characteristic `2a28`) |
| Other version strings on the device | `2a26` = `7.5.10`, `2a25` = `1.3.3-LE`, `2a27` = `0.1.8`, manufacturer `Viomi` |

If your kettle reports different values, the integration may still work — the product ID is
the only value that must match — but nothing beyond the setup above has been verified.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → *Custom repositories*
2. Repository: `https://github.com/perseus177/ha-xiaomi-kettle-ble`, category: *Integration*
3. Download, restart Home Assistant
4. The kettle is discovered automatically → *Settings → Devices & Services*

### Manual

Copy `custom_components/mikettle_ble/` into your `config/custom_components/` and restart.

## Setup

The integration discovers the kettle from its BLE advertisement, so you do not need to
know its MAC address or model — both are read from the advertisement. Leave the token
field empty only if you will never use the Mi Home app again (see the warning above).

## Entities

| Entity | What it does |
|---|---|
| `number` Keep warm temperature | 40–95 °C (the app limits it to 90) |
| `select` Keep warm mode | *Cool down to the set temperature* / *Warm up to the set temperature* |
| `select` Temperature preset | the app's presets (90 coffee, 80 white tea, 70 rice flour, 50 milk, 40 probiotics) |
| `number` Keep warm time limit | 0–12 h in half-hour steps |
| `switch` Turn off after boil | the app's "Do not boil again" |
| `sensor` × 5 | water temperature, target temperature, action, mode, keep-warm elapsed |
| `button` Refresh state | one short connection on demand |
| `button` Dump characteristics | diagnostics into the log |
| `switch` Bluetooth pause | kill switch: while on, the integration never connects |

## How it behaves (and why)

- **No persistent connection.** The kettle accepts a single GATT connection at a time, so
  every operation is `connect → authenticate → write + read → disconnect`. While Home
  Assistant is connected (~2 s), the phone app cannot connect, and vice versa.
- **Polling is off by default** (`0` in the integration options). State is read at startup,
  after every write, and when you press *Refresh state*. Enable polling only if you accept
  that the kettle is occupied for ~2 s per poll.
- **Writes are optimistic.** The requested value is published immediately and replaced by
  the value read back from the kettle when the session finishes (rolled back on failure) —
  a full session takes 6–20 s depending on how quickly the kettle accepts the connection.
- **Bluetooth pause** survives restarts (stored in the config entry options), including the
  state read that would otherwise happen during setup.
- For live temperature and status without occupying the kettle at all, run a passive
  receiver alongside this integration — for example
  [Passive BLE Monitor](https://github.com/custom-components/ble_monitor). It never
  connects, so it does not compete for the single connection slot.

## Troubleshooting

### "Kettle … has not advertised for N s"

**This is the most common failure, and the kettle usually looks perfectly fine on its base.**

An idle, cooled-down kettle advertises only rarely — minutes can pass between two
advertisements. Home Assistant refuses to even *attempt* a connection to a device that is
not in its recent connectable history, so the operation fails before any Bluetooth traffic
happens. On a weak signal (this was reproduced at **−84 dBm** on the Pi 4's built-in
adapter) the kettle can disappear from the stack's device list entirely.

Observed on the test setup: the last advertisement arrived at 00:58, an operation at 01:04
failed with `last advertisement 345s ago`, and the same operation succeeded on the first
try immediately after the kettle was woken up.

- **Fix now:** take the kettle off its base and put it back (or press its button), then run
  the operation within a few seconds.
- **Fix permanently:** put an ESPHome Bluetooth proxy with active connections enabled near
  the kettle. This does not make the kettle advertise more often, but it raises the signal
  enough that the advertisements it *does* send are received reliably.

This is not a busy device and not a bug in the integration — the message is separate from
the "another client is connected" one precisely so the two are not confused.

### "Kettle … refused the connection"

The kettle accepts one GATT connection at a time. Close the Mi Home app (or wait for it to
disconnect) and try again in half a minute.

### Authentication fails with `ATT 0x0E`

The configured product ID does not match the kettle. See [Supported kettles](#supported-kettles).

## Diagnostics

`mikettle_ble.probe_characteristic` subscribes to a characteristic and optionally writes
to it, returning notifications and the resulting state as a service response. Without a
payload it is a safe read; with a payload it is a deliberate experiment on a live appliance.

**Known open question:** the app's *Extended Warm Up* setting is not exposed on any
readable characteristic — toggling it in the app changes none of `aa01`, `aa04`, `aa05`.
The only remaining write channel is `aa03` (`write,notify`, not readable), which also emits
16-byte frames on its own. If you manage to capture what the app writes there (Android
HCI snoop log), a pull request is very welcome.

## Protocol

Reverse engineering credit goes to [aprosvetova/xiaomi-kettle](https://github.com/aprosvetova/xiaomi-kettle).

| Characteristic | Meaning |
|---|---|
| `0010` / `0001` / `0004` | auth handshake (`90 CA 85 DE`, RC4-style cipher over `mixA/mixB`, `92 AB 54 FA`) |
| `aa01` | `[keep warm type, temperature]` — type `0` = boil then cool down, `1` = heat to temperature |
| `aa02` | status notification, 12 bytes |
| `aa04` | keep-warm time limit, 1 byte = hours × 2 |
| `aa05` | "do not boil again", 1 byte |

Other implementations that helped: [drndos/mikettle](https://github.com/drndos/mikettle),
[devbis/ble2mqtt](https://github.com/devbis/ble2mqtt),
[Hypfer/Cybele](https://github.com/Hypfer/Cybele). Note that Cybele maps `aa05` to a
keep-warm refill mode; on `yunmi.kettle.v1` it is the "do not boil again" flag instead.

## Authorship

This integration — code, translations, documentation and the protocol work needed to make
the writes work — was **written by an AI model, Anthropic Claude Opus 5**, while debugging
a real kettle on the setup listed above. Bug reports and pull requests are welcome; keep in
mind there is no dedicated human maintainer behind it, and the only hardware it has ever
run on is the one in *Tested on*.

## License

MIT — see [LICENSE](LICENSE).
