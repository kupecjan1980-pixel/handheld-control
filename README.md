# Handheld Control (Decky plugin)

TDP, EPP, fan, RGB, battery (charge limit + charge bypass), brightness and display
stability control for:

| | AYANEO NEXT 2 (Bazzite 44) | ONEXPLAYER 3 (CachyOS) |
|---|---|---|
| DMI | vendor `AYA`, board `AB09` | vendor `ONE-NETBOOK`, board `ONEXPLAYER 3` |
| TDP | firmware-attributes (`amd-dptc`) → `ryzenadj` fallback, 10–80 W, boost up to 85 W | Intel RAPL powercap (MSR and MMIO package zones), 3–40 W, PL2 up to 42 W, PL4 kept at 84 W or higher; platform profile held at `performance` (via power-profiles-daemon when running) while TDP control is on |
| EPP | `amd-pstate-epp` | `intel_pstate` (HWP) |
| CPU boost | cpufreq `boost` | `intel_pstate/no_turbo` |
| CPU clock limit | cpufreq `scaling_max_freq` (amd-pstate) | cpufreq `scaling_max_freq` (intel_pstate), clamped per P-/E-core type |
| GPU clock | Auto / Min / Max via `power_dpm_force_performance_level`; Manual via `pp_od_clk_voltage` when the kernel exposes it | Auto / Min / Max / Manual via xe `freq0/min_freq`, `max_freq` (RPn–RP0) |
| Preferred cores | scheduler ITMT switch (amd-pstate prefcore) | not applicable on hybrid CPUs |
| Fan | hwmon `ayaneo_ec` → raw EC fallback (0x4A/0x4B, 0–100) | hwmon `oxp_ec` → raw EC fallback (0x4A/0x4B, 0–184) |
| Charge limit | **emulated**: the EC can only inhibit charging, so the plugin inhibits at the limit and resumes 3% below it | native `charge_control_end_threshold` → EC 0xA3 |
| Charge bypass | Off / Always (EC 0x1E, `0xAA`/`0x55`) | Off / While awake / Always (`charge_behaviour` → EC 0xA4) |
| RGB | stick rings over vendor HID `1c4f:0002` | `hid-oxp` LED class → vendor HID `1a86:fe00` |
| GPU readout | amdgpu power, clock, VRAM and GTT | GPU power (xe hwmon energy or RAPL uncore), GPU memory from DRM fdinfo, clock |
| Display | brightness slider | brightness slider, PSR / Panel Replay / DC toggles |

**GPU clock** locks the GPU at its lowest (Min) or highest (Max) clock, or at
a MHz value you pick (Manual). **Limit CPU clock** sets a ceiling for the CPU.
Neither adds power: with a fixed TDP, they move the shared budget between CPU
and GPU. Limiting the CPU clock (or turning CPU boost off) and setting the GPU
to Max shifts it toward the GPU. On Intel, the GPU section shows what is
holding the GPU back (power limit, temperature) when it can't reach the
requested clock. Both go back to the driver defaults when the plugin unloads.

**Controller → Record controller input** (30 s) lists every input and HID
device and records what they send while you press the extra buttons and touch
the touchpads. It only reads, nothing is grabbed or remapped, and saves
`~/handheld-control-input-<desktop|game>.txt`. Controller remapping (Steam
Deck controller emulation, or Xbox Elite with the touchpads as a mouse) will
be built from these reports.

**On a desktop PC** (for testing): `desktop/hhc-desktop` runs the plugin's
real backend on the PC and opens the same panel in its own Chromium window
(`desktop/install.sh` adds it to the app launcher). It asks for your sudo
password in a terminal so it can change settings, or runs read-only with
`--user`. Closing the window puts everything back, as unloading the plugin
does. The panel is built from `src/index.tsx` with browser stand-ins for
Decky's UI (`desktop/shims`). It listens on 127.0.0.1 only, and every request
needs a random token made at start. TDP on AMD PCs needs `ryzenadj`.

**Updates** (last section): **Check for updates** lists this plugin's GitHub
releases (pre-releases included); pick any version and install it, newer or
older. The zip must come from this repository's releases; it's checked,
swapped in, and Decky's plugin loader restarts to load it. Settings stay.
Versions before 0.10.7 have no updater, so going back to one means
installing from the release URL to return.

**Controller → Extra buttons** (on by default): tapping AYN opens
Handheld Control in the Quick Access Menu, holding it (0.5 s) opens the
regular Quick Access Menu, and the button next to it (Win+D) opens the Steam
keyboard. On the ONEXPLAYER 3 the home button (Ctrl+Win+F17) works like AYN
and the keyboard button (Ctrl+Win+O) opens the Steam keyboard. ☰ stays the
gamepad's Start, so games keep it. The plugin only
reads those keys, so nothing is taken from Steam or games. The back buttons
and the touchpads need a virtual controller; that comes later.

Fan modes: **Auto** (firmware), **Curve** (quiet / balanced / performance) or
**Fixed** %. In every manual mode the fan is forced to 100% above 90 °C, and
released below 80 °C.

