#!/usr/bin/env python3
"""
make_gamma_icc.py — Generate ICC profiles from xrandr-style per-channel gamma values.

Usage:
    python3 make_gamma_icc.py --output monitor.icc --red 0.80 --green 0.80 --blue 0.93
    python3 make_gamma_icc.py --output monitor.icc --gamma 0.80:0.80:0.93
    python3 make_gamma_icc.py --output monitor.icc --gamma 0.80  (same value for all channels)
    python3 make_gamma_icc.py --output monitor.icc --gamma 1.76 --raw  (bypass ×2.2 scaling)

Gamma semantics (xrandr-compatible, the default):
  By default, gamma values are treated as xrandr-style *multipliers* applied on top of
  an assumed display gamma of 2.2.  The ICC exponent written into the profile is:

      icc_exponent = 2.2 × input_value

  So:
      xrandr 1.0  → ICC 2.2  (no correction, native display gamma)
      xrandr 0.80 → ICC 1.76 (raises midtones / brightens)
      xrandr 0.93 → ICC 2.05 (slight brightening)
      xrandr 1.25 → ICC 2.75 (lowers midtones / darkens)

  Use --raw to skip the ×2.2 scaling and write the value you supply directly as the
  ICC exponent (useful if you already know the absolute exponent you want).

The generated profile uses:
  - Standard sRGB colorimetry (D65 whitepoint, Rec.709 primaries)
  - Per-channel power-law TRC matching the computed exponent
  - vcgt tag with the same per-channel LUT (for GPU gamma ramp via colord)

No external dependencies beyond Python stdlib.
"""

import struct
import math
import argparse
import sys
import datetime


# ---------------------------------------------------------------------------
# ICC primitives
# ---------------------------------------------------------------------------

def s15f16(v: float) -> bytes:
    """Pack float as ICC s15Fixed16Number (big-endian signed 32-bit, 16 frac bits)."""
    return struct.pack(">i", int(round(v * 65536)))


def u16(v: int) -> bytes:
    return struct.pack(">H", v)


def u32(v: int) -> bytes:
    return struct.pack(">I", v)


def xyz_tag(x: float, y: float, z: float) -> bytes:
    """Build an XYZType tag (sig 'XYZ ')."""
    return b"XYZ " + bytes(4) + s15f16(x) + s15f16(y) + s15f16(z)


def text_tag(text: str) -> bytes:
    """Build a textType tag (sig 'text')."""
    return b"text" + bytes(4) + text.encode("ascii") + b"\x00"


def mluc_tag(text: str, lang: str = "en", country: str = "US") -> bytes:
    """Build a multiLocalizedUnicodeType tag (sig 'mluc')."""
    sig = b"mluc"
    reserved = bytes(4)
    record_count = u32(1)
    record_size = u32(12)
    lang_bytes = lang.encode("ascii")
    country_bytes = country.encode("ascii")
    utf16 = text.encode("utf-16-be")
    str_len = u32(len(utf16))
    str_offset = u32(28)  # 4+4+4+4+2+2+4+4 = 28
    return (sig + reserved + record_count + record_size
            + lang_bytes + country_bytes + str_len + str_offset + utf16)


def curve_tag_gamma(gamma: float, npoints: int = 1024) -> bytes:
    """
    Build a curveType tag encoding output = input ^ gamma.

    This is the TRC (tone response curve) for a display profile.
    The curve maps encoded signal → linear light.
    gamma < 1.0 → brighter (matches xrandr gamma < 1.0 behaviour).
    """
    sig = b"curv"
    reserved = bytes(4)
    count = u32(npoints)
    pts = bytearray()
    for i in range(npoints):
        x = i / (npoints - 1)
        y = 0.0 if x == 0.0 else x ** gamma
        pts += struct.pack(">H", int(round(min(y, 1.0) * 65535)))
    return sig + reserved + count + bytes(pts)


def vcgt_tag(r_gamma: float, g_gamma: float, b_gamma: float,
             npoints: int = 256) -> bytes:
    """
    Build a vcgt (Video Card Gamma Table) tag.

    colord reads this and loads it directly to the GPU gamma ramp,
    equivalent to what xrandr --gamma does on X11.

    Uses 'table' formula (type 0) with uint16 values.
    """
    sig = b"vcgt"
    reserved = bytes(4)
    # type 0 = table
    vcgt_type = u32(0)
    channels = u16(3)
    count_per_channel = u16(npoints)
    bits_per_entry = u16(16)

    table = bytearray()
    for gamma in (r_gamma, g_gamma, b_gamma):
        for i in range(npoints):
            x = i / (npoints - 1)
            y = 0.0 if x == 0.0 else x ** gamma
            table += struct.pack(">H", int(round(min(y, 1.0) * 65535)))

    return (sig + reserved + vcgt_type
            + channels + count_per_channel + bits_per_entry
            + bytes(table))


