from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

import serial
from serial.tools import list_ports

logger = logging.getLogger(__name__)

# See ../../../../../macropad_pendant/PROTOCOL.md (sibling project, not part of this repo)
# for the full protocol this module speaks.


class _PendantPermissionError(Exception):
    """Raised when a matching device is found but the serial port can't be opened."""


class Daemon:
    """
    Establishes a connection to a MacroPad pendant over USB serial, receives protocol
    events, and invokes callbacks. Mirrors the structure of whb04.Daemon, but the transport
    is a line-based serial protocol instead of raw HID reports -- see PROTOCOL.md.
    """

    VENDOR_ID = 0x239A  # Adafruit

    # The device enumerates as two serial ports (console + data) sharing this VID/PID, so
    # identity is confirmed by protocol handshake (ID), not by USB descriptor alone.
    _ID_RE = re.compile(r"^ID MACROPAD (\S.*)$")
    _HANDSHAKE_TIMEOUT = 0.5

    def __init__(self, callback_executor: Callable[[Callable[[], None]], None] = lambda f: f()) -> None:
        self._is_running = False
        self._daemon_thread: threading.Thread | None = None

        self._serial_lock = threading.Lock()
        self._serial: serial.Serial | None = None

        self._pressed_keys: set[int] = set()
        self._encoder_pressed = False

        self.num_keys = 12
        self.num_rows = 0
        self.num_cols = 0
        self.firmware_version = 0

        self.callback_executor = callback_executor

        self.on_connect: Callable[[Daemon], None] | None = None
        self.on_disconnect: Callable[[Daemon], None] | None = None
        self.on_key_press: Callable[[Daemon, int], None] | None = None
        self.on_key_release: Callable[[Daemon, int], None] | None = None
        self.on_encoder_delta: Callable[[Daemon, int], None] | None = None
        self.on_encoder_press: Callable[[Daemon], None] | None = None
        self.on_encoder_release: Callable[[Daemon], None] | None = None
        self.on_update: Callable[[Daemon], None] | None = None
        self.on_permission_error: Callable[[Daemon], None] | None = None

    def start(self) -> None:
        self._daemon_thread = threading.Thread(target=self._thread_loop, daemon=True)
        self._is_running = True
        self._daemon_thread.start()

    def stop(self) -> None:
        if not self._is_running:
            return
        self._is_running = False
        self._daemon_thread.join()

    @property
    def pressed_keys(self) -> set[int]:
        return self._pressed_keys

    @property
    def encoder_pressed(self) -> bool:
        return self._encoder_pressed

    # --- Outbound commands ------------------------------------------------------------

    def set_led(self, index: int, rgb: int) -> None:
        self._send(f"LED {index} {rgb:06X}")

    def set_led_all(self, rgb: int) -> None:
        self._send(f"LEDALL {rgb:06X}")

    def set_led_anim(self, index: int, mode: str, rgb: int, period_ms: int) -> None:
        """
        Animate one key on the device itself (firmware 3+).

        The board redraws locally, so an animation costs one command rather than a stream
        of frames, and keys sharing a mode and period stay in phase with each other.
        """
        self._send(f"LEDA {index} {mode} {rgb:06X} {int(period_ms)}")

    def set_screensaver(self, period_ms: int) -> None:
        """
        Blank the display down to one dot walking its perimeter (firmware 3+).

        The device animates it, so resting costs no traffic at all.
        """
        self._send(f"SAVER {int(period_ms)}")

    def clear_screensaver(self) -> None:
        self._send("SAVER")

    @property
    def supports_animation(self) -> bool:
        """LEDA/SAVER were added in firmware 3; older firmware ignores them."""
        return self.firmware_version >= 3

    def set_brightness(self, percent: int) -> None:
        self._send(f"BRIGHT {max(0, min(100, int(percent)))}")

    def set_text(self, row: int, text: str) -> None:
        self._send(f"TEXT {row} {text}")

    def set_banner(self, text: str) -> None:
        """Large centered text. Empty text returns the display to row-grid mode."""
        self._send(f"BANNER {text}".rstrip())

    def set_hint(self, text: str) -> None:
        """Small hint line under the banner."""
        self._send(f"HINT {text}".rstrip())

    @property
    def supports_banner(self) -> bool:
        """BANNER/HINT were added in firmware 2; older firmware ignores them."""
        return self.firmware_version >= 2

    def clear_text(self) -> None:
        self._send("CLEAR")

    def reset_device(self) -> None:
        self._send("RESET")

    def _send(self, command: str) -> None:
        with self._serial_lock:
            if self._serial is None:
                return
            try:
                self._serial.write((command + "\n").encode("ascii", "replace"))
            except (OSError, serial.SerialException) as e:
                logger.warning("Failed to write to pendant (will reconnect): %s", e)

    # --- Connection lifecycle ----------------------------------------------------------

    def _thread_loop(self) -> None:
        while self._is_running:
            connected = False
            try:
                ser = self._connect()
                if ser is None:
                    continue

                with self._serial_lock:
                    self._serial = ser
                connected = True
                if self.on_connect is not None:
                    self.callback_executor(lambda: self.on_connect(self))
                self._device_loop(ser)
            except _PendantPermissionError as e:
                logger.error("Pendant found but cannot be opened: permission denied on %s", e)
                self._is_running = False
                if self.on_permission_error is not None:
                    self.callback_executor(lambda: self.on_permission_error(self))
            except Exception as e:
                logger.warning("Pendant error (will retry): %s", e)
            finally:
                with self._serial_lock:
                    if self._serial is not None:
                        try:
                            self._serial.close()
                        except Exception:
                            pass
                        self._serial = None
                if connected and self.on_disconnect is not None:
                    self.callback_executor(lambda: self.on_disconnect(self))

    def _candidate_ports(self) -> list[str]:
        return [p.device for p in list_ports.comports() if p.vid == self.VENDOR_ID]

    def _connect(self, poll_interval: float = 0.5) -> serial.Serial | None:
        while self._is_running:
            for port in self._candidate_ports():
                try:
                    handshake = self._try_handshake(port)
                except _PendantPermissionError:
                    raise
                except Exception as e:
                    logger.debug("Could not probe %s: %s", port, e)
                    continue
                if handshake is not None:
                    return handshake
            time.sleep(poll_interval)
        return None

    def _try_handshake(self, port: str) -> serial.Serial | None:
        try:
            ser = serial.Serial(port, baudrate=115200, timeout=self._HANDSHAKE_TIMEOUT)
        except (OSError, serial.SerialException) as e:
            if "Permission" in str(e) or getattr(e, "errno", None) == 13:
                raise _PendantPermissionError(port) from e
            raise

        try:
            ser.reset_input_buffer()
            ser.write(b"ID\n")
            deadline = time.time() + self._HANDSHAKE_TIMEOUT
            while time.time() < deadline:
                line = ser.readline().decode("utf-8", "replace").strip()
                if not line:
                    continue
                match = self._ID_RE.match(line)
                if match:
                    self._parse_id(match.group(1))
                    return ser
            ser.close()
            return None
        except Exception:
            ser.close()
            raise

    def _parse_id(self, fields: str) -> None:
        values = dict(pair.split("=", 1) for pair in fields.split() if "=" in pair)
        self.firmware_version = int(values.get("fw", 0))
        self.num_keys = int(values.get("keys", 12))
        self.num_rows = int(values.get("rows", 0))
        self.num_cols = int(values.get("cols", 0))

    def _device_loop(self, ser: serial.Serial) -> None:
        ser.timeout = 0.1
        while self._is_running:
            raw_line = ser.readline()
            if raw_line:
                line = raw_line.decode("utf-8", "replace").strip()
                if line:
                    self._process_line(line)

            if self.on_update is not None:
                self.callback_executor(lambda: self.on_update(self))

    def _process_line(self, line: str) -> None:
        parts = line.split()
        if not parts:
            return

        try:
            if parts[0] == "KEY" and len(parts) == 3:
                key_number = int(parts[1])
                pressed = parts[2] == "DOWN"
                if pressed:
                    self._pressed_keys.add(key_number)
                    if self.on_key_press is not None:
                        self.callback_executor(lambda k=key_number: self.on_key_press(self, k))
                else:
                    self._pressed_keys.discard(key_number)
                    if self.on_key_release is not None:
                        self.callback_executor(lambda k=key_number: self.on_key_release(self, k))
            elif parts[0] == "ENC" and len(parts) == 4 and parts[2] == "DELTA":
                delta = int(parts[3])
                if self.on_encoder_delta is not None:
                    self.callback_executor(lambda d=delta: self.on_encoder_delta(self, d))
            elif parts[0] == "ENC" and len(parts) == 3 and parts[2] in ("DOWN", "UP"):
                self._encoder_pressed = parts[2] == "DOWN"
                if self._encoder_pressed and self.on_encoder_press is not None:
                    self.callback_executor(lambda: self.on_encoder_press(self))
                elif not self._encoder_pressed and self.on_encoder_release is not None:
                    self.callback_executor(lambda: self.on_encoder_release(self))
            elif parts[0] == "ERR":
                logger.debug("Pendant reported error: %s", line)
            # PONG / ID / anything else: no-op. Unknown messages must not be fatal --
            # a firmware update may add message types this driver predates.
        except (ValueError, IndexError):
            logger.debug("Malformed pendant line ignored: %r", line)


if __name__ == "__main__":
    daemon = Daemon()

    daemon.on_connect = lambda d: print(f"Connected: fw={d.firmware_version} rows={d.num_rows} cols={d.num_cols}")
    daemon.on_disconnect = lambda _d: print("Disconnected")
    daemon.on_key_press = lambda _d, k: print(f"Key {k} down")
    daemon.on_key_release = lambda _d, k: print(f"Key {k} up")
    daemon.on_encoder_delta = lambda _d, delta: print(f"Encoder delta {delta}")
    daemon.on_encoder_press = lambda _d: print("Encoder down")
    daemon.on_encoder_release = lambda _d: print("Encoder up")

    daemon.start()
    while True:
        time.sleep(1)
