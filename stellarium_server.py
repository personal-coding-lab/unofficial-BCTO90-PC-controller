"""Stellarium telescope-control TCP protocol v1 bridge."""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass


GOTO_PACKET_SIZE = 20
POSITION_PACKET_SIZE = 24


@dataclass(frozen=True)
class GotoTarget:
    ra_hours: float
    dec_degrees: float
    timestamp_micros: int


def decode_goto(packet: bytes) -> GotoTarget:
    if len(packet) < GOTO_PACKET_SIZE:
        raise ValueError("Stellarium GoTo packet is too short")
    length, packet_type, timestamp, ra_raw, dec_raw = struct.unpack_from("<HHQIi", packet)
    if length != GOTO_PACKET_SIZE or packet_type != 0:
        raise ValueError(f"unsupported Stellarium packet: length={length}, type={packet_type}")
    ra_hours = (ra_raw / 2**32) * 24.0
    dec_degrees = (dec_raw / 2**31) * 180.0
    return GotoTarget(ra_hours, dec_degrees, timestamp)


def encode_position(ra_hours: float, dec_degrees: float, status: int = 0) -> bytes:
    timestamp = int(time.time() * 1_000_000)
    ra_raw = round((ra_hours % 24.0) / 24.0 * 2**32) & 0xFFFFFFFF
    dec_raw = round(max(-180.0, min(180.0, dec_degrees)) / 180.0 * 2**31)
    dec_raw = max(-(2**31), min(2**31 - 1, dec_raw))
    return struct.pack("<HHQIii", POSITION_PACKET_SIZE, 0, timestamp, ra_raw, dec_raw, status)


class StellariumServer:
    def __init__(self, emit, host: str = "127.0.0.1", port: int = 10001) -> None:
        self.emit = emit
        self.host = host
        self.port = port
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._position_lock = threading.Lock()
        # Stellarium marks a remote telescope initialized only after the first
        # position packet. Use an explicit "not aligned" status until a real
        # aligned position is supplied by the GUI.
        self._position: tuple[float, float, int] = (0.0, 0.0, 1)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def set_position(self, ra_hours: float, dec_degrees: float) -> None:
        with self._position_lock:
            self._position = (ra_hours, dec_degrees, 0)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="stellarium-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    def _serve(self) -> None:
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server = server
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(2)
            server.settimeout(0.5)
            self.emit("stellarium_status", f"대기 중 {self.host}:{self.port}")
            while not self._stop.is_set():
                try:
                    client, address = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._client_loop,
                    args=(client, address),
                    name=f"stellarium-{address[1]}",
                    daemon=True,
                ).start()
        except Exception as exc:
            self.emit("error", f"Stellarium 서버 오류: {type(exc).__name__}: {exc}")
        finally:
            self.emit("stellarium_status", "중지됨")

    def _client_loop(self, client: socket.socket, address) -> None:
        self.emit("stellarium_client", f"연결됨 {address[0]}:{address[1]}")
        buffer = bytearray()
        client.settimeout(0.25)
        last_position = 0.0
        try:
            with client:
                while not self._stop.is_set():
                    try:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                    except TimeoutError:
                        pass
                    while len(buffer) >= 2:
                        packet_length = int.from_bytes(buffer[:2], "little")
                        if packet_length < 4 or packet_length > 1024:
                            raise ValueError(f"잘못된 Stellarium packet length: {packet_length}")
                        if len(buffer) < packet_length:
                            break
                        packet = bytes(buffer[:packet_length])
                        del buffer[:packet_length]
                        if packet_length == GOTO_PACKET_SIZE:
                            self.emit("stellarium_goto", decode_goto(packet))
                    now = time.monotonic()
                    if now - last_position >= 0.5:
                        with self._position_lock:
                            position = self._position
                        client.sendall(encode_position(*position))
                        last_position = now
        except Exception as exc:
            self.emit("log", f"Stellarium client 오류: {type(exc).__name__}: {exc}")
        finally:
            self.emit("stellarium_client", "연결 해제")