The title bar shows the plugin version. A header fixed at the top of the panel shows the device name, TDP used / set, and a CPU/GPU table (clock, power, temperature, RAM and VRAM used / total, and what caps each); the settings scroll below it. The last section shows device notes and conflict warnings, and has a **Theme color** option (Default, Red, Blue, Green, Gold) that tints the plugin's panel, labels, toggles and sliders (RGB can follow it with **Match theme color**), a **background picture** chosen from thumbnails and adjustable (zoom, position, dimming), and a **Font** option: Default, or a free font (SIL Open Font License, bundled in `fonts/` with each license) in the style of a game: **Diablo** (Cinzel), **Matrix** (Share Tech Mono), **Doom** (Metal Mania), **Quake** (Grenze Gotisch), and **Fraktur** (UnifrakturMaguntia, German blackletter). The games' own lettering can't be redistributed. Each theme brings a font (Gold: Quake, Green: Matrix, Red: Diablo, Blue: Doom; Gold is the default), which can be changed afterwards, and tints all text in shades of its color.

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
https://github.com/kupecjan1980-pixel/handheld-control/releases/download/v0.10.16/handheld-control-0.10.16.zip
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

**First CCX only** (Ryzen chips with more than one CCX, such as the NEXT 2's
Strix Halo with two 8-core CCDs) takes every CPU outside CPU 0's CCX offline,
SMT siblings included. CCXs are told apart by their shared L3 cache; the layout
is cached so it's still known while CPUs are offline. It saves some power at
light loads, and heavily threaded games run slower. EPP is re-applied when the
cores come back, and unloading the plugin brings them back online. Not shown
on single-CCX CPUs (including the ONEXPLAYER 3).

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

> **Both are blocked on the ONEXPLAYER 3.** With PSR and Panel Replay off, its
> panel (Panel Replay with DSC) stays black after boot. Display C-states are
> untested there and blocked too. The toggles can still be turned off, to
> undo an earlier change.

> **Game Mode HDR switching is blocked on the ONEXPLAYER 3** (since 0.6.2).
> With HDR off, or when switching between HDR and SDR, its screen turned off
> and only a long power press brought it back, the same blackouts seen on
> Bazzite. HDR, Steam's default on CachyOS, keeps it stable. A mode saved by
> 0.6.0/0.6.1 is reset to Steam default and never re-applied.

**Game Mode brightness** (since 0.7.0) is how brightness works in Game Mode
on the ONEXPLAYER 3. HDR has to stay on there, and in HDR the panel ignores
the backlight. Instead, the slider sets gamescope's brightness for SDR content
inside HDR output (`GAMESCOPE_SDR_ON_HDR_CONTENT_BRIGHTNESS`, in nits; 40–500),
which covers Steam's UI and normal games. No display mode changes. It's set with
`xprop` on Steam's X display, as the session user. The first change is a trial
that must be confirmed with **Keep** within 20 s, or it reverts; after that the
slider applies directly. The value is re-applied after each new Game Mode
session and if Steam resets it. Turning it off returns to gamescope's default.

**Game Mode HDR.** On the ONEXPLAYER 3, gamescope drives the internal OLED in
HDR10 (connector colorspace `BT2020_RGB`) even though Steam shows no HDR toggle,
and in HDR the panel ignores the backlight. That's why brightness works in
Desktop Mode but not Game Mode. The **Game Mode HDR** option changes gamescope's
`hdr_enabled` / `hdr_content_driven` settings through `gamescopectl`:

- **Steam default:** leave it to Steam.
- **Only for HDR games:** SDR for Steam's UI and SDR games, so brightness works,
  and HDR only while an HDR game shows HDR content. Needs a gamescope with
  `hdr_content_driven`.
- **Off:** always SDR. Re-applied if Steam turns HDR back on.

Every change has to be confirmed within 20 s or it reverts by itself, so a
black screen fixes itself. While the plugin has HDR changed, it also keeps the
backlight at 5% or more: with HDR off, Steam's own slider reaches the panel for
the first time and can set it to near zero, which looks like the screen
turning off. The settings only last as long as gamescope runs, so
a reboot always returns to Steam's defaults. A confirmed choice is re-applied
about 15 s after each new Game Mode session starts.

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
  cpu.py                CPU boost (turbo), preferred cores, first-CCX-only
  fan.py                hwmon / EC fan, curves, temperature source
  battery.py            charge limit (native/EC/emulated) and bypass
  rgb.py                AYANEO HID, hid-oxp LED, OXP HID v1
  ec.py                 guarded EC access: ec_sys / MMIO / port I/O
  gpu.py                GPU + package power, GPU memory, clock
  backlight.py          brightness
  background.py         custom panel background picture, picture browser
  updater.py            install any GitHub release from the panel
desktop/              run the plugin on a desktop PC (hhc-desktop, server.py, shims/)
  buttons.py            extra buttons (read-only) -> Quick Access / Steam keyboard
  diag.py               brightness test + report
  gamescope.py          Game Mode HDR (gamescopectl) and brightness (xprop)
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
