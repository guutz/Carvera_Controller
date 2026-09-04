"""Tests for the MacroPad pendant serial protocol driver (no hardware required)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from carveracontroller.addons.pendant import macropad as macropad_module


def make_daemon() -> macropad_module.Daemon:
    # callback_executor defaults to immediate invocation, which is what we want in tests
    # (no Kivy Clock involved).
    return macropad_module.Daemon()


def test_parse_id_populates_fields():
    daemon = make_daemon()
    daemon._parse_id("fw=1 keys=12 enc=1 rows=8 cols=21")

    assert daemon.firmware_version == 1
    assert daemon.num_keys == 12
    assert daemon.num_rows == 8
    assert daemon.num_cols == 21


def test_parse_id_defaults_missing_fields():
    daemon = make_daemon()
    daemon._parse_id("fw=2")

    assert daemon.firmware_version == 2
    assert daemon.num_keys == 12  # unchanged default
    assert daemon.num_rows == 0
    assert daemon.num_cols == 0


@pytest.mark.parametrize("line", ["KEY 3 DOWN", "KEY 0 DOWN", "KEY 11 DOWN"])
def test_process_line_key_down_tracks_pressed_and_fires_callback(line):
    daemon = make_daemon()
    seen = []
    daemon.on_key_press = lambda d, k: seen.append(k)

    daemon._process_line(line)

    key_number = int(line.split()[1])
    assert key_number in daemon.pressed_keys
    assert seen == [key_number]


def test_process_line_key_up_clears_pressed_and_fires_callback():
    daemon = make_daemon()
    daemon._pressed_keys.add(5)
    released = []
    daemon.on_key_release = lambda d, k: released.append(k)

    daemon._process_line("KEY 5 UP")

    assert 5 not in daemon.pressed_keys
    assert released == [5]


def test_process_line_encoder_delta_positive_and_negative():
    daemon = make_daemon()
    deltas = []
    daemon.on_encoder_delta = lambda d, delta: deltas.append(delta)

    daemon._process_line("ENC 0 DELTA 3")
    daemon._process_line("ENC 0 DELTA -2")

    assert deltas == [3, -2]


def test_process_line_encoder_press_release():
    daemon = make_daemon()
    events = []
    daemon.on_encoder_press = lambda d: events.append("down")
    daemon.on_encoder_release = lambda d: events.append("up")

    daemon._process_line("ENC 0 DOWN")
    assert daemon.encoder_pressed is True
    daemon._process_line("ENC 0 UP")
    assert daemon.encoder_pressed is False

    assert events == ["down", "up"]


@pytest.mark.parametrize(
    "line",
    [
        "PONG",
        "ID MACROPAD fw=1 keys=12 enc=1 rows=8 cols=21",
        "ERR unrecognized: FOO",
        "",
        "GARBAGE",
        "KEY notanumber DOWN",
        "KEY 3 SIDEWAYS",
        "ENC 0 DELTA notanumber",
        "ENC 0 SIDEWAYS",
    ],
)
def test_process_line_never_raises_on_unknown_or_malformed_input(line):
    daemon = make_daemon()
    daemon.on_key_press = lambda d, k: pytest.fail("should not fire on malformed input")
    daemon.on_encoder_delta = lambda d, delta: pytest.fail("should not fire on malformed input")

    daemon._process_line(line)  # must not raise


def test_candidate_ports_filters_by_adafruit_vendor_id(monkeypatch):
    fake_ports = [
        SimpleNamespace(device="/dev/cu.usbmodem1", vid=0x239A),
        SimpleNamespace(device="/dev/cu.usbmodem2", vid=0x239A),
        SimpleNamespace(device="/dev/cu.other", vid=0x1234),
        SimpleNamespace(device="/dev/cu.none", vid=None),
    ]
    monkeypatch.setattr(macropad_module.list_ports, "comports", lambda: fake_ports)

    daemon = make_daemon()
    assert daemon._candidate_ports() == ["/dev/cu.usbmodem1", "/dev/cu.usbmodem2"]


class FakeSerial:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.closed = True


def test_set_led_writes_expected_command():
    daemon = make_daemon()
    fake = FakeSerial()
    daemon._serial = fake

    daemon.set_led(3, 0xFF8800)

    assert fake.written == [b"LED 3 FF8800\n"]


def test_set_led_all_and_brightness_and_text_and_clear_and_reset():
    daemon = make_daemon()
    fake = FakeSerial()
    daemon._serial = fake

    daemon.set_led_all(0x00FF00)
    daemon.set_brightness(150)  # clamped to 100
    daemon.set_brightness(-5)  # clamped to 0
    daemon.set_text(2, "WX: 1.234")
    daemon.clear_text()
    daemon.reset_device()

    assert fake.written == [
        b"LEDALL 00FF00\n",
        b"BRIGHT 100\n",
        b"BRIGHT 0\n",
        b"TEXT 2 WX: 1.234\n",
        b"CLEAR\n",
        b"RESET\n",
    ]


def test_send_is_noop_when_not_connected():
    daemon = make_daemon()
    assert daemon._serial is None

    daemon.set_led(0, 0xFFFFFF)  # must not raise


def test_send_swallows_write_errors_and_logs(monkeypatch):
    daemon = make_daemon()

    class ExplodingSerial:
        def write(self, data):
            raise OSError("device disconnected")

    daemon._serial = ExplodingSerial()
    daemon.set_led(0, 0xFFFFFF)  # must not raise
