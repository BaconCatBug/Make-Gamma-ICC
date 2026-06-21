# Make-Gamma-ICC
Simple python script to make simple ICC profiles that change gamma values and nothing else for Wayland, replicating the xrandr --gamma R:G:B command

# Disclaimer: This was written with Claude AI. It's a simple one shot script, so calm thy mammeries.

# make_gamma_icc.py

`python3 make_gamma_icc.py -h` for in-script help.

Generate ICC profiles from xrandr-style per-channel gamma values.

No external dependencies beyond Python stdlib.

## Usage

```
python3 make_gamma_icc.py --output monitor.icc --red 0.80 --green 0.80 --blue 0.93
python3 make_gamma_icc.py --output monitor.icc --gamma 0.80:0.80:0.93
python3 make_gamma_icc.py --output monitor.icc --gamma 0.80        # same value for all channels
python3 make_gamma_icc.py --output monitor.icc --gamma 1.76 --raw  # bypass ×2.2 scaling
```

## Gamma semantics

By default, gamma values are treated as xrandr-style **multipliers** applied on top of an assumed display gamma of 2.2. The ICC exponent written into the profile is:

```
icc_exponent = 2.2 × input_value
```

| xrandr value | ICC exponent | Effect |
|---|---|---|
| `1.0` | `2.2` | No correction (native display gamma) |
| `0.80` | `1.76` | Raises midtones / brightens |
| `0.93` | `2.05` | Slight brightening |
| `1.25` | `2.75` | Lowers midtones / darkens |

Use `--raw` to skip the ×2.2 scaling and write the supplied value directly as the ICC exponent (useful if you already know the absolute exponent you want).

## Generated profile

The output profile uses:

- Standard sRGB colorimetry (D65 whitepoint, Rec.709 primaries)
- Per-channel power-law TRC matching the computed exponent
- `vcgt` tag with the same per-channel LUT (for GPU gamma ramp via `colord`)

## Options

| Option | Description |
|---|---|
| `--gamma R:G:B` | Per-channel gamma as `R:G:B`, or a single value applied to all channels |
| `--red GAMMA` | Red channel gamma (overrides `--gamma` red component) |
| `--green GAMMA` | Green channel gamma |
| `--blue GAMMA` | Blue channel gamma |
| `--raw` | Treat gamma values as absolute ICC exponents, skipping the ×2.2 scaling |
| `--output FILE`, `-o FILE` | Output `.icc` file path (**required**) |
| `--description TEXT`, `-d TEXT` | Profile description string (defaults to auto-generated) |
