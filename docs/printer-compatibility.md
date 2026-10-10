# Printer compatibility - which printers AccessSlice can send to and control

Researched 2026-09-21 (web sources at the end). AccessSlice talks to
printers over exactly two protocols, so a printer is compatible when it
speaks one of them on the local network:

- PrusaLink (app/prusalink.py, app/printer_control.py job mode): HTTP,
  /api/v1/files/usb upload and start, /api/v1/status, X-Api-Key or
  digest auth as user "maker". Control = tiny G-code files printed one
  at a time; works only while the printer is idle.
- Creality LAN (app/creality_lan.py, direct mode): HTTP upload to
  /upload/<name>.gcode and a JSON WebSocket on port 9999. Control = real
  G-code lines, status pushed live.

Bambu printers are NOT controlled by AccessSlice: the Bambu backend hands
the sliced file to Bambu Studio, whose own send (Ctrl+Shift+G) does the
network part. Any Bambu printer Studio can send to therefore works for
sending, but the control, status and files pages do not apply.

## Prusa (PrusaLink) - built in, no extra hardware

| Printer | Notes |
| --- | --- |
| CORE One, CORE One+, CORE One L | Tested (Core One). |
| MK4, MK4S | Same Buddy firmware and API as the Core One. |
| MK3.9, MK3.9S | Same. |
| MK3.5, MK3.5S | Same. |
| XL (all sizes) | Same API; tool changer untested. |
| MINI, MINI+ | Needs Prusa's Wi-Fi module or Ethernet; then the same API. |
| Prusa Pro HT90 | PrusaLink built in; untested. |
| SL1, SL1S SPEED | Resin, PrusaLink built in, but the storage name and job flow differ; untested and unlikely to work unchanged. |

## Prusa (PrusaLink) - with a Raspberry Pi

| Printer | Notes |
| --- | --- |
| MK3, MK3S, MK3S+ | PrusaLink runs on a Pi Zero 2 W / Zero W (GPIO) or Pi 3/4/5 (USB). Prusa-Link on the Pi serves the same /api/v1/files, /api/v1/status and /api/v1/job routes with digest auth (user maker + password), which prusalink.py already falls back to. Should work; untested. |
| MK2.5, MK2.5S | Same as above over USB. |

Anything older or third-party running Marlin without PrusaLink: no.

## Creality (LAN protocol, port 9999) - stock firmware, no rooting

Confirmed by community integrations (ha_creality_ws, Homey Creality app,
Creality-Control, home-assistant-creality) as speaking the same port 9999
WebSocket that creality_lan.py uses:

| Printer | Notes |
| --- | --- |
| K2 Plus | Tested (send, status, control). |
| K2, K2 Pro | Same K2 firmware family; CFS box present. |
| K1, K1C, K1C 2025, K1 SE, K1 Max | Same protocol; gcode path is /usr/data/printer_data/gcodes (creality_lan.py already handles it). No CFS. |
| Ender-3 V3, Ender-3 V3 KE, Ender-3 V3 Plus | Creality OS (Klipper) bed slingers, same protocol via the K1 driver. Cheapest way to a second test printer. |
| Creality Hi, Hi Combo | Same protocol; the Combo has a CFS. |

Probable but not verified by any source found:

| Printer | Notes |
| --- | --- |
| Ender-5 Max | Creality OS (Klipper), LAN printing in Creality Print, no browser UI on stock firmware. Almost certainly port 9999; nobody has published a capture. |
| Nebula Pad (Ender-3 V3 SE, Ender-3, V2, S1, Max Neo) | Runs Creality OS and appears in Creality Print as a LAN device. Likely the same protocol; unverified. |

## Creality - NOT compatible

| Printer | Why |
| --- | --- |
| Ender-3 V3 SE (alone) | Marlin, no network at all. |
| CR-6 SE, CR-10 Smart, CR-10 Smart Pro, Sermoon V1/V1 Pro, Sermoon D1/D3/D3 Pro, CR-M4 | Marlin era; their LAN used the old Creality WiFi Box / cloud protocol, not port 9999. Slicing profiles exist in Creality Print, sending does not work. |
| SPARKX i7 (Combo) | Klipper, but a different LAN API: WebSocket on port 80 (/ws) plus Moonraker on 7125. Would need a new backend. |
| Halot resin printers | Port 18188, a different protocol. |
| Sonic Pad conversions | Moonraker (port 7125), which AccessSlice does not speak. |
| Any rooted printer with the Creality services removed | Port 9999 disappears with them. |

## Everything else

Klipper/Moonraker printers (Voron, Sovol, Qidi, Elegoo, Anycubic Kobra
with Klipper, Flashforge, rooted Crealitys with Fluidd) share one open
HTTP API on port 7125. AccessSlice has no Moonraker backend today; adding
one would cover all of them at once and is the single biggest
compatibility win left.

## Sources

- Prusa Knowledge Base, "Prusa Connect and PrusaLink explained":
  https://help.prusa3d.com/article/prusa-connect-and-prusalink-explained_302608
- prusa3d/Prusa-Link (Pi PrusaLink) main.py and files.py routes:
  https://github.com/prusa3d/Prusa-Link
- 3dg1luk43/ha_creality_ws README (model list):
  https://github.com/3dg1luk43/ha_creality_ws
- Homey Creality app (K1/K2/Ender-3 V3/Hi on port 9999; CR-M4 via Moonraker):
  https://homey.app/en-us/app/com.creality/Creality/
- SiloCityLabs/Creality-Control (9999 vs 18188):
  https://github.com/SiloCityLabs/Creality-Control
- filipeamorimdev/home-assistant-creality (Ender-3 V3 KE / K1-class, port 9999):
  https://github.com/filipeamorimdev/home-assistant-creality
- jingjoks/ha-creality-sparkx (SPARKX i7 uses port 80 /ws and 7125):
  https://github.com/jingjoks/ha-creality-sparkx
- Creality forum, Ender-5 Max IP access thread:
  https://forum.creality.com/t/ip-acces/39553
- SimplyPrint Ender-5 Max guide (Klipper firmware, LAN):
  https://simplyprint.io/setup-guide/creality/ender-5-max
