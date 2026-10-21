# Handheld Control (Decky plugin)

TDP, EPP, fan, RGB, battery (charge limit + charge bypass), brightness and display
stability control for:

| | AYANEO NEXT 2 (Bazzite 44) | ONEXPLAYER 3 (CachyOS) |
|---|---|---|
| DMI | vendor `AYA`, board `AB09` | vendor `ONE-NETBOOK`, board `ONEXPLAYER 3` |
| TDP | firmware-attributes (`amd-dptc`) → `ryzenadj` fallback, 10–80 W, boost up to 85 W | Intel RAPL powercap (MSR and MMIO package zones), 3–40 W, PL2 up to 42 W |
| EPP | `amd-pstate-epp` | `intel_pstate` (HWP) |
| CPU boost | cpufreq `boost` | `intel_pstate/no_turbo` |
| Preferred cores | scheduler ITMT switch (amd-pstate prefcore) | not applicable on hybrid CPUs |
| Fan | hwmon `ayaneo_ec` → raw EC fallback (0x4A/0x4B, 0–100) | hwmon `oxp_ec` → raw EC fallback (0x4A/0x4B, 0–184) |
| Charge limit | **emulated**: the EC can only inhibit charging, so the plugin inhibits at the limit and resumes 3% below it | native `charge_control_end_threshold` → EC 0xA3 |
| Charge bypass | Off / Always (EC 0x1E, `0xAA`/`0x55`) | Off / While awake / Always (`charge_behaviour` → EC 0xA4) |
| RGB | stick rings over vendor HID `1c4f:0002` | `hid-oxp` LED class → vendor HID `1a86:fe00` |
| GPU readout | amdgpu power, clock, VRAM and GTT | GPU power (xe hwmon energy or RAPL uncore), GPU memory from DRM fdinfo, clock |
| Display | brightness slider | brightness slider, PSR / Panel Replay / DC toggles |

Fan modes: **Auto** (firmware), **Curve** (quiet / balanced / performance) or
**Fixed** %. In every manual mode the fan is forced to 100% above 90 °C, and
released below 80 °C.

Nothing is changed until you enable a section. Settings persist in
`~/homebrew/settings/Handheld Control/settings.json` and are re-applied at
boot, after resume, and (for TDP) when AC is plugged or unplugged. When the
plugin unloads, the fan goes back to firmware auto and the emulated charge
inhibit is cleared.

> **Status: early.** Reported working so far: TDP, fan, battery and RGB on the
> ONEXPLAYER 3 (EC via `ec_sys`), and TDP and RGB on the NEXT 2. NEXT 2 fan and
> battery through `/dev/port` (added in 0.3.0) and the 0.3.0 display features
> are not yet confirmed on hardware. Please report what works in an issue.

## Install

In Decky, enable **Settings → General → Developer mode**, then use
**Developer → Install plugin from URL** with:

