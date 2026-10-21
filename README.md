# Handheld Control (Decky plugin)

TDP, fan, RGB and battery (charge limit + charge bypass) control for:

| | AYANEO NEXT 2 (Bazzite 44) | ONEXPLAYER 3 (CachyOS) |
|---|---|---|
| DMI | vendor `AYA`, board `AB09` | vendor `ONE-NETBOOK`, board `ONEXPLAYER 3` |
| TDP | firmware-attributes (`amd-dptc`) → `ryzenadj` fallback, 10–80 W, boost up to 85 W | Intel RAPL powercap (MSR and MMIO package zones), 3–40 W, PL2 up to 42 W |
| Fan | hwmon `ayaneo_ec` → raw EC fallback (0x4A/0x4B, 0–100) | hwmon `oxp_ec` → raw EC fallback (0x4A/0x4B, 0–184) |
| Charge limit | **emulated**: the EC can only inhibit charging, so the plugin inhibits at the limit and resumes 3% below it | native `charge_control_end_threshold` → EC 0xA3 |
| Charge bypass | Off / Always (EC 0x1E, `0xAA`/`0x55`) | Off / While awake / Always (`charge_behaviour` → EC 0xA4) |
| RGB | stick rings over vendor HID `1c4f:0002` | `hid-oxp` LED class → vendor HID `1a86:fe00` |

Fan modes: **Auto** (firmware), **Curve** (quiet / balanced / performance) or
**Fixed** %. In every manual mode the fan is forced to 100% above 90 °C, and
released below 80 °C.

Nothing is changed until you enable a section. Settings persist in
`~/homebrew/settings/Handheld Control/settings.json` and are re-applied at
boot, after resume, and (for TDP) when AC is plugged or unplugged. When the
plugin unloads, the fan goes back to firmware auto and the emulated charge
inhibit is cleared.

> **Status: untested on hardware.** Packet formats and register maps were
> checked against the kernel drivers and HHD, but the plugin has not yet run on
> a NEXT 2 or ONEXPLAYER 3. Enable one section at a time and watch the result.
> Please report what works in an issue.

## Install

In Decky, enable **Settings → General → Developer mode**, then use
**Developer → Install plugin from URL** with:

```
https://github.com/kupecjan1980-pixel/handheld-control/releases/download/v0.1.0/handheld-control-0.1.0.zip
```

To build it yourself, run `./package.sh` (needs node, npm and zip). Copy
`out/handheld-control-<version>.zip` to the device and use **Install plugin
from ZIP**.

## Before you use it

**Turn off HHD's overlapping features.** Bazzite ships Handheld Daemon (HHD),
which also drives TDP, fan, RGB and charging on both devices. If both run,
they will fight. In HHD, disable TDP control and RGB, and leave fan and battery
on their defaults, or stop it (`systemctl disable --now hhd@$USER`). This also
applies to PowerStation/InputPlumber on CachyOS Handheld. The plugin shows a
warning when it sees one of them running.

**Kernel drivers vs raw EC.** The plugin prefers kernel interfaces. If they
are missing, it writes the EC through `ec_sys`:

- `oxpec` supports the ONEXPLAYER 3 in pdx86 `for-next` (mapped like the G1 i).
  Older CachyOS kernels fall back to EC.
- Mainline `ayaneo-ec` does not match the NEXT 2 (its vendor string is `AYA`,
  not `AYANEO`). On a kernel without a NEXT 2 entry, fan and charge control
  need the EC fallback.
- The EC fallback **does not work with Secure Boot enabled**, because kernel
  lockdown blocks `ec_sys`. The panel shows why when a section is unavailable.

**TDP on the NEXT 2** uses whichever `firmware-attributes` device exposes
`ppt_pl1_spl` (the `amd-dptc` driver), and switches the platform profile to
`custom` first. Without it, the plugin uses `ryzenadj` if it is installed.
Strix Halo support in ryzenadj depends on its version.

**RGB on the NEXT 2**: the AYANEO RGB report also carries joystick
sensitivity and vibration bytes. The plugin sends HHD's defaults (100%
sensitivity, medium vibration).

## Layout

```
main.py                 Decky entry point (async API + 2 s background loop)
py_modules/hhctl/
  device.py             DMI detection and per-device register maps
  tdp.py                firmware-attributes / RAPL / ryzenadj
  fan.py                hwmon / EC fan, curves, temperature source
  battery.py            charge limit (native/EC/emulated) and bypass
  rgb.py                AYANEO HID, hid-oxp LED, OXP HID v1
  ec.py                 ec_sys access with lockdown checks
  controller.py         settings, apply, tick, resume detection, UI state
src/index.tsx           Quick Access panel
```

Register maps and HID protocols are taken from the Linux `oxpec`,
`ayaneo-ec` and `hid-oxp` drivers and from Handheld Daemon (LGPL-2.1).
Licensed GPL-2.0-or-later.
