from __future__ import annotations

import asyncio
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

try:
    from bleak import BleakClient, BleakScanner
except ImportError as exc:  # pragma: no cover - shown as a GUI error at startup
    raise SystemExit("Bleak가 없습니다. run.ps1을 실행하거나 'pip install bleak'를 실행하세요.") from exc

from bcto_protocol import (
    CMD_POSITION,
    CMD_RESPONSE,
    FrameParser,
    build_ack,
    build_initialize,
    build_move,
    build_stop,
    decode_motor_positions,
)
from alignment import (
    AlignmentPoint,
    MultiStarAlignment,
    equatorial_to_horizontal,
    horizontal_to_equatorial,
)
from stellarium_server import GotoTarget, StellariumServer


NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_WRITE = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
SUPPORTED_DEVICE_TOKENS = ("BCTO", "DS20090")
SPEED_PERCENT_BY_LEVEL = (0, 1, 2, 4, 8, 16, 32, 50, 75, 100)
SPEED_KEY_LEVELS = {
    **{str(level): level for level in range(1, 10)},
    **{f"KP_{level}": level for level in range(1, 10)},
    "KP_End": 1,
    "KP_Down": 2,
    "KP_Next": 3,
    "KP_Left": 4,
    "KP_Begin": 5,
    "KP_Right": 6,
    "KP_Home": 7,
    "KP_Up": 8,
    "KP_Prior": 9,
}


@dataclass(frozen=True)
class DeviceInfo:
    address: str
    name: str
    rssi: int
    services: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.name or '(이름 없음)'}  [{self.address}]  {self.rssi} dBm"


