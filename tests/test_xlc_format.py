"""Tests for the ``.xlc`` format engine.

These tests do not require a real codeplug on disk: a minimal valid image is
synthesized in-memory, so they run anywhere (no Tkinter, no display, no sample
files needed).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from uv5rm_editor.xlc_format import (  # noqa: E402
    FILE_SIZE,
    SLOTS_PER_ZONE,
    Channel,
    Codeplug,
    CodeplugError,
    format_frequency,
    format_tone,
)


def make_blank_image() -> bytes:
    """Build a minimal but structurally valid empty codeplug image."""
    data = bytearray(FILE_SIZE)
    # occupancy bitmap: everything free (0xFF)
    for i in range(31264, 31264 + 80):
        data[i] = 0xFF
    # zone tables: count 0, index list empty, 0xFF padding
    for zone in range(10):
        base = 31376 + zone * 152
        data[base] = 0
        data[base + 1] = 0xFF
        for slot in range(64):
            data[base + 2 + slot * 2] = 0xFF
            data[base + 3 + slot * 2] = 0xFF
        for k in range(6):
            data[base + 130 + k] = 0xFF
    return bytes(data)


@pytest.fixture()
def blank() -> Codeplug:
    return Codeplug(make_blank_image())


def test_rejects_wrong_size():
    with pytest.raises(CodeplugError):
        Codeplug(b"\x00" * 1024)


def test_blank_has_no_channels(blank):
    assert blank.channels == []


def test_add_channel_roundtrip(blank):
    blank.zone_names[1] = "PMR"
    blank.channels.append(
        Channel(zone=1, name="PMR01", rx=446.00625, tx=446.00625,
                tone=0.0, high_power=False, wide=False, raw=None, dirty=True)
    )
    reloaded = Codeplug(blank.build())
    assert len(reloaded.channels) == 1
    ch = reloaded.channels[0]
    assert ch.name == "PMR01"
    assert ch.zone == 1
    assert ch.high_power is False
    assert ch.wide is False
    assert reloaded.zone_names[1] == "PMR"


def test_frequency_bcd_is_exact(blank):
    for freq in (446.00625, 446.01250, 433.07500, 433.10000, 400.12500):
        blank.channels = [
            Channel(1, "X", freq, freq, 0.0, True, True, raw=None, dirty=True)
        ]
        reloaded = Codeplug(blank.build())
        assert abs(reloaded.channels[0].rx - freq) < 1e-6


def test_tone_roundtrip(blank):
    # generic 70 cm repeater example (+5 MHz shift, common 88.5 Hz tone)
    blank.channels = [
        Channel(1, "TONE", 430.000, 435.000, 88.5, True, True, raw=None, dirty=True)
    ]
    reloaded = Codeplug(blank.build())
    ch = reloaded.channels[0]
    assert abs(ch.tone - 88.5) < 1e-6
    assert abs(ch.tx - 435.000) < 1e-6


def test_flags_high_wide(blank):
    blank.channels = [
        Channel(1, "HI", 145.0, 145.0, 0.0, high_power=True, wide=True, raw=None, dirty=True),
        Channel(1, "LO", 446.0, 446.0, 0.0, high_power=False, wide=False, raw=None, dirty=True),
    ]
    reloaded = Codeplug(blank.build())
    hi = next(c for c in reloaded.channels if c.name == "HI")
    lo = next(c for c in reloaded.channels if c.name == "LO")
    assert hi.high_power and hi.wide
    assert not lo.high_power and not lo.wide


def test_unchanged_channel_is_byte_identical(blank):
    blank.channels = [
        Channel(1, "KEEP", 433.075, 433.075, 88.5, False, False, raw=None, dirty=True)
    ]
    image = blank.build()
    # reload, change nothing, rebuild: must match byte for byte
    reloaded = Codeplug(image)
    assert bytes(reloaded.build()) == bytes(image)


def test_zone_overflow_blocks_build(blank):
    blank.channels = [
        Channel(1, f"C{i}", 433.0, 433.0, 0.0, False, False, raw=None, dirty=True)
        for i in range(SLOTS_PER_ZONE + 1)
    ]
    with pytest.raises(CodeplugError):
        blank.build()


def test_name_truncated_to_ten(blank):
    blank.channels = [
        Channel(1, "TOOLONGNAME123", 446.0, 446.0, 0.0, False, False, raw=None, dirty=True)
    ]
    reloaded = Codeplug(blank.build())
    assert reloaded.channels[0].name == "TOOLONGNAM"


def test_airband_toggle(blank):
    airband = Codeplug(blank.build(allow_airband=True))
    assert airband.bandlimit_is_airband()
    stock = Codeplug(blank.build(allow_airband=False))
    assert not stock.bandlimit_is_airband()


def test_active_zone_counter(blank):
    blank.channels = [
        Channel(1, "A", 145.0, 145.0, 0.0, True, True, raw=None, dirty=True),
        Channel(3, "B", 446.0, 446.0, 0.0, True, True, raw=None, dirty=True),
    ]
    image = blank.build()
    assert image[31360] == 2  # two zones populated


def test_formatting_helpers():
    assert format_frequency(446.00625) == "446.00625"
    assert format_frequency(433.5) == "433.5"
    assert format_tone(0.0) == "-"
    assert format_tone(88.5) == "88.5"
