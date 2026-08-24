"""Reader/writer for Baofeng ``.xlc`` codeplug files (UV-5RM Plus / 5RH Pro).

This module contains **no GUI code** so it can be imported and unit-tested on
its own. The on-disk format was reverse-engineered from real codeplugs produced
by the manufacturer's CPS and validated to be byte-identical on write.

Format summary
--------------
The file is a fixed ``131072``-byte memory image.

Channel records
    ``128 + global_index * 48`` bytes, 48 bytes each, ``640`` slots
    (``10`` zones x ``64`` slots)::

        global_index = (zone - 1) * 64 + slot

    Record layout::

        [0:4]   RX frequency - little-endian BCD, unit = 10 Hz
        [4:8]   TX frequency - little-endian BCD, unit = 10 Hz
        [8:12]  tone         - big-endian BCD, value = Hz * 10 (0 = no tone)
        [16]    flags        - bit 0x80 High power, 0x20 Wide band,
                               0x08 tone enabled (other bits preserved)
        [32:42] name         - ASCII, max 10 chars, zero-padded

Global occupancy bitmap
    ``80`` bytes at offset ``31264``; bit ``1 << (g % 8)`` in byte ``g // 8``;
    ``0`` = used, ``1`` = free.

Active-zone counter
    single byte at offset ``31360``: number of zones that contain channels.

Zone table
    ``152`` bytes at ``31376 + (zone - 1) * 152``::

        [0]        channel count
        [1]        0xFF
        [2:130]    64 x 2-byte big-endian global indices (0xFFFF = empty)
        [130:136]  0xFF * 6
        [136:152]  zone name, ASCII, zero-padded

Low band limit (header)
    4-byte BCD at offset ``16``. ``136.0`` MHz is the stock value;
    ``108.0`` unlocks the AM aircraft band for receive-only channels.

Not handled on purpose
    Separate RX tone and explicit AM mode are not written. For channels that
    are not modified, their original bytes are preserved verbatim, so those
    fields survive a round-trip untouched.
"""

from __future__ import annotations

import datetime
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = [
    "FILE_SIZE",
    "N_ZONES",
    "SLOTS_PER_ZONE",
    "NAME_MAXLEN",
    "CTCSS_TONES",
    "Channel",
    "Codeplug",
    "format_frequency",
    "format_tone",
]

# ---------------------------------------------------------------------------
# Format constants
# ---------------------------------------------------------------------------
FILE_SIZE = 131072
_CH_BASE = 128
_CH_SIZE = 48
N_ZONES = 10
SLOTS_PER_ZONE = 64
_N_SLOTS = N_ZONES * SLOTS_PER_ZONE  # 640
_BITMAP_OFF = 31264
_BITMAP_LEN = 80
_ZONE_COUNT_OFF = 31360
_ZONE_TABLE_OFF = 31376
_ZONE_TABLE_STRIDE = 152
_BANDLIMIT_OFF = 16
_BANDLIMIT_AIRBAND = b"\x00\x00\x80\x10"  # 108.0 MHz
_BANDLIMIT_STOCK = b"\x00\x00\x60\x13"    # 136.0 MHz
NAME_MAXLEN = 10
_ZONE_NAME_MAXLEN = 16

_KNOWN_FLAG_BITS = 0x80 | 0x20 | 0x08

#: Standard CTCSS tones in Hz (``0.0`` means "no tone").
CTCSS_TONES = (
    0.0, 67.0, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4,
    100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5,
    141.3, 146.2, 151.4, 156.7, 162.2, 167.9, 173.8, 179.9, 186.2, 192.8,
    203.5, 210.7, 218.1, 225.7, 233.6, 241.8, 250.3,
)


# ---------------------------------------------------------------------------
# Low-level BCD helpers
# ---------------------------------------------------------------------------
def _freq_to_bcd_le(mhz: float) -> bytes:
    """Encode a frequency in MHz to 4-byte little-endian BCD (10 Hz units)."""
    value = round(mhz * 100_000)
    digits = f"{value:08d}"
    big_endian = bytes((int(digits[i]) << 4) | int(digits[i + 1]) for i in range(0, 8, 2))
    return big_endian[::-1]


def _freq_from_bcd_le(raw: bytes) -> float:
    """Decode 4-byte little-endian BCD to a frequency in MHz."""
    digits = "".join(f"{byte >> 4}{byte & 0xF}" for byte in reversed(raw))
    return int(digits) / 100_000.0


def _tone_to_bcd_be(tone: float) -> bytes:
    """Encode a CTCSS tone in Hz to 4-byte big-endian BCD (0 = no tone)."""
    if not tone:
        return b"\x00\x00\x00\x00"
    value = round(tone * 10)
    digits = f"{value:08d}"
    return bytes((int(digits[i]) << 4) | int(digits[i + 1]) for i in range(0, 8, 2))


