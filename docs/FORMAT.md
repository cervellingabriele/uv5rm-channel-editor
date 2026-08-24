# Baofeng `.xlc` codeplug format (UV-5RM Plus / 5RH Pro)

Reverse-engineered for personal use by diffing real codeplugs saved by the
manufacturer's CPS. Every field below was confirmed by writing values back and
comparing byte-for-byte with CPS output, and by round-tripping full codeplugs.
This is **not** official documentation and may be incomplete or vary by firmware.

All multi-byte frequency/tone fields are **BCD** (binary-coded decimal), i.e. each
nibble is a decimal digit.

## Overall

| Property | Value |
|---|---|
| File size | `131072` bytes (fixed) |
| Zones | `10` |
| Slots per zone | `64` |
| Total channel slots | `640` |

## Header

| Offset | Size | Meaning |
|---|---|---|
| `16` | 4 | Low band limit, BCD. `00 00 60 13` = 136.0 MHz (stock). `00 00 80 10` = 108.0 MHz (unlocks AM aircraft band for RX). |

Other header bytes (timestamps, model strings, etc.) are left untouched by this
tool.

## Channel record

Located at `128 + global_index * 48`, 48 bytes each, where:

```
global_index = (zone - 1) * 64 + slot        # zone 1..10, slot 0..63
```

| Offset (in record) | Size | Meaning |
|---|---|---|
| `0` | 4 | RX frequency — little-endian BCD, unit = 10 Hz. e.g. `25 06 60 44` → `44600625` → 446.00625 MHz |
| `4` | 4 | TX frequency — same encoding |
| `8` | 4 | Tone — big-endian BCD, value = Hz × 10. e.g. `00 00 08 85` → `885` → 88.5 Hz. `00 00 00 00` = none |
| `16` | 1 | Flags (see below) |
| `32` | 10 | Name — ASCII, zero-padded, max 10 chars (CPS truncates longer names) |

Bytes not listed are zero in normal channels; this tool preserves the original
record verbatim for channels it does not modify.

### Flag byte (offset 16)

| Bit | Mask | Meaning |
|---|---|---|
| 7 | `0x80` | Power: 1 = High, 0 = Low |
| 5 | `0x20` | Bandwidth: 1 = Wide (25 kHz), 0 = Narrow (12.5 kHz) |
| 3 | `0x08` | TX tone enabled |

Remaining bits are preserved when re-encoding an existing channel.

## Occupancy bitmap

`80` bytes at offset `31264` (covers all 640 slots).

```
byte  = 31264 + global_index // 8
bit   = 1 << (global_index % 8)
used  = (data[byte] & bit) == 0        # 0 = used, 1 = free
```

## Active-zone counter

Single byte at offset `31360`: the number of zones that contain at least one
channel.

## Zone table

`152` bytes per zone at `31376 + (zone - 1) * 152`.

| Offset (in table) | Size | Meaning |
|---|---|---|
| `0` | 1 | Channel count in the zone |
| `1` | 1 | `0xFF` separator |
| `2` | 128 | 64 entries × 2-byte **big-endian** global index; `0xFFFF` = empty slot |
| `130` | 6 | `0xFF` padding |
| `136` | 16 | Zone name — ASCII, zero-padded |

## Notes / open items

- **RX tone** appears to occupy the low half of the tone field while the CPS's
  single "Rx/Tx" field writes the TX side; a reliable RX-tone-enable bit was not
  isolated, so this tool does not set RX tone. Use the radio's `R-CTCS` menu for
  individual channels if needed.
- **AM mode** for the aircraft band is not written explicitly; the radio appears
  to switch to AM automatically below ~137 MHz. Verify audio on air.
