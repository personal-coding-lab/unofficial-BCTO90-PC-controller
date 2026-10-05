# Compatibility

## Verified

| Component | Status | Evidence |
|---|---|---|
| BCTO90 / DS20090 mount | Verified on one physical unit | BLE initialization reply, CRC-valid frames, manual movement, stop commands, and motor-position reports were observed on the actual mount. |
| Windows 11 x64 | Verified on the development PC | Python 3.13, Bleak 3.0.2, Bluetooth LE, GUI startup, and local Stellarium TCP connection were tested. |
| Stellarium TCP telescope protocol v1 | Software-tested | Packet encode/decode tests and a local TCP client connection passed. |

## Expected but not physically verified

| Component | Status | Notes |
|---|---|---|
| Python 3.10–3.13 on the GitHub Windows runner | CI target | Unit tests cover protocol, alignment, and Stellarium packet handling. This is not a physical Windows 11 Bluetooth test. |
| Other BCTO90/DS20090 production revisions | Unverified | Firmware or motor-axis mapping may differ. Start at speed level 1 and verify both axes. |
| Windows 10 | Unsupported/unverified | Bleak 3.0.2 currently declares Windows 11 support. It may work, but this project does not claim it. |

## Not compatible unless separately proven

- Classic Bluetooth SPP, COM-port, HBX, LX200, ASCOM, and INDI connections.
- A telescope that merely exposes Nordic UART Service UUIDs. Matching BLE UUIDs
  do not prove that its command framing, CRC, motor numbering, or safety limits
  match BCTO90.
- macOS, Linux, Android, and iOS. The GUI and launcher are Windows-specific.

When a selected BLE device name does not contain `BCTO` or `DS20090`, the GUI
shows a warning and requires explicit confirmation before connecting.