def _tone_from_bcd_be(raw: bytes) -> float:
    """Decode 4-byte big-endian BCD to a CTCSS tone in Hz (0.0 = none)."""
    digits = "".join(f"{byte >> 4}{byte & 0xF}" for byte in raw)
    return int(digits) / 10.0


def _compose_flags(base: int, high: bool, wide: bool, tone_on: bool) -> int:
    """Build the flag byte, preserving unknown bits from ``base``."""
    flags = base & ~_KNOWN_FLAG_BITS & 0xFF
    if high:
        flags |= 0x80
    if wide:
        flags |= 0x20
    if tone_on:
        flags |= 0x08
    return flags


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Channel:
    """A single memory channel.

    The physical ``slot`` inside a zone is not stored here; channels are packed
    into consecutive slots (in list order) when the codeplug is written.
    """

    zone: int                      # 1..N_ZONES
    name: str
    rx: float                      # MHz
    tx: float                      # MHz
    tone: float = 0.0              # Hz, 0 = none
    high_power: bool = True
    wide: bool = True              # True = 25 kHz, False = 12.5 kHz
    raw: Optional[bytes] = field(default=None, repr=False)
    dirty: bool = False            # True => must be re-encoded on save

    def encode(self) -> bytes:
        """Return the 48-byte record for this channel.

        Unmodified channels that carry their original bytes are written back
        verbatim, preserving any reserved fields we do not model.
        """
        if self.raw is not None and not self.dirty:
            return self.raw
        base_flags = self.raw[16] if self.raw is not None else 0
        rec = bytearray(_CH_SIZE)
        rec[0:4] = _freq_to_bcd_le(self.rx)
        rec[4:8] = _freq_to_bcd_le(self.tx)
        rec[8:12] = _tone_to_bcd_be(self.tone)
        rec[16] = _compose_flags(base_flags, self.high_power, self.wide, bool(self.tone))
        name = self.name.encode("ascii", "replace")[:NAME_MAXLEN]
        rec[32:32 + len(name)] = name
        return bytes(rec)


class CodeplugError(Exception):
    """Raised for invalid files or constraints (e.g. zone overflow)."""


