# BCTO90 (DS20090) Bluetooth Controller

[![tests](https://github.com/personal-coding-lab/unofficial-BCTO90-PC-controller/actions/workflows/tests.yml/badge.svg)](https://github.com/personal-coding-lab/unofficial-BCTO90-PC-controller/actions/workflows/tests.yml)

> This is an unofficial, open-source compatibility project. It is not official software affiliated with or endorsed by the manufacturer, distributor, Meade, or Stellarium. Product names are used only to identify compatible equipment. See [NOTICE.md](NOTICE.md) for details.

This program uses Bluetooth LE only. It does not include serial-port, COM-port, HBX/LX200, or `pyserial` code.

## Compatibility Scope

- Hardware verified: one BCTO90/DS20090 mount, Windows 11 x64, and Python 3.13.
- Automated tests: Windows 11 and Python 3.10–3.13.
- Not verified: BCTO90/DS20090 units from other production periods or Windows 10.
- Incompatible or unsupported: generic Nordic UART devices, telescopes from other manufacturers, macOS, Linux, ASCOM, INDI, HBX/LX200, and COM ports.
- A matching BLE UUID alone does not establish that a device uses the same commands, CRC, or motor mapping. A warning is shown before connecting to devices whose name is not identified as `BCTO` or `DS20090`.

See [COMPATIBILITY.md](COMPATIBILITY.md) for supporting details and limitations.

## Running the Program and Connecting over BLE

1. Move the three-position switch on the rear of the unit fully down to the **lowest Bluetooth-icon position**—not to `I` or `O`.
2. Close the BCTO app on your phone completely. No other device may be connected to `BCTO 90`.
3. Install Python 3.10 or later. An internet connection is required on the first run to install Bleak.
4. Double-click `run.exe`.
5. Click **Scan**, select the device named exactly `BCTO 90`, then click **Connect**. Initialization queries are retried automatically up to twice.
6. A real connection requires both `BCTO protocol response confirmed` and `FRAME OK`. `GATT LINK` or `BLE link only` alone indicates a failed connection.
7. Start with motor speed level `1` and press each direction briefly to confirm motion.

## Motion, Speed, and Direction Reversal

- Pressing a direction button or key sends one motion command; releasing it sends a stop command. Repeated motion commands are not sent.
- Use the arrow keys or `W/A/S/D` for keyboard control. `Space` or `Esc` performs an emergency stop on both axes.
- Set the speed with the numeric entry, the number row, or numeric-keypad `1`–`9`. Level 1 is the slowest at 1%, and level 9 is the fastest at 100%. The numeric keypad works whether Num Lock is on or off.
- The output levels are `1, 2, 4, 8, 16, 32, 50, 75, 100%`.
- If the left/right and up/down axes are swapped, exchange the `0/1` assignments for the `Azimuth-axis motor` and `Altitude-axis motor`. If only a direction is reversed, use `Reverse left/right` or `Reverse up/down`. Both reversal options are off by default.
- The hardware-verified BCTO90 defaults are `Azimuth (left/right) = motor 1` and `Altitude (up/down) = motor 0`.

## Stellarium

- The program accepts a Stellarium TCP connection and the target coordinates of the selected object at `127.0.0.1:10001`.
- With Stellarium closed, run `configure_stellarium.ps1` once to register `BCTO90 BLE Bridge`.
- Select an object in Stellarium and press `Ctrl+1`; its RA/Dec appears in the application log and UI.
- Start in this order: run `run.exe` → confirm TCP is `Listening` → start Stellarium.

## Multi-Point Alignment and GoTo

1. Enter the observing site's latitude and longitude in decimal degrees. For South Korea, both values are positive.
2. In the program, click `Wait for Stellarium alignment-star selection`.
3. In Stellarium, select the first bright star and press `Ctrl+1`. This selection is stored only for alignment; it does not trigger automatic motion even if `GoTo immediately on Ctrl+1 reception` is enabled.
4. Use the manual controls to center the star in the eyepiece, then click `Save reference point after centering`.
5. Repeat steps 2–4 with a second star that differs by at least 5° in both azimuth and altitude. Basic GoTo is available from this point.
6. Add stars in other areas of the sky to improve accuracy. Up to eight points are supported; from three points, mount tilt and inter-axis error are also compensated, and RMS error is displayed.
7. Select the desired object, press `Ctrl+1`, and click `GoTo received target`.
8. Enable `GoTo immediately on Ctrl+1 reception` only after confirming manual GoTo. During motion, `Space`, `Esc`, or `Emergency stop` immediately stops both axes.

GoTo stops each axis using the `0x22` motor-position feedback confirmed in the APK and hardware logs. Remove an incorrectly registered point with `Undo last reference point`. Perform alignment again after restarting the program or changing the axis mapping. For safety, only altitudes from 3° to 85° are allowed.

## Verification Status and Limitations

- Verified: scanning for the `BCTO 90` BLE device and connecting through Nordic-UART-style GATT `6e400001/0002/0003`.
- Verified: initialization, response ACK, motion, and stop packet generation, cross-checked against an actual phone communication capture and the APK's native CRC table.
- Verified: the official app also enables the notification characteristic first and uses Write Without Response for the write characteristic.
- Fixed and hardware verified: a CRC mismatch in the previous program that caused the mount to ignore initialization commands.
- Hardware verified: mount response, CRC validation, response ACK, and status-frame reception after sending the PC initialization sequence `5A 06 36 00 00 11`.
- Confirmed in hardware logs: continuous motor 0/1 position values received in `0x22` frames. These are used for multi-point alignment and closed-loop GoTo stopping decisions.
- Fixed: GUI input handling that sent only a stop command instead of a motion command when a button was pressed.
- Fixed: removed the unverified repeated `0x17` command previously used at 100%; motion and stopping now consistently use the `0x18` motion and `0x16` stop commands verified in the official app.
- Manual motor motion is also blocked until a valid BCTO response has been confirmed.
- Communication logs are saved as UTF-8 in the `logs` directory next to the program.

## License and Safety

The project's own code is distributed under the [MIT License](LICENSE). It does not include the original APK, manufacturer firmware or source code, product photos, or catalog materials. Unattended telescope operation risks collisions and cable damage; for the first motion, always use low speed and observe the mount in person. This project does not guarantee against equipment damage or lost observing time.
