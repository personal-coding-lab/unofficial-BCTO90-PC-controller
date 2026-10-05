"""Independent BCTO90 BLE interoperability protocol implementation.

This module implements the wire behavior observed from a BCTO90/DS20090 mount.
It does not contain vendor application or firmware source code.  The wire
format is independent from Meade LX200/AutoStar serial commands.
"""

from __future__ import annotations

from dataclasses import dataclass


FRAME_MARKER = 0x5A
MIN_FRAME_SIZE = 5
MAX_FRAME_SIZE = 21

CMD_ACK = 0x01
CMD_RESPONSE = 0x02
CMD_RECOVER = 0x09
CMD_INITIALIZE = 0x11
CMD_CALIBRATE = 0x12
CMD_WRITE_CALIBRATION = 0x13
CMD_STOP = 0x16
CMD_MOVE_FULL_SPEED = 0x17
CMD_MOVE = 0x18
CMD_MOTOR_RESET = 0x1A
CMD_POSITION = 0x22

MAX_PROTOCOL_SPEED = 0x7B0000


def _crc16_buypass_byte(value: int) -> int:
    """Return one CRC-16/BUYPASS table entry (poly 0x8005)."""
    crc = value << 8
    for _ in range(8):
        crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


# The native APK contains a non-standard 256-entry table. Its high nibble is
# shifted by three bits before being combined with the low nibble; bit 3
# overlaps. Reproduce the table exactly instead of substituting a named CRC.
_APK_CRC_TABLE = tuple(
    _crc16_buypass_byte(((value & 0xF0) >> 1) + (value & 0x0F))
    for value in range(256)
)


def calculate_crc(data: bytes) -> int:
    """Calculate the exact checksum used by BCTO_CN.apk's native protocol."""
    crc = 0
    for value in data:
        index = value ^ ((crc & 0xFF00) >> 8)
        crc = _APK_CRC_TABLE[index] ^ ((crc << 8) & 0xFFFF)
    return crc


def encode_i24(value: int) -> bytes:
    if not -(1 << 23) <= value < (1 << 23):
        raise ValueError("24-bit signed integer out of range")
    return (value & 0xFFFFFF).to_bytes(3, "little")


def decode_i24(data: bytes) -> int:
    if len(data) != 3:
        raise ValueError("i24 requires exactly three bytes")
    value = int.from_bytes(data, "little")
    return value - (1 << 24) if value & (1 << 23) else value


def build_frame(sequence: int, command: int, data: bytes = b"") -> bytes:
    payload = bytes((sequence & 0xFF, command & 0xFF)) + bytes(data)
    length = len(payload) + 4
    if not MIN_FRAME_SIZE <= length <= MAX_FRAME_SIZE:
        raise ValueError(f"invalid BCTO frame length: {length}")
    crc = calculate_crc(payload)
    return bytes((FRAME_MARKER, length, crc & 0xFF, crc >> 8)) + payload


def build_initialize(sequence: int) -> bytes:
    return build_frame(sequence, CMD_INITIALIZE)


def build_ack(sequence: int) -> bytes:
    return build_frame(sequence, CMD_ACK, b"\x00\x00")


def build_stop(sequence: int, motor: int) -> bytes:
    _validate_motor(motor)
    return build_frame(sequence, CMD_STOP, bytes((motor,)))


def build_move(sequence: int, motor: int, negative: bool, speed_percent: int) -> bytes:
    """Build the APK's continuous manual-move command.

    The observed Android app uses CMD_MOVE with a signed 24-bit speed and
    target=0, then stops motion with CMD_STOP. Keep the same command at 100%
    instead of switching to the unverified CMD_MOVE_FULL_SPEED path.
    """
    _validate_motor(motor)
    if not 1 <= speed_percent <= 100:
        raise ValueError("speed_percent must be between 1 and 100")
    speed = max(10, round(MAX_PROTOCOL_SPEED * speed_percent / 100))
    signed_speed = -speed if negative else speed
    data = bytes((motor,)) + encode_i24(signed_speed) + b"\x00\x00" + encode_i24(0)
    return build_frame(sequence, CMD_MOVE, data)


def decode_motor_positions(frame: "Frame") -> tuple[int, int]:
    """Decode the two 32-bit motor counters from the mount's 0x22 report."""
    if not frame.crc_valid or frame.command != CMD_POSITION or len(frame.data) < 8:
        raise ValueError("not a valid BCTO motor-position frame")
    return (
        int.from_bytes(frame.data[0:4], "little"),
        int.from_bytes(frame.data[4:8], "little"),
    )


def _validate_motor(motor: int) -> None:
    if motor not in (0, 1):
        raise ValueError("motor must be 0 or 1")


@dataclass(frozen=True)
class Frame:
    raw: bytes
    sequence: int
    command: int
    data: bytes
    crc_valid: bool


class FrameParser:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buffer.extend(chunk)
        frames: list[Frame] = []
        while True:
            marker = self._buffer.find(FRAME_MARKER)
            if marker < 0:
                self._buffer.clear()
                break
            if marker:
                del self._buffer[:marker]
            if len(self._buffer) < 2:
                break
            length = self._buffer[1]
            if not MIN_FRAME_SIZE <= length <= MAX_FRAME_SIZE:
                del self._buffer[0]
                continue
            if len(self._buffer) < length:
                break
            raw = bytes(self._buffer[:length])
            del self._buffer[:length]
            payload = raw[4:]
            expected_crc = int.from_bytes(raw[2:4], "little")
            frames.append(
                Frame(
                    raw=raw,
                    sequence=payload[0],
                    command=payload[1] if len(payload) > 1 else -1,
                    data=payload[2:],
                    crc_valid=calculate_crc(payload) == expected_crc,
                )
            )
        return frames