class Codeplug:
    """In-memory representation of a ``.xlc`` codeplug.

    A codeplug is always created from an existing file image so that every
    region we do not understand (radio settings, DTMF, APRS, display prefs,
    ...) is preserved. Only channel records, the occupancy bitmap, the zone
    tables/counter and optionally the band limit are rewritten on save.
    """

    def __init__(self, data: bytes, path: Optional[str] = None) -> None:
        if len(data) != FILE_SIZE:
            raise CodeplugError(
                f"Unexpected file size ({len(data)} bytes, expected {FILE_SIZE}); "
                "this does not look like a valid .xlc for this model."
            )
        self.data = bytearray(data)
        self.path = path
        self.channels: List[Channel] = []
        self.zone_names: Dict[int, str] = {z: "" for z in range(1, N_ZONES + 1)}
        self._parse()

    # -- construction ------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> "Codeplug":
        with open(path, "rb") as handle:
            return cls(handle.read(), path)

    # -- parsing -----------------------------------------------------------
    def _parse(self) -> None:
        for zone in range(1, N_ZONES + 1):
            base = _ZONE_TABLE_OFF + (zone - 1) * _ZONE_TABLE_STRIDE
            name = bytes(self.data[base + 136:base + 152]).split(b"\x00")[0]
            self.zone_names[zone] = name.decode("ascii", "replace")

        for g in range(_N_SLOTS):
            if self._slot_used(g):
                channel = self._read_channel(g)
                if channel is not None:
                    self.channels.append(channel)

    def _slot_used(self, g: int) -> bool:
        byte = self.data[_BITMAP_OFF + g // 8]
        return (byte >> (g % 8)) & 1 == 0

    def _read_channel(self, g: int) -> Optional[Channel]:
        off = _CH_BASE + g * _CH_SIZE
        rec = bytes(self.data[off:off + _CH_SIZE])
        if rec[0:8] == b"\x00" * 8 and rec[32] == 0:
            return None
        flags = rec[16]
        name = rec[32:32 + NAME_MAXLEN].split(b"\x00")[0].decode("ascii", "replace")
        return Channel(
            zone=g // SLOTS_PER_ZONE + 1,
            name=name,
            rx=_freq_from_bcd_le(rec[0:4]),
            tx=_freq_from_bcd_le(rec[4:8]),
            tone=_tone_from_bcd_be(rec[8:12]),
            high_power=bool(flags & 0x80),
            wide=bool(flags & 0x20),
            raw=rec,
            dirty=False,
        )

    # -- validation --------------------------------------------------------
    def zone_overflow(self) -> Dict[int, int]:
        """Return ``{zone: count}`` for zones exceeding ``SLOTS_PER_ZONE``."""
        counts: Dict[int, int] = {}
        for channel in self.channels:
            counts[channel.zone] = counts.get(channel.zone, 0) + 1
        return {z: n for z, n in counts.items() if n > SLOTS_PER_ZONE}

    def channels_by_zone(self) -> Dict[int, List[Channel]]:
        grouped: Dict[int, List[Channel]] = {z: [] for z in range(1, N_ZONES + 1)}
        for channel in self.channels:
            grouped[channel.zone].append(channel)
        return grouped

    def bandlimit_is_airband(self) -> bool:
        return bytes(self.data[_BANDLIMIT_OFF:_BANDLIMIT_OFF + 4]) == _BANDLIMIT_AIRBAND

    # -- serialization -----------------------------------------------------
    def build(self, allow_airband: Optional[bool] = None) -> bytearray:
        """Rebuild and return the full file image without writing to disk.

        ``allow_airband`` set to ``True``/``False`` lowers/restores the band
        limit; ``None`` leaves it as-is.
        """
        overflow = self.zone_overflow()
        if overflow:
            detail = ", ".join(f"zone {z}: {n}" for z, n in sorted(overflow.items()))
            raise CodeplugError(f"Too many channels in a zone (max {SLOTS_PER_ZONE}). {detail}")

        data = bytearray(self.data)  # start from the loaded image: preserves everything

        for i in range(_CH_BASE, _CH_BASE + _N_SLOTS * _CH_SIZE):
            data[i] = 0
        for i in range(_BITMAP_OFF, _BITMAP_OFF + _BITMAP_LEN):
            data[i] = 0xFF

        grouped = self.channels_by_zone()
        zone_indices: Dict[int, List[int]] = {z: [] for z in range(1, N_ZONES + 1)}
        for zone in range(1, N_ZONES + 1):
            for slot, channel in enumerate(grouped[zone]):
                g = (zone - 1) * SLOTS_PER_ZONE + slot
                off = _CH_BASE + g * _CH_SIZE
                data[off:off + _CH_SIZE] = channel.encode()
                data[_BITMAP_OFF + g // 8] &= ~(1 << (g % 8)) & 0xFF
                zone_indices[zone].append(g)

        data[_ZONE_COUNT_OFF] = sum(1 for z in range(1, N_ZONES + 1) if zone_indices[z])

        for zone in range(1, N_ZONES + 1):
            base = _ZONE_TABLE_OFF + (zone - 1) * _ZONE_TABLE_STRIDE
            indices = zone_indices[zone]
            data[base] = len(indices)
            data[base + 1] = 0xFF
            for i in range(SLOTS_PER_ZONE):
                if i < len(indices):
                    g = indices[i]
                    data[base + 2 + i * 2] = (g >> 8) & 0xFF
                    data[base + 3 + i * 2] = g & 0xFF
                else:
                    data[base + 2 + i * 2] = 0xFF
                    data[base + 3 + i * 2] = 0xFF
            for i in range(6):
                data[base + 130 + i] = 0xFF
            for i in range(_ZONE_NAME_MAXLEN):
                data[base + 136 + i] = 0
            name = self.zone_names.get(zone, "").encode("ascii", "replace")[:_ZONE_NAME_MAXLEN]
            data[base + 136:base + 136 + len(name)] = name

        if allow_airband is True:
            data[_BANDLIMIT_OFF:_BANDLIMIT_OFF + 4] = _BANDLIMIT_AIRBAND
        elif allow_airband is False:
            data[_BANDLIMIT_OFF:_BANDLIMIT_OFF + 4] = _BANDLIMIT_STOCK

        return data

    def save(
        self,
        path: str,
        allow_airband: Optional[bool] = None,
        make_backup: bool = True,
    ) -> None:
        """Serialize to ``path``, optionally creating a timestamped backup."""
        data = self.build(allow_airband=allow_airband)
        if make_backup and os.path.exists(path):
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(path, f"{path}.{stamp}.bak")
        with open(path, "wb") as handle:
            handle.write(bytes(data))
        self.data = bytearray(data)
        self.path = path


# ---------------------------------------------------------------------------
# Formatting helpers (handy for any front-end)
# ---------------------------------------------------------------------------
def format_frequency(mhz: float) -> str:
    """Human-readable MHz string without trailing zeros (e.g. ``446.00625``)."""
    text = f"{mhz:.5f}".rstrip("0").rstrip(".")
    return text or "0"


def format_tone(tone: float) -> str:
    """Human-readable tone (``-`` when no tone is set)."""
    return "-" if not tone else f"{tone:.1f}"