class BleController:
    def __init__(self, emit: Callable[[str, object], None]) -> None:
        self.emit = emit
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        self.client: BleakClient | None = None
        self.write_uuid: str | None = None
        self.notify_uuid: str | None = None
        self.write_with_response = True
        self.sequence = 0
        self.parser = FrameParser()
        self.handshake_event: asyncio.Event | None = None
        self.write_lock: asyncio.Lock | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coroutine) -> None:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def done(result) -> None:
            try:
                result.result()
            except Exception as exc:
                self.emit("error", f"{type(exc).__name__}: {exc}")

        future.add_done_callback(done)

    async def scan(self) -> None:
        self.emit("status", "BLE 검색 중…")
        found = await BleakScanner.discover(timeout=8, return_adv=True)
        devices: list[DeviceInfo] = []
        for address, (device, advertisement) in found.items():
            services = tuple(value.lower() for value in advertisement.service_uuids)
            name = advertisement.local_name or device.name or ""
            devices.append(DeviceInfo(address, name, advertisement.rssi, services))
        devices.sort(key=lambda item: (_candidate_score(item), item.rssi), reverse=True)
        self.emit("devices", devices)
        self.emit("status", f"검색 완료: {len(devices)}개")

    async def connect(self, address: str) -> None:
        await self.disconnect()
        self.emit("status", f"연결 중: {address}")
        client = BleakClient(address, disconnected_callback=self._on_disconnected, timeout=15)
        await client.connect()
        write_uuid, notify_uuid, write_with_response = self._find_uart(client)
        self.client = client
        self.write_uuid = write_uuid
        self.notify_uuid = notify_uuid
        self.write_with_response = write_with_response
        self.sequence = 0
        self.parser = FrameParser()
        self.handshake_event = asyncio.Event()
        self.write_lock = asyncio.Lock()
        await client.start_notify(notify_uuid, self._on_notify)
        self.emit("connected", (address, write_uuid, notify_uuid))
        write_characteristic = client.services.get_characteristic(write_uuid)
        notify_characteristic = client.services.get_characteristic(notify_uuid)
        self.emit(
            "log",
            f"GATT MTU {client.mtu_size} · "
            f"WRITE PROPS {','.join(write_characteristic.properties) if write_characteristic else '?'} · "
            f"NOTIFY PROPS {','.join(notify_characteristic.properties) if notify_characteristic else '?'}",
        )
        self.emit("status", "BLE 링크 연결됨 — BCTO 프로토콜 자동 확인 중…")
        # The APK enables notifications before starting the telescope protocol.
        # Give Windows time to finish writing the CCCD, then retry the same task
        # once because the native implementation also has a retry path.
        await asyncio.sleep(1.2)
        initialize_packet = build_initialize(self.next_sequence())
        self.emit("log", "초기화 조회 1/2")
        await self.send_raw(initialize_packet)
        try:
            await asyncio.wait_for(self.handshake_event.wait(), timeout=3.0)
        except TimeoutError:
            self.emit("log", "초기화 응답 없음 — 동일 패킷 재시도 2/2")
            await self.send_raw(initialize_packet)
            try:
                await asyncio.wait_for(self.handshake_event.wait(), timeout=4.0)
            except TimeoutError:
                self.emit(
                    "protocol_failed",
                    "BLE 링크만 연결됨 — 본체 스위치를 Bluetooth 아이콘 위치로 "
                    "끝까지 내린 뒤 전원을 다시 켜세요. 초기화 응답(RX)이 없습니다.",
                )

    async def disconnect(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                if client.is_connected and self.notify_uuid:
                    await client.stop_notify(self.notify_uuid)
            except Exception:
                pass
            if client.is_connected:
                await client.disconnect()
        self.write_uuid = None
        self.notify_uuid = None
        self.handshake_event = None
        self.write_lock = None
        self.emit("disconnected", None)

    def _find_uart(self, client: BleakClient) -> tuple[str, str, bool]:
        exact_write = client.services.get_characteristic(NUS_WRITE)
        exact_notify = client.services.get_characteristic(NUS_NOTIFY)
        if exact_write and exact_notify:
            response = "write-without-response" not in exact_write.properties
            return NUS_WRITE, NUS_NOTIFY, response

        candidates: list[tuple[int, str, str, bool]] = []
        for service in client.services:
            writes = [
                characteristic
                for characteristic in service.characteristics
                if "write" in characteristic.properties
                or "write-without-response" in characteristic.properties
            ]
            notifies = [
                characteristic
                for characteristic in service.characteristics
                if "notify" in characteristic.properties or "indicate" in characteristic.properties
            ]
            if not writes or not notifies:
                continue
            score = 10 if service.uuid.lower().startswith("6e40") else 0
            for write in writes:
                for notify in notifies:
                    candidates.append(
                        (
                            score,
                            write.uuid,
                            notify.uuid,
                            "write-without-response" not in write.properties,
                        )
                    )
        if not candidates:
            raise RuntimeError("UART형 write/notify BLE 서비스를 찾지 못했습니다.")
        _score, write_uuid, notify_uuid, response = max(candidates)
        return write_uuid, notify_uuid, response

    def _on_disconnected(self, _client: BleakClient) -> None:
        self.client = None
        self.emit("disconnected", None)
        self.emit("status", "BLE 연결이 끊어졌습니다.")

    def _on_notify(self, _sender, value: bytearray) -> None:
        data = bytes(value)
        frames = self.parser.feed(data)
        if not frames or any(frame.command != CMD_POSITION for frame in frames):
            self.emit("log", f"RX  {data.hex(' ')}")
        for frame in frames:
            if frame.crc_valid and frame.command == CMD_POSITION:
                self.emit("motor_position", decode_motor_positions(frame))
            else:
                self.emit("frame", frame)
            if frame.crc_valid and self.handshake_event is not None:
                self.loop.call_soon_threadsafe(self.handshake_event.set)
            if frame.crc_valid and frame.command == CMD_RESPONSE:
                self.submit(self.send_raw(build_ack(frame.sequence)))

    async def send_raw(self, packet: bytes) -> None:
        if not self.client or not self.client.is_connected or not self.write_uuid:
            raise RuntimeError("BLE 장치가 연결되지 않았습니다.")
        if self.write_lock is None:
            raise RuntimeError("BLE 쓰기 채널이 준비되지 않았습니다.")
        async with self.write_lock:
            self.emit("log", f"TX  {packet.hex(' ')}")
            await self.client.write_gatt_char(
                self.write_uuid, packet, response=self.write_with_response
            )

    def next_sequence(self) -> int:
        result = self.sequence
        self.sequence = (self.sequence + 1) & 0xFF
        return result

    async def initialize(self) -> None:
        await self.send_raw(build_initialize(self.next_sequence()))

    async def stop_motor(self, motor: int) -> None:
        await self.send_raw(build_stop(self.next_sequence(), motor))

    async def stop_all(self) -> None:
        errors = []
        for motor in (0, 1):
            try:
                await self.stop_motor(motor)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    async def move(self, motor: int, negative: bool, speed_percent: int) -> None:
        packet = build_move(self.next_sequence(), motor, negative, speed_percent)
        await self.send_raw(packet)


def _candidate_score(device: DeviceInfo) -> int:
    score = 0
    upper_name = device.name.upper()
    if any(token in upper_name for token in ("BCTO", "DS20090")):
        score += 20
    if NUS_SERVICE in device.services:
        score += 20
    if any(uuid.startswith("6e40") for uuid in device.services):
        score += 10
    return score


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("BCTO90 (DS20090) Bluetooth Controller")
        self.geometry("920x920")
        self.minsize(840, 760)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.controller = BleController(self._emit)
        self.stellarium = StellariumServer(self._emit, port=10001)
        self.devices: dict[str, DeviceInfo] = {}
        self.connected = False
        self.verified_reply = False
        self.motion_inputs: dict[str, tuple[str, bool]] = {}
        self.last_target: GotoTarget | None = None
        self.alignment_target: GotoTarget | None = None
        self.alignment_selection_armed = False
        self.alignment = MultiStarAlignment()
        self.raw_motor_positions: tuple[int, int] | None = None
        self.unwrapped_motor_positions: list[float] | None = None
        self.goto_axes: dict[int, tuple[float, float]] = {}
        self.goto_deadline = 0.0
        self._last_position_display = 0.0
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(exist_ok=True)
        self.log_path = log_dir / time.strftime("session-%Y%m%d-%H%M%S.log")
        self._build_ui()
        self.stellarium.start()
        self.after(50, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="BCTO90 Bluetooth Controller", font=("Segoe UI", 13, "bold")).pack(
            side="left"
        )

        stellarium_frame = ttk.LabelFrame(outer, text="Stellarium 연동", padding=10)
        stellarium_frame.pack(fill="x")
        self.stellarium_status_var = tk.StringVar(value="TCP 서버 시작 중…")
        ttk.Label(stellarium_frame, textvariable=self.stellarium_status_var).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        self.target_var = tk.StringVar(value="수신한 목표 없음")
        ttk.Label(stellarium_frame, textvariable=self.target_var).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        ttk.Label(
            stellarium_frame,
            text="127.0.0.1:10001 · Stellarium에서 천체 선택 후 Ctrl+1",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        alignment_frame = ttk.LabelFrame(outer, text="다점 얼라인 · Stellarium GoTo", padding=10)
        alignment_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(alignment_frame, text="위도 (북 +)").grid(row=0, column=0, sticky="w")
        self.latitude_var = tk.StringVar()
        ttk.Entry(alignment_frame, width=12, textvariable=self.latitude_var).grid(
            row=0, column=1, padx=(5, 14), sticky="w"
        )
        ttk.Label(alignment_frame, text="경도 (동 +)").grid(row=0, column=2, sticky="w")
        self.longitude_var = tk.StringVar()
        ttk.Entry(alignment_frame, width=12, textvariable=self.longitude_var).grid(
            row=0, column=3, padx=(5, 14), sticky="w"
        )
        self.auto_goto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            alignment_frame,
            text="Ctrl+1 수신 즉시 GoTo",
            variable=self.auto_goto_var,
        ).grid(row=0, column=4, sticky="w")
        ttk.Button(
            alignment_frame,
            text="Stellarium 얼라인 별 선택 대기",
            command=self._arm_alignment_star_selection,
        ).grid(
            row=1, column=0, columnspan=2, padx=(0, 5), pady=(8, 0), sticky="ew"
        )
        ttk.Button(alignment_frame, text="중앙 정렬 후 기준점 저장", command=self._add_alignment).grid(
            row=1, column=2, padx=5, pady=(8, 0), sticky="ew"
        )
        ttk.Button(alignment_frame, text="수신 목표로 GoTo", command=self._goto_target).grid(
            row=1, column=3, padx=5, pady=(8, 0), sticky="ew"
        )
        ttk.Button(alignment_frame, text="GoTo 중지", command=self._cancel_goto).grid(
            row=1, column=4, padx=(5, 0), pady=(8, 0), sticky="ew"
        )
        self.alignment_status_var = tk.StringVar(
            value="최소 2점, 권장 3~8점 · 넓게 떨어진 별일수록 정확도가 높아집니다."
        )
        ttk.Button(alignment_frame, text="마지막 기준점 취소", command=self._undo_alignment).grid(
            row=2, column=0, padx=(0, 5), pady=(8, 0), sticky="ew"
        )
        ttk.Button(alignment_frame, text="전체 초기화", command=self._reset_alignment).grid(
            row=2, column=1, padx=5, pady=(8, 0), sticky="ew"
        )
        ttk.Label(alignment_frame, textvariable=self.alignment_status_var).grid(
            row=2, column=2, columnspan=3, sticky="w", pady=(8, 0)
        )
        self.motor_position_var = tk.StringVar(value="축 위치: 수신 대기")
        ttk.Label(alignment_frame, textvariable=self.motor_position_var).grid(
            row=3, column=0, columnspan=5, sticky="w", pady=(4, 0)
        )
        for column in range(5):
            alignment_frame.columnconfigure(column, weight=1)

        connection = ttk.LabelFrame(outer, text="BLE 연결 (실험적 수동 제어)", padding=10)
        connection.pack(fill="x", pady=(10, 0))
        self.device_var = tk.StringVar()
        self.device_box = ttk.Combobox(connection, textvariable=self.device_var, state="readonly")
        self.device_box.grid(row=0, column=0, columnspan=3, sticky="ew", padx=(0, 8))
        ttk.Button(connection, text="검색", command=self._scan).grid(row=0, column=3, padx=3)
        self.connect_button = ttk.Button(connection, text="연결", command=self._connect)
        self.connect_button.grid(row=0, column=4, padx=3)
        ttk.Button(connection, text="해제", command=self._disconnect).grid(row=0, column=5, padx=3)
        connection.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(
            value="본체 스위치를 맨 아래 Bluetooth 아이콘 위치로 놓고 전원을 다시 켠 뒤 검색하세요."
        )
        ttk.Label(connection, textvariable=self.status_var).grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(8, 0)
        )

        safety = ttk.LabelFrame(outer, text="안전 설정", padding=10)
        safety.pack(fill="x", pady=(10, 0))
        self.armed_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            safety,
            text="이동 제어 사용 (연결 응답이 확인된 경우에만 동작)",
            variable=self.armed_var,
            command=self._movement_toggle,
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(safety, text="모터 속도 단계").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.speed_var = tk.IntVar(value=1)
        self.speed_spinbox = ttk.Spinbox(
            safety,
            from_=1,
            to=9,
            increment=1,
            width=4,
            justify="center",
            textvariable=self.speed_var,
        )
        self.speed_spinbox.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        self.speed_label = ttk.Label(safety, text="1=최저 · 9=최대")
        self.speed_label.grid(row=1, column=2, columnspan=2, sticky="w", padx=(3, 0), pady=(8, 0))
        self.speed_var.trace_add("write", self._update_speed_label)
        safety.columnconfigure(1, weight=1)

        mapping = ttk.Frame(safety)
        mapping.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(mapping, text="방위축 모터").pack(side="left")
        self.az_motor_var = tk.IntVar(value=1)
        ttk.Combobox(mapping, width=4, state="readonly", values=(0, 1), textvariable=self.az_motor_var).pack(
            side="left", padx=(4, 12)
        )
        ttk.Label(mapping, text="고도축 모터").pack(side="left")
        self.alt_motor_var = tk.IntVar(value=0)
        ttk.Combobox(mapping, width=4, state="readonly", values=(0, 1), textvariable=self.alt_motor_var).pack(
            side="left", padx=(4, 12)
        )
        self.reverse_az_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mapping, text="좌우 반전", variable=self.reverse_az_var).pack(side="left", padx=6)
        self.reverse_alt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mapping, text="상하 반전", variable=self.reverse_alt_var).pack(side="left", padx=6)

        control = ttk.LabelFrame(outer, text="수동 제어", padding=10)
        control.pack(fill="x", pady=(10, 0))
        ttk.Button(control, text="초기화 조회", command=self._initialize).grid(
            row=0, column=0, padx=8, pady=8, sticky="ew"
        )
        self.stop_button = tk.Button(
            control,
            text="비상 정지 (양축)",
            command=self._stop_all,
            bg="#b00020",
            fg="white",
            activebackground="#d32f2f",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
        )
        self.stop_button.grid(row=0, column=2, padx=8, pady=8, sticky="ew")

        up = ttk.Button(control, text="▲  위")
        left = ttk.Button(control, text="◀  왼쪽")
        right = ttk.Button(control, text="오른쪽  ▶")
        down = ttk.Button(control, text="▼  아래")
        up.grid(row=1, column=1, padx=8, pady=5, sticky="ew")
        left.grid(row=2, column=0, padx=8, pady=5, sticky="ew")
        right.grid(row=2, column=2, padx=8, pady=5, sticky="ew")
        down.grid(row=3, column=1, padx=8, pady=5, sticky="ew")
        self._bind_motion(up, "alt", False)
        self._bind_motion(down, "alt", True)
        self._bind_motion(left, "az", True)
        self._bind_motion(right, "az", False)
        ttk.Label(
            control,
            text="방향키/WASD: 이동 · 숫자열/숫자패드 1~9: 속도 · Space/Esc: 비상 정지",
        ).grid(
            row=4, column=0, columnspan=3, pady=(8, 0)
        )
        for column in range(3):
            control.columnconfigure(column, weight=1)

        log_frame = ttk.LabelFrame(outer, text="통신 로그", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(log_frame, height=14, wrap="none", state="disabled", font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)

        key_map = {
            "Up": ("alt", False),
            "w": ("alt", False),
            "W": ("alt", False),
            "Down": ("alt", True),
            "s": ("alt", True),
            "S": ("alt", True),
            "Left": ("az", True),
            "a": ("az", True),
            "A": ("az", True),
            "Right": ("az", False),
            "d": ("az", False),
            "D": ("az", False),
        }
        self._keyboard_motion_map = key_map
        for key in key_map:
            self.bind_all(f"<KeyPress-{key}>", self._key_motion_press, add="+")
            self.bind_all(f"<KeyRelease-{key}>", self._key_motion_release, add="+")
        for key in SPEED_KEY_LEVELS:
            self.bind_all(f"<KeyPress-{key}>", self._key_speed_level, add="+")
        self.bind_all("<KeyPress-space>", self._key_emergency_stop, add="+")
        self.bind_all("<KeyPress-Escape>", self._key_emergency_stop, add="+")
        self.bind_all("<ButtonRelease-1>", self._release_mouse_motions, add="+")
        self.bind("<FocusOut>", self._window_focus_lost, add="+")

    def _emit(self, kind: str, value: object) -> None:
        self.events.put((kind, value))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status_var.set(str(value))
                elif kind == "devices":
                    self._set_devices(value)
                elif kind == "connected":
                    self.connected = True
                    self.verified_reply = False
                    address, write_uuid, notify_uuid = value
                    self._append_log(
                        f"GATT LINK {address}\nWRITE {write_uuid}\nNOTIFY {notify_uuid}"
                    )
                elif kind == "disconnected":
                    self.connected = False
                    self.verified_reply = False
                    self.motion_inputs.clear()
                elif kind == "protocol_failed":
                    self.verified_reply = False
                    self.status_var.set(str(value))
                    self._append_log(f"PROTOCOL NOT CONNECTED: {value}")
                elif kind == "stellarium_status":
                    self.stellarium_status_var.set(f"Stellarium TCP: {value}")
                    self._append_log(f"STELLARIUM {value}")
                elif kind == "stellarium_client":
                    self.stellarium_status_var.set(f"Stellarium TCP: {value}")
                    self._append_log(f"STELLARIUM CLIENT {value}")
                elif kind == "stellarium_goto":
                    self._handle_stellarium_goto(value)
                elif kind == "motor_position":
                    self.verified_reply = True
                    self._handle_motor_position(value)
                elif kind == "log":
                    self._append_log(str(value))
                elif kind == "frame":
                    frame = value
                    state = "OK" if frame.crc_valid else "CRC 오류"
                    self._append_log(
                        f"FRAME {state} seq={frame.sequence} cmd=0x{frame.command:02X} "
                        f"data={frame.data.hex(' ')}"
                    )
                    if frame.crc_valid:
                        self.verified_reply = True
                        self.status_var.set("BCTO 프로토콜 응답 확인됨.")
                elif kind == "error":
                    self._append_log(f"ERROR {value}")
                    self.status_var.set(str(value))
        except queue.Empty:
            pass
        self.after(50, self._drain_events)

    def _handle_stellarium_goto(self, target: GotoTarget) -> None:
        self.last_target = target
        self.target_var.set(
            f"목표 수신: RA {target.ra_hours:.6f} h · Dec {target.dec_degrees:+.6f}°"
        )
        self._append_log(
            f"STELLARIUM GOTO RA={target.ra_hours:.8f}h Dec={target.dec_degrees:+.8f}deg"
        )
        if self.alignment_selection_armed:
            self.alignment_selection_armed = False
            self.alignment_target = target
            try:
                latitude, longitude = self._site()
                az, alt = equatorial_to_horizontal(
                    target.ra_hours,
                    target.dec_degrees,
                    latitude,
                    longitude,
                )
                selected = (
                    f"얼라인 별 선택됨: RA {target.ra_hours:.4f}h / "
                    f"Dec {target.dec_degrees:+.3f}° · 현재 Az {az:.2f}° / Alt {alt:.2f}°"
                )
            except ValueError:
                selected = (
                    f"얼라인 별 선택됨: RA {target.ra_hours:.4f}h / "
                    f"Dec {target.dec_degrees:+.3f}° · 위도/경도를 입력하세요."
                )
            self.alignment_status_var.set(
                f"{selected} · 망원경 중앙 정렬 후 기준점 저장"
            )
            self.status_var.set("Stellarium 얼라인 별을 선택했습니다. 자동 GoTo는 실행하지 않습니다.")
            self._append_log(f"ALIGN STAR SELECTED {selected}")
            return
        self.status_var.set(
            "Stellarium 목표 수신됨. 얼라인 완료 후 GoTo할 수 있습니다."
        )
        if self.auto_goto_var.get() and self.alignment.ready:
            self._goto_target()

    def _arm_alignment_star_selection(self) -> None:
        self.alignment_target = None
        self.alignment_selection_armed = True
        self.alignment_status_var.set(
            "선택 대기 중 · Stellarium에서 얼라인 별을 선택하고 Ctrl+1을 누르세요."
        )
        self._append_log("ALIGN STAR SELECTION ARMED")

    def _site(self) -> tuple[float, float]:
        try:
            latitude = float(self.latitude_var.get().strip())
            longitude = float(self.longitude_var.get().strip())
        except ValueError as exc:
            raise ValueError("위도와 경도를 십진수 도 단위로 입력하세요.") from exc
        if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
            raise ValueError("위도는 -90~90, 경도는 -180~180 범위여야 합니다.")
        return latitude, longitude

    def _axis_count(self, axis: str) -> float:
        if self.unwrapped_motor_positions is None:
            raise RuntimeError("본체 축 위치가 아직 수신되지 않았습니다.")
        return self.unwrapped_motor_positions[self._axis_motor(axis)]

    def _add_alignment(self) -> None:
        try:
            if not self.connected or not self.verified_reply:
                raise RuntimeError("먼저 BCTO90에 연결해 위치 응답을 확인하세요.")
            if self.alignment_target is None:
                raise RuntimeError(
                    "먼저 'Stellarium 얼라인 별 선택 대기'를 누른 뒤 "
                    "Stellarium에서 별을 선택하고 Ctrl+1을 누르세요."
                )
            latitude, longitude = self._site()
            az, alt = equatorial_to_horizontal(
                self.alignment_target.ra_hours,
                self.alignment_target.dec_degrees,
                latitude,
                longitude,
            )
            point = AlignmentPoint(
                az_deg=az,
                alt_deg=alt,
                az_count=self._axis_count("az"),
                alt_count=self._axis_count("alt"),
            )
            count = self.alignment.add(point)
            if count == 1:
                message = f"1점 저장: Az {az:.2f}° / Alt {alt:.2f}° · 다른 방향의 두 번째 별 필요"
            elif count == 2:
                message = "2점 기본 얼라인 완료 · 정확도 향상을 위해 다른 방향의 별을 추가하세요."
            else:
                message = (
                    f"{count}점 보정 완료 · RMS {self.alignment.rms_error_deg:.2f}° · "
                    f"최대 {self.alignment.MAX_POINTS}점"
                )
            self.alignment_target = None
            self.alignment_selection_armed = False
            self.alignment_status_var.set(
                f"{message} · 다음 별은 Stellarium 선택 대기를 다시 누르세요."
            )
            self._append_log(f"ALIGN {count}/{self.alignment.MAX_POINTS} {message}")
        except (ValueError, RuntimeError) as exc:
            messagebox.showwarning("얼라인", str(exc))

    def _undo_alignment(self) -> None:
        self._cancel_goto()
        count = self.alignment.remove_last()
        if count == 0:
            message = "등록된 기준점이 없습니다."
        elif count == 1:
            message = "기준점 1개 남음 · 다른 방향의 별을 추가하세요."
        elif count == 2:
            message = "기준점 2개 남음 · 기본 GoTo 사용 가능"
        else:
            message = f"기준점 {count}개 · RMS {self.alignment.rms_error_deg:.2f}°"
        self.alignment_status_var.set(message)
        self._append_log(f"ALIGN UNDO {message}")

    def _reset_alignment(self) -> None:
        self._cancel_goto()
        self.alignment.reset()
        self.alignment_target = None
        self.alignment_selection_armed = False
        self.alignment_status_var.set("얼라인 초기화됨 · 최소 2점, 정확도 향상에는 3점 이상을 등록하세요.")

    def _goto_target(self) -> None:
        try:
            if not self._motion_ready():
                return
            if not self.alignment.ready:
                raise RuntimeError("먼저 최소 두 별로 얼라인을 완료하세요.")
            if self.last_target is None:
                raise RuntimeError("Stellarium에서 목표 천체를 선택하고 Ctrl+1을 누르세요.")
            latitude, longitude = self._site()
            az, alt = equatorial_to_horizontal(
                self.last_target.ra_hours,
                self.last_target.dec_degrees,
                latitude,
                longitude,
            )
            if not 3.0 <= alt <= 85.0:
                raise RuntimeError(f"안전 고도 범위(3~85°) 밖의 목표입니다: {alt:.2f}°")
            target_az, target_alt = self.alignment.counts_for(az, alt)
            targets = {
                self._axis_motor("az"): target_az,
                self._axis_motor("alt"): target_alt,
            }
            self.goto_axes.clear()
            speed = self._get_speed()
            for motor, target in targets.items():
                current = self.unwrapped_motor_positions[motor]
                remaining = target - current
                if abs(remaining) < 2.0:
                    continue
                if abs(remaining) > 0x7B0000:
                    raise RuntimeError("목표 이동량이 프로토콜 안전 범위를 초과했습니다.")
                self.goto_axes[motor] = (target, remaining)
                self.controller.submit(self.controller.move(motor, remaining < 0, speed))
            if not self.goto_axes:
                self.status_var.set("이미 목표 허용오차 안에 있습니다.")
                return
            self.goto_deadline = time.monotonic() + 120.0
            self.status_var.set(f"GoTo 이동 중 · Az {az:.2f}° / Alt {alt:.2f}° · 비상정지: Space/Esc")
            self._append_log(f"GOTO START az={az:.4f} alt={alt:.4f} targets={targets}")
        except (ValueError, RuntimeError) as exc:
            messagebox.showwarning("GoTo", str(exc))

    def _cancel_goto(self) -> None:
        if self.goto_axes:
            self._append_log("GOTO CANCEL")
        self.goto_axes.clear()
        self._stop_all()

    def _handle_motor_position(self, positions: tuple[int, int]) -> None:
        if self.raw_motor_positions is None or self.unwrapped_motor_positions is None:
            self.raw_motor_positions = positions
            self.unwrapped_motor_positions = [float(positions[0]), float(positions[1])]
        else:
            for motor in (0, 1):
                delta = ((positions[motor] - self.raw_motor_positions[motor] + 32768) % 65536) - 32768
                self.unwrapped_motor_positions[motor] += delta
            self.raw_motor_positions = positions
        now = time.monotonic()
        if now - self._last_position_display >= 0.25:
            self.motor_position_var.set(f"축 위치: M0 {positions[0]} · M1 {positions[1]}")
            self._last_position_display = now
        if self.alignment.ready:
            try:
                latitude, longitude = self._site()
                az, alt = self.alignment.horizontal_for_counts(
                    self._axis_count("az"), self._axis_count("alt")
                )
                ra, dec = horizontal_to_equatorial(az, alt, latitude, longitude)
                self.stellarium.set_position(ra, dec)
            except (ValueError, RuntimeError):
                pass
        if not self.goto_axes:
            return
        if now >= self.goto_deadline:
            self._append_log("GOTO TIMEOUT — 양축 정지")
            self.goto_axes.clear()
            self._stop_all()
            self.status_var.set("GoTo 제한시간 초과로 정지했습니다.")
            return
        finished: list[int] = []
        for motor, (target, initial) in self.goto_axes.items():
            remaining = target - self.unwrapped_motor_positions[motor]
            tolerance = max(2.0, abs(
                self.alignment.az_counts_per_degree
                if motor == self._axis_motor("az")
                else self.alignment.alt_counts_per_degree
            ) * 0.15)
            crossed = (initial > 0 and remaining <= 0) or (initial < 0 and remaining >= 0)
            if abs(remaining) <= tolerance or crossed:
                self._stop_motor(motor)
                finished.append(motor)
                self._append_log(f"GOTO AXIS STOP motor={motor} remaining={remaining:.1f}")
        for motor in finished:
            self.goto_axes.pop(motor, None)
        if finished and not self.goto_axes:
            self.status_var.set("GoTo 완료 · 접안부에서 미세 조정 후 필요하면 다시 얼라인하세요.")
            self._append_log("GOTO COMPLETE")

    def _set_devices(self, devices: list[DeviceInfo]) -> None:
        self.devices = {device.label: device for device in devices}
        labels = list(self.devices)
        self.device_box["values"] = labels
        if labels:
            self.device_box.current(0)

    def _scan(self) -> None:
        self.controller.submit(self.controller.scan())

    def _connect(self) -> None:
        device = self.devices.get(self.device_var.get())
        if not device:
            messagebox.showwarning("BCTO90", "먼저 BLE 장치를 검색하고 선택하세요.")
            return
        if not any(token in device.name.upper() for token in SUPPORTED_DEVICE_TOKENS):
            proceed = messagebox.askyesno(
                "미검증 BLE 장치",
                "선택한 장치는 BCTO/DS20090 이름으로 식별되지 않았습니다.\n\n"
                "Nordic UART 서비스가 같아도 BCTO 명령 체계와 호환된다는 뜻은 아닙니다. "
                "잘못된 명령은 가대를 움직이거나 장치를 손상시킬 수 있습니다.\n\n"
                "동일 프로토콜 장치임을 직접 확인한 경우에만 계속하세요.",
            )
            if not proceed:
                return
        self.controller.submit(self.controller.connect(device.address))

    def _disconnect(self) -> None:
        self.controller.submit(self.controller.disconnect())

    def _initialize(self) -> None:
        if not self.connected:
            messagebox.showwarning("BCTO90", "먼저 장치에 연결하세요.")
            return
        self.controller.submit(self.controller.initialize())
        self.status_var.set("초기화 조회 전송됨 — 응답 대기 중…")

    def _bind_motion(self, button: ttk.Button, axis: str, negative: bool) -> None:
        source = f"mouse:{axis}:{int(negative)}"
        button.bind(
            "<ButtonPress-1>",
            lambda _event: self._activate_motion(source, axis, negative),
        )
        button.bind("<ButtonRelease-1>", lambda _event: self._deactivate_motion(source))

    def _axis_motor(self, axis: str) -> int:
        return self.alt_motor_var.get() if axis == "alt" else self.az_motor_var.get()

    def _motion_ready(self) -> bool:
        if not self.connected:
            message = "먼저 BLE 장치에 연결하세요."
            self.status_var.set(message)
            self._append_log(f"MOVE BLOCKED: {message}")
            return False
        if not self.armed_var.get():
            message = "안전 설정에서 이동 제어 사용을 활성화하세요."
            self.status_var.set(message)
            self._append_log(f"MOVE BLOCKED: {message}")
            return False
        if not self.verified_reply:
            message = "초기화 조회에 대한 유효한 BCTO 응답이 아직 없습니다."
            self.status_var.set(message)
            self._append_log(f"MOVE BLOCKED: {message}")
            return False
        if self.az_motor_var.get() == self.alt_motor_var.get():
            message = "방위축과 고도축 모터 번호가 서로 달라야 합니다."
            self.status_var.set(message)
            self._append_log(f"MOVE BLOCKED: {message}")
            return False
        return True

    def _activate_motion(self, source: str, axis: str, negative: bool) -> None:
        if not self._motion_ready():
            return
        self.motion_inputs[source] = (axis, negative)
        self._drive_axis(axis)

    def _deactivate_motion(self, source: str) -> None:
        motion = self.motion_inputs.pop(source, None)
        if motion is None:
            return
        axis, _negative = motion
        if any(active_axis == axis for active_axis, _ in self.motion_inputs.values()):
            self._drive_axis(axis)
        else:
            self._stop_axis(axis)

    def _drive_axis(self, axis: str) -> None:
        active = [
            negative
            for active_axis, negative in self.motion_inputs.values()
            if active_axis == axis
        ]
        if not active:
            self._stop_axis(axis)
            return
        negative = active[-1]
        motor = self._axis_motor(axis)
        reverse = self.reverse_alt_var.get() if axis == "alt" else self.reverse_az_var.get()
        effective_negative = bool(negative) ^ bool(reverse)
        speed_level = self._get_speed_level()
        speed = self._get_speed()
        self.controller.submit(self.controller.move(motor, effective_negative, speed))
        direction = "-" if effective_negative else "+"
        self.status_var.set(
            f"{axis.upper()}축 모터 {motor} {direction} {speed_level}단({speed}%) 이동 — 입력을 놓으면 정지"
        )

    def _stop_axis(self, axis: str) -> None:
        self._stop_motor(self._axis_motor(axis))

    def _stop_motor(self, motor: int) -> None:
        if self.connected:
            self.controller.submit(self.controller.stop_motor(motor))

    def _stop_all(self) -> None:
        self.motion_inputs.clear()
        self.goto_axes.clear()
        if self.connected:
            self.controller.submit(self.controller.stop_all())
        self.status_var.set("양축 정지 명령 전송")

    def _movement_toggle(self) -> None:
        if not self.armed_var.get():
            self._stop_all()

    def _update_speed_label(self, *_args) -> None:
        try:
            level = int(self.speed_var.get())
        except (tk.TclError, ValueError):
            return
        if 1 <= level <= 9:
            self.speed_label.configure(text=f"{level}단 · 출력 {SPEED_PERCENT_BY_LEVEL[level]}%")

    def _get_speed_level(self) -> int:
        try:
            current = int(self.speed_var.get())
        except (tk.TclError, ValueError):
            current = 1
        level = max(1, min(9, current))
        if current != level:
            self.speed_var.set(level)
        return level

    def _get_speed(self) -> int:
        return SPEED_PERCENT_BY_LEVEL[self._get_speed_level()]

    def _key_speed_level(self, event) -> str | None:
        if self._is_text_input(event.widget):
            return None
        level = SPEED_KEY_LEVELS.get(event.keysym)
        if level is None:
            return None
        self.speed_var.set(level)
        self.status_var.set(
            f"모터 속도 {level}단 · 출력 {SPEED_PERCENT_BY_LEVEL[level]}%"
        )
        return "break"

    def _key_motion_press(self, event) -> str | None:
        if self._is_text_input(event.widget):
            return None
        key = event.keysym if event.keysym in self._keyboard_motion_map else event.keysym.lower()
        motion = self._keyboard_motion_map.get(key)
        if motion is None:
            return None
        source = f"key:{key}"
        if source not in self.motion_inputs:
            self._activate_motion(source, *motion)
        return "break"

    def _key_motion_release(self, event) -> str | None:
        key = event.keysym if event.keysym in self._keyboard_motion_map else event.keysym.lower()
        source = f"key:{key}"
        if source in self.motion_inputs:
            self._deactivate_motion(source)
            return "break"
        return None

    @staticmethod
    def _is_text_input(widget: tk.Misc) -> bool:
        return widget.winfo_class() in {
            "Entry",
            "TEntry",
            "Spinbox",
            "TSpinbox",
            "TCombobox",
            "Text",
        }

    def _key_emergency_stop(self, _event) -> str:
        self._stop_all()
        return "break"

    def _release_mouse_motions(self, _event) -> None:
        for source in [key for key in self.motion_inputs if key.startswith("mouse:")]:
            self._deactivate_motion(source)

    def _window_focus_lost(self, event) -> None:
        if event.widget is self and self.motion_inputs:
            self._stop_all()

    def _append_log(self, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        try:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"[{stamp}] {line}\n")
        except OSError:
            pass
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {line}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _close(self) -> None:
        self.stellarium.stop()
        if self.connected:
            self.controller.submit(self.controller.stop_all())
            time.sleep(0.1)
            self.controller.submit(self.controller.disconnect())
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