```
https://github.com/kupecjan1980-pixel/handheld-control/releases/download/v0.5.5/handheld-control-0.5.5.zip
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
are missing, it can write the EC directly, but only after you turn on
**Advanced → Allow raw EC writes** (off by default). It uses the first of
these that works:

1. `ec_sys` debugfs. The kernel serialises it with its own EC traffic, so it's
   the safest. CachyOS has it; Bazzite's kernel does not.
2. Memory-mapped EC RAM through `/dev/mem` (NEXT 2: `0xFE800400`). Used only
   if it matches port reads byte for byte on the probe registers.
3. The ACPI EC port protocol on `0x62`/`0x66` through `/dev/port`. This isn't
   serialised with the kernel's EC driver, so transactions retry on timeout,
   stale bytes are discarded, and every write is verified.

Driver notes:

- `oxpec` supports the ONEXPLAYER 3 in pdx86 `for-next` (mapped like the G1 i).
  Older CachyOS kernels fall back to EC.
- Mainline `ayaneo-ec` does not match the NEXT 2 (its vendor string is `AYA`,
  not `AYANEO`). On a kernel without a NEXT 2 entry, fan and charge control
  need the EC fallback.
- The EC fallback **does not work with Secure Boot enabled**, because kernel
  lockdown blocks `ec_sys`, `/dev/mem` and `/dev/port`. The panel shows why
  when a section is unavailable.

**EC safety.** Raw EC writes are guarded:

- At startup each mapped register is read. If it holds a value the map
  doesn't expect (for example, the NEXT 2 charge register 0x1E isn't
  `0xAA`/`0x55`), EC control for that feature is disabled and the panel says
  why. Nothing is written.
- Before every write the register is checked again, and afterwards it is
  read back. A mismatch turns EC control off for that feature.
- While the fan is in manual mode, a separate watchdog runs as the systemd
  unit `hhctl-fan-watchdog`. It puts the fan back to firmware auto if the
  plugin stops sending heartbeats for 20 s (crash, hang, kill or Decky
  restart). The timer uses the monotonic clock, so suspend doesn't trigger
  it. The plugin also returns the fan to auto on unload, on exit and on
  SIGTERM.

**TDP on the NEXT 2** uses whichever `firmware-attributes` device exposes
`ppt_pl1_spl` (the `amd-dptc` driver), and switches the platform profile to
`custom` first. Without it, the plugin uses `ryzenadj` if it is installed.
Strix Halo support in ryzenadj depends on its version.

**EPP (Energy Performance Preference)** tells the CPU how to trade speed for
power inside the TDP limit: Performance, Balance performance, Balance power or
Power saver. Both drivers pin EPP to `performance` under the `performance`
governor, so any other choice switches the governor to `powersave`. If the
pstate driver is in passive/guided mode, it's switched to active first.
Turning the toggle off restores the mode, governors and EPP values from when
the plugin started. power-profiles-daemon and tuned rewrite EPP when the
profile or AC state changes, so the plugin checks every 10 s and re-applies
your choice.

**Three different "boosts"** live in the TDP section:

- **Power boost** lets the package briefly exceed the sustained limit
  (PL2 / fast PPT).
- **CPU boost (turbo)** allows clocks above base frequency. Turning it off
  saves a lot of power and heat, at the cost of single-thread speed.
- **EPP** is a hint for how eagerly the CPU ramps up within those limits. It
  isn't a boost switch.

**Preferred cores** turns on the scheduler's ITMT switch
(`/sys/kernel/debug/x86/sched_itmt_enabled`), which places work on the cores
the firmware ranks fastest through CPPC. On AMD this needs amd-pstate's
prefcore (`/sys/devices/system/cpu/amd_pstate/prefcore` = enabled). Hybrid
Intel CPUs, such as the ONEXPLAYER 3's Panther Lake, don't use ITMT; the
scheduler uses core capacities instead, so the toggle shows as unavailable.
It's also blocked under Secure Boot lockdown. Boost and preferred cores are
left alone until you change them, then re-applied at boot and every 10 s.

**GPU readout.** The TDP section shows package power, GPU power, GPU memory
and GPU clock. The TDP limit is a package limit, so CPU and GPU share it.
Steam's overlay runs unprivileged, so on Intel it can't read GPU power (RAPL
counters are root-only) and shows no VRAM (integrated Arc has none). The
plugin can't change what the overlay shows; use the panel instead.

**Display.** The brightness slider writes `/sys/class/backlight/*` directly,
so it works in Game Mode even when gamescope's own slider doesn't. It stops
at 5%, so the panel never goes fully dark. On Intel GPUs (`xe`/`i915`) two
toggles help with the screen blanking or not waking:

- **Fix screen blanking** adds `xe.enable_psr=0 xe.enable_panel_replay=0`.
- **Disable display C-states** adds `xe.enable_dc=0`. Stronger, but uses more
  battery.

If the slider doesn't change the screen in Game Mode, press **Run brightness
test**. It steps the backlight to 25%, 75% and back while checking whether
the value sticks, asks whether the screen visibly changed, and saves a report
with PSR status, driver settings and a read-only dump of the DRM display state
to `~/handheld-control-brightness-game.txt` or `-desktop.txt`, depending on
the mode. Running it in both modes and comparing the two shows what gamescope
sets differently. Paste both files into an issue.

> The test never switches PSR at runtime. Version 0.5.2 did, through the
> driver's debugfs, and on the ONEXPLAYER 3 that turned the panel off until a
> second reboot. Don't use 0.5.2's brightness test.

**If the screen stays black after a reboot** with a display toggle on: hold the
power button for about 10 s to power off fully, then boot again. If it's still
black, press **E** at the Limine boot menu, delete `xe.enable_psr=0
xe.enable_panel_replay=0` (and `xe.enable_dc=0`) from the kernel command line
and boot. Then turn the toggle off in the plugin so the change is undone
permanently.

The display toggles edit `KERNEL_CMDLINE[default]` in `/etc/default/limine` (then run
`limine-update`), GRUB, or sdboot-manage, or use `rpm-ostree kargs` on
Bazzite. Only those parameters are touched. The file is backed up first
(`.hhctl-orig` once, `.hhctl.bak` each time) and restored if regenerating the
boot entries fails. For Limine, the plugin then reads the generated
`limine.conf` to check that a boot entry really carries the change. If
`limine-update` didn't rebuild it, it tries `limine-mkinitcpio`. If neither
works, the file is restored and the panel shows the error. The panel shows
*Reboot required* until the running kernel has the new values, and *Saved, but
not in the boot entries yet* with an **Apply to boot entries** button if the
file and the entries disagree.

**RGB on the NEXT 2**: the AYANEO RGB report also carries joystick
sensitivity and vibration bytes. The plugin sends HHD's defaults (100%
sensitivity, medium vibration).

## Layout

```
main.py                 Decky entry point (async API + 2 s background loop)
py_modules/hhctl/
  device.py             DMI detection and per-device register maps
  tdp.py                firmware-attributes / RAPL / ryzenadj
  epp.py                energy performance preference (amd-pstate / intel_pstate)
  cpu.py                CPU boost (turbo) and preferred-core scheduling
  fan.py                hwmon / EC fan, curves, temperature source
  battery.py            charge limit (native/EC/emulated) and bypass
  rgb.py                AYANEO HID, hid-oxp LED, OXP HID v1
  ec.py                 guarded EC access: ec_sys / MMIO / port I/O
  gpu.py                GPU + package power, GPU memory, clock
  backlight.py          brightness
  diag.py               brightness test + report
  kargs.py              kernel parameters via Limine / GRUB / sdboot / rpm-ostree
  proc.py               run system tools with the host environment, not Decky's
  watchdog.py           standalone fan watchdog (own systemd unit)
  controller.py         settings, apply, tick, resume detection, UI state
src/index.tsx           Quick Access panel
tests/                  backend test suites (tests/run.sh)
```

Register maps and HID protocols are taken from the Linux `oxpec`,
`ayaneo-ec` and `hid-oxp` drivers and from Handheld Daemon (LGPL-2.1).
Licensed GPL-2.0-or-later.