# ---------------------------------------------------------------------------
# Full profile assembly
# ---------------------------------------------------------------------------

def build_display_profile(
    r_gamma: float,
    g_gamma: float,
    b_gamma: float,
    description: str = "Custom Gamma",
) -> bytes:
    """
    Build a minimal ICC v2 display profile with per-channel gamma correction.

    Colorimetry: sRGB primaries (Rec.709), D65 whitepoint.
    Tags included: desc, cprt, wtpt, rXYZ/gXYZ/bXYZ, rTRC/gTRC/bTRC, vcgt.
    """

    # -- Tag payloads ---------------------------------------------------------

    tag_desc   = mluc_tag(description)
    tag_cprt   = text_tag("No copyright")
    tag_wtpt   = xyz_tag(0.9505, 1.0000, 1.0890)   # D65

    # Rec.709 / sRGB primaries in XYZ (relative colorimetric, D65 adapted)
    tag_rXYZ   = xyz_tag(0.4361, 0.2225, 0.0139)
    tag_gXYZ   = xyz_tag(0.3851, 0.7169, 0.0971)
    tag_bXYZ   = xyz_tag(0.1431, 0.0606, 0.7141)

    tag_rTRC   = curve_tag_gamma(r_gamma)
    tag_gTRC   = curve_tag_gamma(g_gamma)
    tag_bTRC   = curve_tag_gamma(b_gamma)
    tag_vcgt   = vcgt_tag(r_gamma, g_gamma, b_gamma)

    # ICC tags must be 4-byte aligned
    def pad4(data: bytes) -> bytes:
        rem = len(data) % 4
        return data + bytes((4 - rem) % 4)

    # Each entry: (signature, data_bytes)
    named_tags = [
        (b"desc", tag_desc),
        (b"cprt", tag_cprt),
        (b"wtpt", tag_wtpt),
        (b"rXYZ", tag_rXYZ),
        (b"gXYZ", tag_gXYZ),
        (b"bXYZ", tag_bXYZ),
        (b"rTRC", tag_rTRC),
        (b"gTRC", tag_gTRC),
        (b"bTRC", tag_bTRC),
        (b"vcgt", tag_vcgt),
    ]

    # -- Lay out tag data, deduplicate TRC if channels are identical ----------
    #    (saves space; ICC spec allows shared offsets)
    unique_data: list[bytes] = []          # padded tag payloads
    tag_indices: list[int]   = []          # index into unique_data per tag

    payload_cache: dict[bytes, int] = {}
    for _, data in named_tags:
        padded = pad4(data)
        if padded not in payload_cache:
            payload_cache[padded] = len(unique_data)
            unique_data.append(padded)
        tag_indices.append(payload_cache[padded])

    # -- Compute offsets ------------------------------------------------------
    header_size   = 128
    tag_count     = len(named_tags)
    tag_table_size = 4 + tag_count * 12   # count + N*(sig+offset+size)

    data_start = header_size + tag_table_size
    offsets: list[int] = []
    pos = data_start
    seen_offsets: dict[int, int] = {}   # unique_data index -> absolute offset
    for idx in tag_indices:
        if idx not in seen_offsets:
            seen_offsets[idx] = pos
            pos += len(unique_data[idx])
        offsets.append(seen_offsets[idx])

    total_size = pos

    # -- Assemble tag table ---------------------------------------------------
    tag_table = u32(tag_count)
    for i, (sig, data) in enumerate(named_tags):
        original_len = len(named_tags[i][1])  # unpadded original length
        tag_table += sig + u32(offsets[i]) + u32(original_len)

    # -- Build tag data block -------------------------------------------------
    data_block_parts: dict[int, bytes] = {}
    for idx, abs_off in seen_offsets.items():
        data_block_parts[abs_off] = unique_data[idx]
    data_block = b"".join(v for _, v in sorted(data_block_parts.items()))

    # -- Build 128-byte header ------------------------------------------------
    profile_class = b"mntr"   # display device
    color_space   = b"RGB "
    pcs            = b"XYZ "

    # Date/time: now (UTC)
    now = datetime.datetime.now(datetime.timezone.utc)
    date_time = struct.pack(">6H",
        now.year, now.month, now.day,
        now.hour, now.minute, now.second)

    # ICC v2.4.0 → encoded as 0x02400000
    version = struct.pack(">4B", 0x02, 0x40, 0x00, 0x00)

    # Rendering intent: perceptual (0)
    rendering_intent = u32(0)

    # Illuminant: D50 (PCS illuminant, fixed by ICC spec)
    pcs_illuminant = s15f16(0.9642) + s15f16(1.0000) + s15f16(0.8249)

    # Profile ID (we'll leave as zeros)
    profile_id = bytes(16)

    # Creator: "PYTH"
    creator = b"PYTH"
    # Primary platform: 0 (none / unrecognized)
    primary_platform = bytes(4)

    header = (
        u32(total_size)         # 0-3:   profile size (filled in)
        + b"PYTH"               # 4-7:   preferred CMM (arbitrary)
        + version               # 8-11:  version 2.4.0
        + profile_class         # 12-15: 'mntr'
        + color_space           # 16-19: 'RGB '
        + pcs                   # 20-23: 'XYZ '
        + date_time             # 24-35: creation date/time
        + b"acsp"               # 36-39: file signature
        + primary_platform      # 40-43: platform
        + bytes(4)              # 44-47: flags
        + b"PYTH"               # 48-51: device manufacturer
        + bytes(4)              # 52-55: device model
        + bytes(8)              # 56-63: device attributes
        + rendering_intent      # 64-67
        + pcs_illuminant        # 68-79
        + creator               # 80-83
        + profile_id            # 84-99
        + bytes(28)             # 100-127: reserved
    )

    assert len(header) == 128, f"Header is {len(header)} bytes, expected 128"

    profile = header + tag_table + data_block
    assert len(profile) == total_size, \
        f"Profile size mismatch: {len(profile)} vs {total_size}"

    return profile


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_gamma_triple(s: str) -> tuple[float, float, float]:
    """Parse '0.80:0.80:0.93' or '0.80' (applied to all channels)."""
    parts = s.split(":")
    if len(parts) == 1:
        v = float(parts[0])
        return v, v, v
    elif len(parts) == 3:
        return float(parts[0]), float(parts[1]), float(parts[2])
    else:
        raise ValueError(f"Expected single value or R:G:B triple, got: {s!r}")


XRANDR_BASE_GAMMA = 2.2


def xrandr_to_icc_gamma(v: float) -> float:
    """
    Convert an xrandr-style gamma multiplier to an absolute ICC exponent.

    xrandr gamma is a *multiplier* applied on top of an assumed 2.2 base:
        icc_exponent = base_gamma * xrandr_value
    So xrandr 1.0  → 2.2 (no change from native display gamma)
       xrandr 0.80 → 1.76 (raises midtones / brightens)
       xrandr 1.25 → 2.75 (lowers midtones / darkens)
    """
    return XRANDR_BASE_GAMMA * v


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--gamma",
        metavar="R:G:B",
        help="Per-channel gamma as 'R:G:B' or single value for all channels.",
    )
    parser.add_argument(
        "--red",   type=float, metavar="GAMMA",
        help="Red channel gamma (overrides --gamma red component).",
    )
    parser.add_argument(
        "--green", type=float, metavar="GAMMA",
        help="Green channel gamma.",
    )
    parser.add_argument(
        "--blue",  type=float, metavar="GAMMA",
        help="Blue channel gamma.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help=(
            "Treat gamma values as absolute ICC exponents rather than "
            "xrandr-style multipliers (skips the ×2.2 scaling). "
            "Use this if you already have the final exponent you want."
        ),
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        required=True,
        help="Output .icc file path.",
    )
    parser.add_argument(
        "--description", "-d",
        metavar="TEXT",
        default=None,
        help="Profile description string (defaults to auto-generated).",
    )

    args = parser.parse_args()

    # Determine RGB gamma values
    if args.gamma:
        r, g, b = parse_gamma_triple(args.gamma)
    else:
        r = g = b = 1.0  # sane default

    if args.red   is not None: r = args.red
    if args.green is not None: g = args.green
    if args.blue  is not None: b = args.blue

    # Store the original xrandr-style values for display, then scale unless --raw
    r_in, g_in, b_in = r, g, b
    if not args.raw:
        r, g, b = xrandr_to_icc_gamma(r), xrandr_to_icc_gamma(g), xrandr_to_icc_gamma(b)

    if args.description:
        desc = args.description
    elif args.raw:
        desc = f"Gamma (raw) R:{r:.4f} G:{g:.4f} B:{b:.4f}"
    else:
        desc = f"Gamma (xrandr) R:{r_in:.4f} G:{g_in:.4f} B:{b_in:.4f}"

    profile = build_display_profile(r, g, b, description=desc)

    with open(args.output, "wb") as f:
        f.write(profile)

    print(f"Written: {args.output}  ({len(profile)} bytes)")
    if args.raw:
        print(f"  Mode: raw (absolute exponents)")
        print(f"  R exponent: {r}  G exponent: {g}  B exponent: {b}")
    else:
        print(f"  Mode: xrandr (multiplied by {XRANDR_BASE_GAMMA})")
        print(f"  Input  R:{r_in}  G:{g_in}  B:{b_in}")
        print(f"  ICC    R:{r:.4f}  G:{g:.4f}  B:{b:.4f}")
    print(f"  Description: {desc}")


if __name__ == "__main__":
    main()
