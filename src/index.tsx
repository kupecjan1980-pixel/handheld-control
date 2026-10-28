import { addEventListener, callable, definePlugin, removeEventListener, toaster, useQuickAccessVisible } from "@decky/api";
import {
  getFocusNavController,
  getGamepadNavigationTrees,
  ButtonItem,
  DropdownItem,
  Field,
  Focusable,
  Navigation,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  gamepadDialogClasses,
  gamepadSliderClasses,
  staticClasses,
} from "@decky/ui";
import { Fragment, useEffect, useRef, useState } from "react";
import { FaMicrochip } from "react-icons/fa";

type Settings = {
  tdp: { enabled: boolean; watts: number | null; boost: boolean };
  epp: { enabled: boolean; value: string };
  cpu: {
    boost: boolean | null;
    prefcore: boolean | null;
    ccx_off: boolean | null;
    cap: boolean;
    cap_mhz: number | null;
    smt: boolean | null;
    hwp_boost: boolean | null;
    epp_boost: boolean | null;
    scx: string | null;
    scx_mode: string;
    cores: "all" | "fast" | "small" | null;
    temp_limit: number | null;
    memtweak?: boolean;
  };
  gpu: { mode: GpuMode; mhz: number | null; profile: string | null };
  fan: { mode: "auto" | "fixed" | "curve"; percent: number; curve: string; points: number[] | null };
  rgb: {
    enabled: boolean;
    mode: string;
    hue: number;
    sat: number;
    bright: number;
    effect: string | null;
    match_theme: boolean;
  };
  battery: { limit: number; bypass: string };
  ec: { enabled: boolean };
  games: { enabled: boolean };
  overlay: { stop_idle: boolean };
  system: {
    thp: string | null;
    swappiness: number | null;
    usb_awake: boolean;
    wifi_ps_off: boolean;
  };
  controller: {
    buttons: boolean;
    ip_target: string | null;
    swipe_keyboard?: boolean;
    desktop_mouse?: boolean | null;
    swipe_edge?: string | null;
    chord?: string | null;
  };
  sleep: { rgb_off: boolean; pad_wake: boolean | null; power_key?: boolean | null };
  ui: {
    theme: string;
    font: string;
    bg: string | null;
    bg_dim: number;
    bg_pos: BgPos;
    glass?: boolean;
  };
  sdr_brightness: { enabled: boolean; nits: number };
};

type GpuMode = "auto" | "min" | "max" | "manual";

// Which part of the background picture shows: CSS background-position (%) and zoom (%).
type BgPos = { x: number; y: number; zoom: number };

type Listing = {
  folder: string;
  parent: string | null;
  home: string;
  dirs: string[];
  files: { name: string; path: string; size: number }[];
  more: number;
  error?: string;
};

type State = {
  device: { name: string; board: string; vendor: string; notes: string[]; cpu: string | null; generic: boolean };
  conflicts: string[];
  settings: Settings;
  errors: Record<string, string>;
  tdp: {
    backend: string | null;
    min: number;
    max: number;
    default: number;
    current: number | null;
    profile: string | null;
  };
  fan: {
    backend: string | null;
    unavailable: string | null;
    curves: string[];
    custom_temps?: number[];
    custom_default?: number[];
    rpm: number | null;
    percent: number | null;
    temp: number | null;
    safety: boolean;
    watchdog: boolean;
    restarting?: boolean;
    note?: string | null;
  };
  battery: {
    limit_mode: string | null;
    bypass_options: string[];
    unavailable: string | null;
    capacity?: number | null;
    status?: string | null;
    power_w?: number | null;
    remaining_min?: number | null;
    inhibited?: boolean;
    ac?: boolean;
  };
  rgb: {
    backend: string | null;
    modes: string[];
    effects: string[];
    zone_test: string | null;
    zone_test_available: boolean;
  };
  ec: { supported: boolean; lockdown: string | null };
  version: string | null;
  home: string | null;
  perf: {
    cpu_mhz: number | null;
    cpu_peak_mhz: number | null;
    cpu_util: number | null;
    cpu_temp_c: number | null;
    ram_used_mb: number | null;
    ram_total_mb: number | null;
    tdp_w: number | null;
    limits: { cpu: string[]; gpu: string[] };
  };
  buttons: { available: boolean; unavailable: string | null; listening: string | null; help: string | null };
  chord?: { value: string; pads: number };
  desktop_mouse?: { on: boolean; pads: number; game_mode: boolean; game: boolean; error: string | null; keyboard?: string | null };
  swipe?: {
    available: boolean;
    listening: string | null;
    learning: boolean;
    last_touch: string | null;
    edge: string | null;
    last_swipe: string | null;
  };
  sleep: {
    rgb_off_available: boolean;
    pad_wake_available: boolean;
    power_key?: boolean;
    power_key_listening?: string[];
    pad_wake: boolean;
    note: string | null;
    pad_wake_text: string | null;
    last_wake: string | null;
  };
  inputplumber?: {
    devices: number;
    targets: string[];
    configurable: boolean;
    status: "missing" | "stopped" | "running" | null;
    failed: string | null;
    installed: boolean;
    install: string | null;
  };
  thermal: { available: boolean; range: [number, number] | null; backend: string | null };
  ai: { enabled: boolean; vars: Record<string, string> };
  memtweak?: { available: Record<string, string>; pending: string[] };
  games?: {
    enabled: boolean;
    game: { appid: string; name: string } | null;
    profile: boolean;
    count: number;
    auto: boolean;
    auto_available: boolean;
    auto_status: string | null;
    steam: boolean;
    desktop: boolean;
  };
  system?: {
    thp?: { current: string | null; choices: string[] };
    swappiness?: { current: number | null; choices: number[]; zram: boolean };
    usb_awake?: { current: number | null };
    wifi_ps_off?: { current: boolean; iface: string };
  };
  overlay?: { note: string | null; idle_since: string | null; game_mode: boolean };
  topuse?: { cpu: [string, number][]; gpu: [string, number][]; at: string } | null;
  rebar?: { state: "on" | "off" | "partial" | "n/a"; bar_mb: number; vram_mb: number | null; text: string } | null;
  upscale: {
    options: string[];
    on: Record<string, boolean>;
    rdna3: boolean;
    fsr4_why: string | null;
    tools: string[];
    optiscaler: string;
    optiscaler_fg: string;
    opti_upscaler: string;
  };
  lsfg?: { installed: boolean; ours: boolean; dll: string | null; branch: boolean; vars: Record<string, string>; busy: boolean };
  vram: {
    available: boolean;
    unavailable: string | null;
    options: { index: number; label: string }[];
    current: number | null;
    boot: number | null;
    now_mb: number | null;
    ram_gb: number;
    suggested_gb: number;
    bios_path: string;
  };
  gpu_mem: {
    available: boolean;
    bootloader?: string;
    choices?: number[];
    default?: number;
    current?: number | null;
    configured?: number | null;
    busy?: boolean;
  };
  update: {
    busy: boolean;
    error: string | null;
    message: string | null;
    releases: { tag: string; version: string; prerelease: boolean; date: string; url: string; notes: string }[];
  };
  input_capture: {
    running: boolean;
    left: number;
    summary: string | null;
    report: string | null;
  };
  gamescope: {
    running: boolean;
    modes: string[];
    mode: string;
    saved: string;
    pending: { mode: string; seconds: number } | null;
    hdr_active: boolean | null;
    blocked: string | null;
  };
  sdr_brightness: {
    available: boolean;
    enabled: boolean;
    nits: number;
    confirmed: boolean;
    min: number;
    max: number;
    current: number | null;
    pending: { seconds: number } | null;
  };
  epp: {
    available: boolean;
    unavailable: string | null;
    driver: string | null;
    options: string[];
    current: string | null;
  };
  cpu: Record<
    "boost" | "prefcore" | "ccx" | "smt" | "hwp_boost" | "epp_boost",
    { available: boolean; unavailable: string | null; on: boolean | null }
  > & {
    cores: {
      available: boolean;
      unavailable: string | null;
      fast: number;
      small: number;
      fast_cores: number;
      small_cores: number;
      fast_name: string;
      small_name: string;
      current: string | null;
    };
    scx: {
      available: boolean;
      unavailable: string | null;
      schedulers: { name: string; label: string }[];
      modes: string[];
      running: string | null;
    };
    ccx_keep: number;
    ccx_total: number;
    cap: {
      available: boolean;
      unavailable: string | null;
      min: number;
      max: number;
      limits: number[];
      avg: number | null;
    };
  };
  gpu: {
    driver: string | null;
    gpu_w: number | null;
    pkg_w: number | null;
    cpu_w: number | null;
    cpu_w_est: boolean;
    temp_c: number | null;
    freq_mhz: number | null;
    util: number | null;
    vram_mb: number | null;
    vram_total_mb: number | null;
    gtt_mb: number | null;
    mem_mb: number | null;
    profile: { available: boolean; options: string[]; current: string | null };
    pipes: { gc: string | null; state: "on" | "off" | "unsupported" | "unknown" } | null;
    clock: {
      available: boolean;
      unavailable: string | null;
      modes: GpuMode[];
      min: number;
      max: number;
      cur_min: number | null;
      cur_max: number | null;
      throttle: string[];
    };
  };
  display: {
    brightness: number | null;
    backlight: string | null;
    bootloader: string | null;
    busy: boolean;
    test: {
      running: boolean;
      phase: number | null;
      phases: number;
      verdict: string | null;
      report: string | null;
      answer: string | null;
    };
    message: string | null;
    toggles: Record<
      string,
      {
        params: string[];
        configured: boolean;
        active: boolean;
        in_entries: boolean | null;
        reboot: boolean;
        blocked: string | null;
      }
    >;
  };
};

type Section = keyof Settings;

const getState = callable<[], State>("get_state");
const getVersion = callable<[], string | null>("get_version");
const setBrightness = callable<[number], State>("set_brightness");
const setVram = callable<[number], State>("set_vram");
const setGpuMem = callable<[number], State>("set_gpu_mem");
const saveIpReport = callable<[], State>("save_ip_report");
const setAi = callable<[boolean], State>("set_ai");
const setUpscale = callable<[string, boolean], State>("set_upscale");
type GameUpscale = {
  proton: string | null;
  knows_vars: boolean;
  env: Record<string, string>;
  loaded: { fsr4: boolean; fidelityfx: boolean; xess: boolean; optiscaler: boolean; lsfg: boolean };
} | null;
const upscaleStatus = callable<[number], GameUpscale>("upscale_status");
const setLsfg = callable<[boolean], State>("set_lsfg");
const setGame = callable<[string | null, string | null], State>("set_game");
const saveGameProfile = callable<[boolean], State>("save_game_profile");
const setAutotune = callable<[boolean], State>("set_autotune");
const topUse = callable<[number], State>("top_use");

const vramSize = (mb: number | null) => (mb == null ? "?" : mb >= 1024 ? `${+(mb / 1024).toFixed(1)} GB` : `${mb} MB`);

// Dedicated VRAM (amdgpu carveout, else where to set it) and how much RAM the GPU may borrow.
type Drive = {
  path: string;
  model: string;
  size: number;
  fstype: string | null;
  label: string | null;
  mountpoint: string | null;
  permanent: boolean;
  ours: boolean;
  forced: boolean;
  in_steam?: boolean;
  can_mount: boolean;
  auto: boolean;
  empty: boolean;
  note: string | null;
};
type Drives = {
  drives: Drive[];
  error: string | null;
  done: { mountpoint: string; library: string } | null;
  msg?: string;
  nag?: { available: boolean; hidden: boolean };
};
const getDrives = callable<[], Drives>("get_drives");
const driveAction = callable<[string, string], Drives>("drive_action");

const driveSize = (b: number) => (b >= 1e12 ? `${(b / 1e12).toFixed(1)} TB` : `${Math.round(b / 1e9)} GB`);

// Steam's own call when it's there (Game Mode); else the backend adds it
// (through Steam's debugger, or into libraryfolders.vdf while Steam is closed).
async function addToSteam(library: string): Promise<string> {
  const sc = (window as any).SteamClient;
  try {
    if (sc?.InstallFolder?.AddInstallFolder) {
      await sc.InstallFolder.AddInstallFolder(library);
      return `Added to Steam: ${library}`;
    }
  } catch (e) {
    console.warn("hhctl: AddInstallFolder", e);
  }
  try {
    const r = await driveAction("library", library);
    return r.msg ?? r.error ?? `In Steam, open Settings › Storage › Add drive and pick ${library}`;
  } catch {
    return `In Steam, open Settings › Storage › Add drive and pick ${library}`;
  }
}

// Steam's library folders (Game Mode); null where Steam can't be asked (HHC Desktop).
async function steamLibraries(): Promise<string[] | null> {
  const sc = (window as any).SteamClient;
  try {
    if (!sc?.InstallFolder?.GetInstallFolders) return null;
    const list = await sc.InstallFolder.GetInstallFolders();
    // Each entry has its path in strFolderPath; take any path-like string to be safe.
    return (Array.isArray(list) ? list : []).flatMap((f: any) =>
      typeof f === "string" ? [f] : Object.values(f ?? {}).filter((x): x is string => typeof x === "string" && x.startsWith("/")),
    );
  } catch (e) {
    console.warn("hhctl: GetInstallFolders", e);
    return null;
  }
}

function DriveRows() {
  const [d, setD] = useState<Drives | null>(null);
  const [libs, setLibs] = useState<string[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [erase, setErase] = useState<string | null>(null); // asked to format this one: confirm
  const [msg, setMsg] = useState<string | null>(null);
  const refreshLibs = () => steamLibraries().then(setLibs);
  useEffect(() => {
    getDrives().then(setD).catch(() => setD(null));
    refreshLibs();
  }, []);
  if (!d || (d.drives.length === 0 && !d.error)) return null;
  const inSteam = (mp: string | null) =>
    !!mp && (!!libs?.some((l) => l === mp || l.startsWith(`${mp}/`)) || !!d?.drives.some((v) => v.mountpoint === mp && v.in_steam));
  const act = async (action: string, path: string) => {
    setBusy(path);
    setErase(null);
    setMsg(null);
    try {
      const r = await driveAction(action, path);
      setD(r);
      if (r.done) {
        setMsg(`${r.done.mountpoint} mounts at every start. ${await addToSteam(r.done.library)}`);
        refreshLibs();
        getDrives().then(setD).catch(() => undefined);
      }
      else if (!r.error && action === "unmount") setMsg("Unmounted; it no longer mounts at start (the data is still there)");
    } finally {
      setBusy(null);
    }
  };
  return (
    <PanelSection title="Drives">
      {d.drives.map((v) => {
        const name = `${v.label ?? v.model} · ${driveSize(v.size)}${v.fstype ? ` · ${v.fstype}` : ""}`;
        const where = v.mountpoint
          ? v.permanent
            ? `Mounted at ${v.mountpoint}, also at every start`
            : v.auto
              ? `Mounted at ${v.mountpoint}`
              : `Mounted at ${v.mountpoint} for now`
          : "Not mounted";
        const off = busy !== null;
        return (
          <Fragment key={v.path}>
            <PanelSectionRow>
              <Field
                label={name}
                description={[v.empty ? null : where, inSteam(v.mountpoint) ? "Steam library" : null, v.note, `${v.model} ${v.path}`]
                  .filter(Boolean)
                  .join(" · ")}
              />
            </PanelSectionRow>
            {(v.can_mount || v.permanent) && (
              <PanelSectionRow>
                <ToggleField
                  label="Mount at every start (no password)"
                  description={
                    v.permanent && !v.ours
                      ? "Already in /etc/fstab, set up outside HHC: change it there."
                      : `${v.ours ? "On: the system mounts it while starting, before Steam, so no password prompt or mount error." : "Turn on if it asks for a password or fails to mount after a start."} Done once, here; NTFS uses ntfs-3g, which also mounts a drive Windows didn't close cleanly. Turning it off unmounts it now and stops mounting it at start (the data stays).${
                          busy === v.path ? " Working…" : ""
                        }`
                  }
                  checked={v.permanent}
                  disabled={off || (v.permanent && !v.ours)}
                  onChange={(on) => act(on ? "mount" : "unmount", v.path)}
                />
              </PanelSectionRow>
            )}
            {v.fstype === "ntfs" && v.can_mount && !(v.permanent && !v.ours) && (
              <PanelSectionRow>
                <ToggleField
                  label="Also when Windows left it hibernated"
                  description={`For data drives Windows hibernated (Fast Startup) or didn't close: mounts them read-write anyway and at every start, readable in Linux and Windows afterwards. Only for drives without Windows on them: never a Windows system drive (HHC refuses one with a Windows/System32 folder). Risk: if a Windows that hibernated with this drive attached resumes later, what Linux changed can be lost or the drive damaged, so turn off Fast Startup in that Windows. It clears the hibernation (ntfs-3g remove_hiberfile, else ntfs3 force).${
                    v.forced ? " On." : ""
                  }${busy === v.path ? " Working…" : ""}`}
                  checked={v.forced}
                  disabled={off}
                  onChange={(on) => act(on ? "mount_force" : "mount", v.path)}
                />
              </PanelSectionRow>
            )}
            {v.ours && !v.mountpoint && (
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={off} onClick={() => act("mount", v.path)}>
                  {busy === v.path ? "Working…" : "Mount now (it didn't mount at start)"}
                </ButtonItem>
              </PanelSectionRow>
            )}
            {v.ours && v.mountpoint && !inSteam(v.mountpoint) && (
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  disabled={off}
                  onClick={async () => {
                    setMsg(await addToSteam(`${v.mountpoint}/SteamLibrary`));
                    refreshLibs();
                    getDrives().then(setD).catch(() => undefined);
                  }}
                >
                  Add to Steam as a library
                </ButtonItem>
              </PanelSectionRow>
            )}
            {!(v.permanent && !v.ours) && v.fstype !== "ext4" && erase !== v.path && (
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={off} onClick={() => setErase(v.path)}>
                  Format as ext4 for Steam (erases it)
                </ButtonItem>
              </PanelSectionRow>
            )}
            {erase === v.path && (
              <>
                <PanelSectionRow>
                  <Field label="Erase everything on it?" description={`${name} (${v.path}) is wiped and formatted as ext4, then mounted at every start for Steam. This can't be undone.`} />
                </PanelSectionRow>
                <PanelSectionRow>
                  <ButtonItem layout="below" disabled={off} onClick={() => act("format", v.path)}>
                    {busy === v.path ? "Formatting…" : "Yes, erase and format"}
                  </ButtonItem>
                </PanelSectionRow>
                <PanelSectionRow>
                  <ButtonItem layout="below" disabled={off} onClick={() => setErase(null)}>
                    Cancel
                  </ButtonItem>
                </PanelSectionRow>
              </>
            )}
          </Fragment>
        );
      })}
      {d.nag?.available && (
        <PanelSectionRow>
          <ToggleField
            label="Hide Bazzite's NTFS warning"
            description="Bazzite pops up a warning each time an NTFS or exFAT drive is mounted. On: it doesn't (the same as Bazzite's ujust _disable-ntfs-service); the drives work the same either way. Off: the warning is back."
            checked={d.nag.hidden}
            disabled={busy !== null}
            onChange={async (on) => {
              setBusy("nag");
              try {
                setD(await driveAction(on ? "nag_hide" : "nag_show", ""));
              } finally {
                setBusy(null);
              }
            }}
          />
        </PanelSectionRow>
      )}
      {msg && (
        <PanelSectionRow>
          <Field description={msg} />
        </PanelSectionRow>
      )}
      <ErrorRow msg={d.error ?? undefined} />
    </PanelSection>
  );
}

// --- OptiScaler per game, through the game's Steam launch options ---------------

function runningGame(): { appid: number; name: string } | null {
  const app = (window as any).SteamUIStore?.MainRunningApp;
  return app?.appid ? { appid: Number(app.appid), name: app.display_name ?? `App ${app.appid}` } : null;
}

// The game the OptiScaler rows act on: the running one, else the one whose
// library page is open, else the installed game played last.
type TargetGame = { appid: number; name: string; running: boolean; why?: string };
function targetGame(): TargetGame | null {
  const run = runningGame();
  if (run) return { ...run, running: true };
  const W = window as any;
  const overview = (id: number) => W.appStore?.GetAppOverviewByAppID?.(id);
  const path: string = W.SteamUIStore?.WindowStore?.GamepadUIMainWindowInstance?.m_locationPathname ?? "";
  const page = path.match(/\/library\/app\/(\d+)/);
  const o = page ? overview(Number(page[1])) : null;
  if (o?.appid) return { appid: Number(o.appid), name: o.display_name, running: false, why: "open in the library" };
  let last: any = null;
  try {
    for (const a of W.collectionStore?.localGamesCollection?.allApps ?? [])
      if ((a.rt_last_time_played ?? 0) > (last?.rt_last_time_played ?? 0)) last = a;
  } catch {
    last = null;
  }
  return last ? { appid: Number(last.appid), name: last.display_name, running: false, why: "played last" } : null;
}

// Launch options, compatibility tool and platforms of one game.
type AppInfo = { opts: string; tool: string; toolLabel: string; linux: boolean };
function appInfo(appid: number): Promise<AppInfo | null> {
  const apps = (window as any).SteamClient?.Apps;
  if (!apps?.RegisterForAppDetails) return Promise.resolve(null);
  return new Promise((resolve) => {
    let reg: { unregister: () => void } | null = null;
    const done = (v: AppInfo | null) => {
      clearTimeout(t);
      reg?.unregister();
      resolve(v);
    };
    const t = setTimeout(() => done(null), 3000);
    reg = apps.RegisterForAppDetails(appid, (d: any) =>
      done({
        opts: d?.strLaunchOptions ?? "",
        tool: d?.strCompatToolName ?? "",
        toolLabel: d?.strCompatToolDisplayName || d?.strCompatToolName || "",
        linux: (d?.vecPlatforms ?? []).includes("linux"),
      }),
    );
  });
}

// OptiScaler needs Proton-CachyOS: switched to it with OptiScaler on, and the
// Proton the game had put back when OptiScaler goes off. What it had is kept in
// the Steam UI's storage per game ("" = Steam's default).
const PREV_TOOL = (appid: number) => `hhctl-prev-compat-${appid}`;
const CACHYOS = /cachyos/i;
const storeGet = (k: string): string | null => {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
};
const storeSet = (k: string, v: string | null) => {
  try {
    if (v === null) localStorage.removeItem(k);
    else localStorage.setItem(k, v);
  } catch {
    /* not kept: turning OptiScaler off then leaves Proton-CachyOS */
  }
};

// Returns the tool the game is on now (name, label) and a note on what changed.
async function matchProton(appid: number, info: AppInfo, opti: boolean): Promise<{ tool: string; toolLabel: string; note: string }> {
  const apps = (window as any).SteamClient?.Apps;
  const keep = { tool: info.tool, toolLabel: info.toolLabel };
  if (!apps?.SpecifyCompatTool) return { ...keep, note: "" };
  if (opti) {
    if (CACHYOS.test(info.tool)) return { ...keep, note: "" };
    if (!info.tool && info.linux)
      return { ...keep, note: "Not switched to Proton-CachyOS: this is the native Linux version, which OptiScaler can't load into (switching would download the Windows version)." };
    const tools: { strToolName: string; strDisplayName: string }[] = (await apps.GetAvailableCompatTools?.(appid).catch(() => [])) ?? [];
    const pick = tools.filter((x) => CACHYOS.test(x.strToolName) || CACHYOS.test(x.strDisplayName)).sort((a, b) => b.strToolName.localeCompare(a.strToolName))[0];
    if (!pick) return { ...keep, note: "Proton-CachyOS isn't installed, so the game stays on its Proton and OptiScaler won't load: install Proton-CachyOS (e.g. with ProtonUp-Qt), then turn this off and on." };
    if (storeGet(PREV_TOOL(appid)) === null) storeSet(PREV_TOOL(appid), info.tool);
    apps.SpecifyCompatTool(appid, pick.strToolName);
    const was = info.toolLabel || "Steam's default Proton";
    return { tool: pick.strToolName, toolLabel: pick.strDisplayName, note: `Switched the game from ${was} to ${pick.strDisplayName}; turning OptiScaler off puts ${was} back.` };
  }
  const prev = storeGet(PREV_TOOL(appid));
  if (prev === null) return { ...keep, note: "" };
  storeSet(PREV_TOOL(appid), null);
  if (prev === info.tool) return { ...keep, note: "" };
  apps.SpecifyCompatTool(appid, prev);
  const tools: { strToolName: string; strDisplayName: string }[] = prev ? ((await apps.GetAvailableCompatTools?.(appid).catch(() => [])) ?? []) : [];
  const label = tools.find((x) => x.strToolName === prev)?.strDisplayName ?? prev;
  return { tool: prev, toolLabel: label, note: `Put the game back on ${label || "Steam's default Proton"}.` };
}

const OPTI_VAR = /(^|\s)PROTON_USE_OPTISCALER=\S+/;
const FG_ON = /FrameGen\.Enabled=true/;
const LSFG_MULT = /(^|\s)LSFGVK_MULTIPLIER=(\d+)/;

// The game's launch options with our parts replaced: `opti` for OptiScaler,
// `lsfg` for Lossless Scaling (null: taken out). Anything else stays.
function withOptions(opts: string, opti: string | null, lsfg: string | null): string {
  let o = opts.replace(/(^|\s)LSFGVK_[A-Z_]+=("[^"]*"|\S+)/g, " ");
  if (OPTI_VAR.test(o)) {
    o = o
      .replace(/(^|\s)PROTON_USE_OPTISCALER=\S+/g, " ")
      .replace(/(^|\s)PROTON_OPTISCALER_CONFIG=("[^"]*"|\S+)/g, " ")
      .replace(/(^|\s)PROTON_FFX4_UPGRADE=1(?=\s|$)/g, " ")
      .replace(/(^|\s)PROTON_FSR4_UPGRADE=1(?=\s|$)/g, " ");
  }
  o = o.replace(/\s+/g, " ").trim();
  if (o === "%command%") o = "";
  const vars = [opti, lsfg].filter(Boolean).join(" ");
  if (!vars) return o;
  if (o.includes("%command%")) return `${vars} ${o}`;
  return o ? `${vars} %command% ${o}` : `${vars} %command%`;
}

function LsfgRow({ state, setState }: { state: State; setState: (s: State) => void }) {
  const l = state.lsfg;
  if (!l) return null;
  const need = !l.installed
    ? "The frame generation layer (lsfg-vk) isn't installed yet: Install below (about 0.5 MB, into your home folder)."
    : !l.dll
      ? "Lossless Scaling isn't installed in Steam yet: install it from your library (Steam Play must be on for all titles), then switch it to the \"lsfg-vk\" branch under Properties › Betas."
      : !l.branch
        ? "Switch Lossless Scaling to the \"lsfg-vk\" branch in Steam (Properties › Betas)."
        : "Ready: pick Lossless Scaling under Frame generation for a running game.";
  return (
    <>
      <PanelSectionRow>
        <Field
          label="Lossless Scaling frame generation"
          description={`Uses your own Lossless Scaling (from Steam) through lsfg-vk; works in almost any game, with any Proton. ${need}`}
        />
      </PanelSectionRow>
      {(!l.installed || l.ours) && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={l.busy} onClick={async () => setState(await setLsfg(!l.installed))}>
            {l.busy ? "Working…" : l.installed ? "Remove lsfg-vk" : "Install lsfg-vk"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      <ErrorRow msg={state.errors.lsfg} />
    </>
  );
}

function OptiscalerRow({ u, l, now }: { u: State["upscale"]; l?: State["lsfg"]; now: ReturnType<typeof useGameUpscale> }) {
  const [game, setGame] = useState<TargetGame | null>(null);
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => {
    const g = targetGame();
    setGame(g);
    if (g) appInfo(g.appid).then(setInfo);
  }, []);
  const opts = info?.opts ?? null;
  if (!(window as any).SteamClient?.Apps?.SetAppLaunchOptions) {
    // HHC Desktop: no Steam to set them in, so the launch options to paste by hand.
    const rows: [string, string | undefined][] = [
      ["OptiScaler", u.optiscaler],
      ["FSR frame generation (OptiScaler, DirectX 12)", u.optiscaler_fg],
      ["Lossless Scaling 2×", l?.vars["2"]],
    ];
    return (
      <PanelSectionRow>
        <Field
          label="OptiScaler and frame generation"
          description={`In Game Mode the plugin sets these per game. Here, paste one into the game's Steam Properties › Launch options (OptiScaler needs Proton-CachyOS; Lossless Scaling needs lsfg-vk installed below): ${rows
            .filter(([, v]) => v)
            .map(([k, v]) => `${k}: ${v} %command%`)
            .join(" · ")}`}
        />
      </PanelSectionRow>
    );
  }
  const base = `Swaps the upscaler the game asks for (pick DLSS, FSR or XeSS in its settings) for ${u.opti_upscaler}. Needs Proton-CachyOS for the game: HHC switches the game to it and back. It loads into the game, so don't use it in online games with anti-cheat (ban risk).`;
  if (!game || opts === null || !info) {
    return (
      <PanelSectionRow>
        <Field
          label="OptiScaler and frame generation"
          description={`${base} ${game ? `Reading ${game.name}'s settings…` : "Start a game or open its page in the library, then open this panel to turn them on for that game."}`}
        />
      </PanelSectionRow>
    );
  }
  const on = OPTI_VAR.test(opts);
  const ls = opts.match(LSFG_MULT)?.[2];
  const mode = ls ? `ls${ls}` : on && FG_ON.test(opts) ? "fsr" : "off";
  const lsReady = !!l?.installed && !!l?.dll;
  const apply = async (next: string) => {
    (window as any).SteamClient.Apps.SetAppLaunchOptions(game.appid, next);
    setInfo({ ...info, opts: next });
    const want = OPTI_VAR.test(next);
    if (want === on && !(want && !CACHYOS.test(info.tool))) return;
    const r = await matchProton(game.appid, info, want);
    setInfo({ ...info, opts: next, tool: r.tool, toolLabel: r.toolLabel });
    setNote(r.note);
  };
  const setMode = (m: string) => {
    const opti = m === "fsr" ? u.optiscaler_fg : on ? u.optiscaler : null; // OptiScaler upscaling stays as it was
    const lsfg = m.startsWith("ls") ? l?.vars[m.slice(2)] ?? null : null;
    apply(withOptions(opts, opti, lsfg));
  };
  const start = game.running ? "restart the game" : `start ${game.name}`;
  const which = game.running ? "" : ` (not running: the game ${game.why})`;
  const proton = ` Proton for it: ${info.toolLabel || (info.linux ? "none (native Linux version)" : "Steam's default")}.${note ? ` ${note}` : ""}`;
  const nowFor = (kind: string, isOn: boolean) =>
    game.running ? upscaleNow(kind, isOn, now.game, now.st) : isOn ? ` Now: set; takes effect when you ${start}.` : "";
  const howto: Record<string, string> = {
    off: `How to: pick a mode, then ${start}.`,
    fsr: `How to: ${start}, then turn on an upscaler (DLSS, FSR or XeSS) in its settings; frame generation is fed from it. DirectX 12 games with Proton-CachyOS only; don't use it with anti-cheat. The HUD can smear a little.`,
    ls2: `How to: ${start}; nothing else to set. Works with any Proton. Steam's fps counter shows the rendered frames, not the generated ones.`,
    ls3: `How to: ${start}; nothing else to set. 3× needs a steady base fps (about 40+); otherwise use 2×.`,
  };
  return (
    <>
      <PanelSectionRow>
        <ToggleField
          label={`OptiScaler for ${game.name}`}
          description={`${base} Applies when you ${start}${which}.${proton}${nowFor("optiscaler", on)}`}
          checked={on}
          onChange={(v) => apply(withOptions(opts, v ? (mode === "fsr" ? u.optiscaler_fg : u.optiscaler) : null, ls ? l?.vars[ls] ?? null : null))}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <DropdownItem
          label={`Frame generation for ${game.name}`}
          description={`More frames shown than rendered; best from about 40 fps (below that it feels laggy). ${howto[mode] ?? howto.off}${
            mode === "fsr" ? nowFor("fsrfg", true) : mode.startsWith("ls") ? nowFor("lsfg", true) : ""
          }`}
          rgOptions={[
            { data: "off", label: "Off" },
            { data: "fsr", label: "FSR (OptiScaler, DirectX 12)" },
            ...(lsReady || ls
              ? [
                  { data: "ls2", label: "Lossless Scaling 2×" },
                  { data: "ls3", label: "Lossless Scaling 3×" },
                ]
              : []),
          ]}
          selectedOption={mode}
          onChange={(o) => setMode(o.data)}
        />
      </PanelSectionRow>
    </>
  );
}

// What the running game actually got, looked up every few seconds while the
// panel is open: ended on each upscaling note as "Now: ...".
function useGameUpscale(): { game: { appid: number; name: string } | null; st: GameUpscale | undefined } {
  const [game, setGame] = useState(runningGame());
  const [st, setSt] = useState<GameUpscale | undefined>(undefined);
  useEffect(() => {
    let live = true;
    const look = async () => {
      const g = runningGame();
      setGame(g);
      const r = g ? await upscaleStatus(g.appid).catch(() => undefined) : undefined;
      if (live) setSt(r);
    };
    look();
    const id = setInterval(look, 4000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);
  return { game, st };
}

// "Now: ..." for one feature. kind: fsr4 / xess (session-wide variables),
// optiscaler, fsrfg, lsfg (the game's launch options).
function upscaleNow(kind: string, on: boolean, game: { name: string } | null, st: GameUpscale | undefined): string {
  if (!game) return on ? " Now: on; no game running to check." : "";
  if (st === undefined) return on ? ` Now: checking ${game.name}…` : "";
  if (st === null) return on ? ` Now: ${game.name} isn't running any more.` : "";
  const env = st.env;
  const proton = st.proton ?? "an unknown Proton";
  const L = st.loaded;
  if (!on) {
    if (kind === "fsr4" && L.fsr4) return ` Now: ${game.name} has FSR 4 loaded anyway.`;
    return "";
  }
  if (kind === "lsfg") {
    if (!env.LSFGVK_MULTIPLIER) return ` Now: ${game.name} was started before this was set: restart the game.`;
    return L.lsfg
      ? ` Now: working: Lossless Scaling ${env.LSFGVK_MULTIPLIER}× is loaded in ${game.name}.`
      : ` Now: set, but lsfg-vk didn't load into ${game.name} (is it a Vulkan/DXVK game?).`;
  }
  const cachy = /cachyos/i.test(proton);
  if (kind === "optiscaler" || kind === "fsrfg") {
    if (!env.PROTON_USE_OPTISCALER) return ` Now: ${game.name} was started before this was set: restart the game.`;
    if (!cachy) return ` Now: not working: ${game.name} runs with ${proton}; OptiScaler needs Proton-CachyOS (Properties › Compatibility).`;
    if (!L.optiscaler) return ` Now: set, but OptiScaler didn't load into ${game.name} (update Proton-CachyOS).`;
    if (kind === "optiscaler") return ` Now: working: OptiScaler is loaded in ${game.name}; pick an upscaler in the game's settings.`;
    return L.fidelityfx
      ? ` Now: working: OptiScaler and the FSR frame generation DLL are loaded in ${game.name}.`
      : ` Now: OptiScaler is loaded, but not the FSR frame generation DLL: turn on an upscaler in the game (DirectX 12 games only).`;
  }
  // fsr4 / xess: from ~/.config/environment.d, read when the session starts
  const has = kind === "fsr4" ? !!(env.PROTON_FSR4_UPGRADE || env.FSR4_UPGRADE) : !!env.PROTON_XESS_UPGRADE;
  if (!st.knows_vars) return ` Now: not working: ${game.name} runs with ${proton}, which ignores this; pick Proton-CachyOS, GE-Proton or Proton-EM for it (Properties › Compatibility).`;
  if (!has) return ` Now: not yet: ${game.name} didn't get the setting. It's read when the session starts: switch to Desktop and back to Game Mode, then start the game again.`;
  if (kind === "fsr4")
    return L.fsr4
      ? ` Now: working: FSR 4 is loaded in ${game.name}.`
      : ` Now: set, but FSR 4 isn't loaded: ${game.name} isn't using FSR 3.1 right now (pick FSR in its settings), or it has no FSR 3.1.`;
  return L.xess
    ? ` Now: XeSS is loaded in ${game.name} (the newest one when Proton could download it).`
    : ` Now: set, but ${game.name} isn't using XeSS right now (pick XeSS in its settings).`;
}

function UpscaleRows({ state, setState }: { state: State; setState: (s: State) => void }) {
  const u = state.upscale;
  const now = useGameUpscale();
  if (!u) return null;
  const tools = u.tools.length
    ? `Installed: ${u.tools.slice(0, 3).join(", ")}${u.tools.length > 3 ? "…" : ""}; pick it per game (Properties › Compatibility).`
    : "Needs Proton-CachyOS, GE-Proton or Proton-EM picked per game (Properties › Compatibility); install one with ProtonPlus or ProtonUp-Qt. Valve's Proton ignores it.";
  const text: Record<string, [string, string]> = {
    fsr4: [
      "FSR 4 in FSR 3.1 games",
      `Swaps FSR 3.1 for FSR 4: a sharper, steadier picture.${
        u.rdna3 ? " On this GPU (RDNA 3 / 3.5) it runs the INT8 model and costs some fps compared with FSR 3.1, so it's worth it from about 60 fps." : ""
      }`,
    ],
    xess: ["Newest XeSS in XeSS games", "Uses the newest XeSS version in games that have XeSS. Best on Intel Arc; also runs on AMD."],
  };
  return (
    <>
      {u.fsr4_why && (
        <PanelSectionRow>
          <Field
            label="FSR"
            description={`FSR 4 ${u.fsr4_why}. FSR 1, 2 and 3 work here: pick them in each game's own settings (FSR 3 frame generation too, where the game has it).`}
          />
        </PanelSectionRow>
      )}
      {u.options.map((o) => (
        <PanelSectionRow key={o}>
          <ToggleField
            label={text[o]?.[0] ?? o}
            description={`${text[o]?.[1] ?? ""} ${tools} Games started after the next login.${upscaleNow(o, !!u.on[o], now.game, now.st)}`}
            checked={!!u.on[o]}
            onChange={async (v) => setState(await setUpscale(o, v))}
          />
        </PanelSectionRow>
      ))}
      <OptiscalerRow u={u} l={state.lsfg} now={now} />
      <LsfgRow state={state} setState={setState} />
      <ErrorRow msg={state.errors.upscale} />
    </>
  );
}

function VramRows({ state, setState }: { state: State; setState: (s: State) => void }) {
  const v = state.vram;
  const m = state.gpu_mem;
  const intel = v.unavailable === "AMD graphics only";
  const suggest = `Suggested for ${v.ram_gb} GB RAM: ${v.suggested_gb} GB`;
  return (
    <>
      {v.available ? (
        <PanelSectionRow>
          <DropdownItem
            label="Dedicated VRAM"
            description={
              v.current !== v.boot
                ? `Changes after a restart (now ${vramSize(v.now_mb)}) · ${suggest}`
                : `Now ${vramSize(v.now_mb)} · ${suggest} · changes after a restart`
            }
            rgOptions={v.options.map((o) => ({ data: o.index, label: o.label }))}
            selectedOption={v.current}
            onChange={async (o) => setState(await setVram(o.data))}
          />
        </PanelSectionRow>
      ) : intel ? (
        <PanelSectionRow>
          <Field
            label="Dedicated VRAM"
            description="Intel graphics have no dedicated VRAM: the GPU takes system RAM as it needs it, up to the GPU memory limit below. That limit is what Windows' Shared GPU Memory Override sets."
          />
        </PanelSectionRow>
      ) : (
        <PanelSectionRow>
          {/* Set in the BIOS: shown like the others, just locked. */}
          <DropdownItem
            label="Dedicated VRAM"
            disabled
            description={`Set in the BIOS, Linux can't change it here: ${v.bios_path}. ${suggest}.`}
            rgOptions={[{ data: 0, label: vramSize(v.now_mb) }]}
            selectedOption={0}
            onChange={() => {}}
          />
        </PanelSectionRow>
      )}
      <ErrorRow msg={state.errors.vram} />
      {m.available && (
        <PanelSectionRow>
          <DropdownItem
            label="GPU memory limit"
            description={
              m.busy
                ? `Updating ${m.bootloader} boot entries…`
                : `System RAM the GPU may use${intel ? " (Windows: Shared GPU Memory Override)" : " on top of its VRAM"} · now ${m.current ?? "?"} GB · changes after a restart`
            }
            disabled={m.busy}
            rgOptions={[
              { data: 0, label: `Default (half the RAM, ${m.default} GB)` },
              ...(m.choices ?? []).map((c) => ({ data: c, label: `${c} GB` })),
            ]}
            selectedOption={m.configured ?? 0}
            onChange={async (o) => setState(await setGpuMem(o.data))}
          />
        </PanelSectionRow>
      )}
      <ErrorRow msg={state.errors.gpumem} />
      {!intel && (
        <PanelSectionRow>
          <Field
            label="VRAM or GPU memory limit?"
            description="Both are the same RAM at the same speed. Dedicated VRAM is set aside at boot: the system can't use it even when no game runs, and games see it as the graphics card's own memory (some pick texture quality from it, or refuse to start below a minimum). The GPU memory limit is how much more RAM the GPU may borrow, only while it needs it. Speed is about the same; dedicated VRAM can be a few percent faster in some games. Usually: the suggested VRAM size, limit on Default."
          />
        </PanelSectionRow>
      )}
    </>
  );
}
const setDisplay = callable<[string, boolean], State>("set_display");
const setGamescopeHdr = callable<[string], State>("set_gamescope_hdr");
const confirmSdrBrightness = callable<[boolean], State>("confirm_sdr_brightness");
const setBackground = callable<[string], State>("set_background");
const clearBackground = callable<[], State>("clear_background");
const getBackground = callable<[], string | null>("get_background");
const listPictures = callable<[string | null], Listing>("list_pictures");
const pictureThumb = callable<[string], string | null>("picture_thumb");
const pictureData = callable<[string], string | null>("picture_data");
const setBackgroundData = callable<[string], State>("set_background_data");
const getFont = callable<[string], string | null>("get_font");
const confirmGamescopeHdr = callable<[boolean], State>("confirm_gamescope_hdr");

const HDR_MODES: Record<string, string> = {
  steam: "Steam default",
  content: "Only for HDR games",
  off: "Off",
};
const brightnessTest = callable<[], State>("brightness_test");
const inputCapture = callable<[], State>("input_capture");
const checkUpdates = callable<[], State>("check_updates");
const rgbZoneTest = callable<[], State>("rgb_zone_test");
const installUpdate = callable<[string], State>("install_update");

const versionKey = (v: string) => v.replace(/^v/, "").split(".").map((p) => parseInt(p, 10) || 0);
const versionCmp = (a: string, b: string) => {
  const x = versionKey(a);
  const y = versionKey(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) - (y[i] ?? 0);
  return 0;
};
const FIRST_WITH_UPDATER = "0.10.7";
// Release notes are Markdown; the first line of text is enough here.
const firstLine = (md: string) =>
  md
    .split("\n")
    .map((l) => l.replace(/[*_`#>]/g, "").replace(/^\s*-\s*/, "").trim())
    .find(Boolean) ?? "";
const brightnessTestAnswer = callable<[string], State>("brightness_test_answer");
const setters: Record<Section, (v: object) => Promise<State>> = {
  tdp: callable<[object], State>("set_tdp"),
  epp: callable<[object], State>("set_epp"),
  cpu: callable<[object], State>("set_cpu"),
  gpu: callable<[object], State>("set_gpu"),
  ui: callable<[object], State>("set_ui"),
  sdr_brightness: callable<[object], State>("set_sdr_brightness"),
  fan: callable<[object], State>("set_fan"),
  rgb: callable<[object], State>("set_rgb"),
  battery: callable<[object], State>("set_battery"),
  ec: callable<[object], State>("set_ec"),
  controller: callable<[object], State>("set_controller"),
  sleep: callable<[object], State>("set_sleep"),
  games: callable<[object], State>("set_games"),
  system: callable<[object], State>("set_system"),
  overlay: callable<[object], State>("set_overlay"),
};

const BYPASS_LABELS: Record<string, string> = {
  off: "Off",
  awake: "While awake",
  always: "Always",
};

const EPP_LABELS: Record<string, string> = {
  performance: "Performance",
  balance_performance: "Balance performance",
  balance_power: "Balance power",
  power: "Power saver",
};

const IP_TARGETS: Record<string, string> = {
  "xbox-elite": "Xbox Elite",
  "xbox-series": "Xbox Series",
  xb360: "Xbox 360",
  "deck-uhid": "Steam Deck",
};

const GPU_PROFILES: Record<string, string> = {
  "3D_FULL_SCREEN": "3D fullscreen (games)",
  POWER_SAVING: "Power saving",
  VIDEO: "Video",
  VR: "VR",
  COMPUTE: "Compute",
};

// Short notes per scheduler, keyed without the scx_ prefix (from the scx
// project's descriptions). Unknown ones just show no note.
const SCX_NOTES: Record<string, string> = {
  off: "EEVDF, the kernel's own fair scheduler. Solid all-rounder.",
  lavd: "Made for handhelds (Steam Deck): favors latency-critical game threads for steady frame times, packs work onto fewer cores to save power.",
  bpfland: "Favors interactive tasks (ones that sleep often): keeps the desktop and games responsive while something heavy runs in the background.",
  flash: "Fair and latency-aware: aims for even, predictable latency. Good for audio, video and consistent frame pacing.",
  cosmos: "Lightweight: keeps tasks on the same CPU for cache locality. Low overhead, good general use.",
  p2dq: "Picks the less busy of two CPUs to balance load. General purpose, scales to many cores.",
  rusty: "Balances load between cache domains (CCXs). Flexible general-purpose scheduler, more desktop/server than handheld.",
  rustland: "Decides scheduling in a userspace Rust program. Mostly a showcase; favors interactive tasks.",
  tickless: "Cuts timer interrupts on busy CPUs. For servers and isolated workloads, not for gaming.",
};

// "4 cores / 8 threads", or just "8 cores" without SMT.
const coreCount = (cores: number, threads: number) =>
  cores && cores !== threads ? `${cores} cores / ${threads} threads` : `${threads} cores`;

const SCX_TAGS: Record<string, string> = {
  lavd: "handhelds",
  bpfland: "responsive",
  flash: "even latency",
  cosmos: "lightweight",
  p2dq: "many cores",
  rusty: "general",
  rustland: "showcase",
  tickless: "servers",
};

const SCX_MODES: Record<string, string> = {
  auto: "Auto",
  gaming: "Gaming",
  powersave: "Power save",
  lowlatency: "Low latency",
};

const GPU_MODES: Record<GpuMode, { label: string; description: string }> = {
  auto: { label: "Auto", description: "The driver picks the clock" },
  min: { label: "Min", description: "Locked at the lowest clock" },
  max: { label: "Max", description: "Locked at the highest clock" },
  manual: { label: "Manual", description: "Locked at the clock you set" },
};

// Reasons from Intel's limit flags, xe throttle files, or inferred (tdp).
const THROTTLE_LABELS: Record<string, string> = {
  tdp: "TDP",
  pl1: "TDP (PL1)",
  pl2: "boost power (PL2)",
  pl4: "peak current",
  thermal: "temperature",
  prochot: "PROCHOT",
  ratl: "average temperature",
  vr_thermalert: "VR temperature",
  vr_tdc: "VR current",
  other: "other",
  clock_limit: "your clock limit",
  boost_off: "boost off",
  mode: "clock mode",
  profile: "low-power profile",
};

const reasons = (xs: string[]) => xs.map((r) => THROTTLE_LABELS[r] ?? r).join(", ");

// Header (device name, TDP, CPU/GPU table) stays at the top of the panel and
// the settings scroll in their own area below it, so nothing slides under it.
// If the panel's scroll container can't be found, the header is sticky instead.
// Table header and cap rows are dimmer.
const READOUT_CSS = `
  .hhctl-root { box-sizing: border-box; }
  /* D-pad focus: a frame of our own over the setting (Steam's styles on the
     setting itself win over anything put there), a flash when it lands, then
     a slow pulse. */
  .hhctl-root { position: relative; }
  .hhctl-ring {
    position: absolute; pointer-events: none; z-index: 20; display: none;
    border: 2px solid rgba(255, 255, 255, 0.9); border-radius: 6px; box-sizing: border-box;
    box-shadow: 0 0 10px 2px rgba(255, 255, 255, 0.3), inset 0 0 8px 1px rgba(255, 255, 255, 0.15);
    animation: hhctl-pulse 1.6s ease-in-out infinite;
  }
  .hhctl-ring.hhctl-flash { animation: hhctl-flash 0.35s ease-out, hhctl-pulse 1.6s ease-in-out 0.35s infinite; }
  @keyframes hhctl-flash {
    0% { border-color: #fff; box-shadow: 0 0 18px 5px rgba(255, 255, 255, 0.7), inset 0 0 16px 4px rgba(255, 255, 255, 0.4); }
    100% { border-color: rgba(255, 255, 255, 0.9); box-shadow: 0 0 10px 2px rgba(255, 255, 255, 0.3), inset 0 0 8px 1px rgba(255, 255, 255, 0.15); }
  }
  @keyframes hhctl-pulse {
    0%, 100% { border-color: rgba(255, 255, 255, 0.9); }
    50% { border-color: rgba(255, 255, 255, 0.45); }
  }
  .hhctl-fit { display: flex; flex-direction: column; }
  .hhctl-header {
    flex: none; padding: 2px 16px 4px; border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    position: sticky; top: 0; z-index: 10;
  }
  .hhctl-sticky .hhctl-header { background: rgba(14, 20, 27, 0.94); backdrop-filter: blur(6px); }
  .hhctl-body { flex: 1; min-height: 0; overflow-y: auto; }
  .hhctl-body::-webkit-scrollbar { display: none; }
  .hhctl-top { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; white-space: nowrap; }
  .hhctl-device { font-size: 14px; font-weight: bold; overflow: hidden; text-overflow: ellipsis; }
  .hhctl-tdp { font-size: 12px; color: #dcdedf; }
  .hhctl-cpu { font-size: 12px; color: #b8bcbf; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .hhctl-name { white-space: normal; overflow-wrap: anywhere; }
  .hhctl-readout {
    display: grid; grid-template-columns: auto auto auto auto auto minmax(0, 1fr); gap: 0 7px;
    font-size: 12px; line-height: 15px; color: #dcdedf; padding-top: 2px; white-space: nowrap;
  }
  .hhctl-readout > span { overflow: hidden; text-overflow: ellipsis; }
  .hhctl-readout > span:nth-child(-n + 6) { color: #8b929a; font-size: 11px; }
  .hhctl-readout .hhctl-capped {
    grid-column: 2 / -1; color: #ffb35c; font-style: normal; font-size: 11px;
    overflow: hidden; text-overflow: ellipsis;
  }
  .hhctl-readout .hhctl-capped.hhctl-free { color: #8b929a; }
  .hhctl-headlabel { font-weight: bold; }
  .hhctl-inline { white-space: nowrap; }
  .hhctl-statusrow {
    display: flex; justify-content: space-between; align-items: baseline; gap: 8px; white-space: nowrap;
    padding: 10px 3ch 10px 0; font-size: 14px; border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  }
`;

const gb = (mb: number) => `${(mb / 1024).toFixed(1)} GB`;

const ghz = (mhz: number) => `${(mhz / 1000).toFixed(1)} GHz`;

const DISPLAY_TOGGLES: Record<string, { label: string; description: string }> = {
  psr: {
    label: "Fix screen blanking",
    description: "Turns off Panel Self Refresh and Panel Replay. Small battery cost.",
  },
  dc: {
    label: "Disable display C-states",
    description: "Stronger fix if the screen still fails to wake. Uses more battery.",
  },
};

// Purely cosmetic: tints the plugin's own panel, its titles and labels.
// filter turns Steam's blue (#1a9fff) on toggles and sliders into the theme
// color; fitted per theme (hue-rotate alone gives orange for red). A filter
// works whatever the controls look like inside, unlike recoloring their parts.
// title: headlines (section titles, device name, status rows, panel title);
// label: setting names; text: values and buttons; desc: descriptions and dim rows.
// font: the font a theme brings along (Font can still be changed afterwards).
type Theme = {
  label: string;
  bg?: string;
  title?: string;
  label2?: string;
  text?: string;
  desc?: string;
  filter?: string;
  font?: string;
  button?: [number, number, number]; // buttons and dropdowns, instead of Steam's gray
  buttonText?: string; // their text, for light button colors
};
const THEMES: Record<string, Theme> = {
  default: { label: "Default (black)" },
  red: {
    label: "Red",
    bg: "linear-gradient(180deg, rgba(170, 25, 35, 0.40) 0%, rgba(70, 10, 15, 0.25) 100%)",
    title: "#ff6b6b",
    label2: "#ff9e9e",
    text: "#ffd6d6",
    desc: "#d49a9a",
    filter: "hue-rotate(150deg) saturate(1.6) brightness(0.94)",
    font: "diablo",
    button: [150, 30, 40],
  },
  // Bright red panel; gold headlines, toggles and sliders.
  redgold: {
    label: "Red + Gold",
    bg: "linear-gradient(180deg, rgba(225, 25, 35, 0.55) 0%, rgba(130, 10, 18, 0.38) 100%)",
    title: "#ffc940",
    label2: "#ffe2a0",
    text: "#fff0e6",
    desc: "#f0b0a8",
    filter: "hue-rotate(-156deg) saturate(0.8) brightness(1.24)",
    font: "diablo",
    button: [175, 25, 35],
  },
  // Bright red panel; bright yellow headlines, toggles and sliders; Steam's font.
  redyellow: {
    label: "Red + Yellow",
    bg: "linear-gradient(180deg, rgba(235, 20, 25, 0.60) 0%, rgba(150, 8, 12, 0.42) 100%)",
    title: "#ffe600",
    label2: "#fff27a",
    text: "#fff8e0",
    desc: "#f5b8a8",
    filter: "hue-rotate(-148deg) saturate(1.3) brightness(1.4)",
    font: "default",
    button: [255, 215, 0],
    buttonText: "#2a0505",
  },
  blue: {
    label: "Blue",
    bg: "linear-gradient(180deg, rgba(25, 80, 190, 0.40) 0%, rgba(10, 30, 80, 0.25) 100%)",
    title: "#6aa8ff",
    label2: "#9cc4ff",
    text: "#d6e6ff",
    desc: "#93a9c9",
    font: "doom",
    button: [35, 85, 170],
  },
  green: {
    label: "Green",
    bg: "linear-gradient(180deg, rgba(25, 140, 60, 0.40) 0%, rgba(10, 60, 25, 0.25) 100%)",
    title: "#5fd877",
    label2: "#92e6a0",
    text: "#d4f5da",
    desc: "#8fb898",
    filter: "hue-rotate(-84deg) saturate(0.7) brightness(1.06)",
    font: "matrix",
    button: [30, 120, 55],
  },
  gold: {
    label: "Gold",
    bg: "linear-gradient(180deg, rgba(185, 140, 20, 0.40) 0%, rgba(80, 60, 10, 0.25) 100%)",
    title: "#ffc940",
    label2: "#ffd97a",
    text: "#fff0cc",
    desc: "#c9b07a",
    filter: "hue-rotate(-156deg) saturate(0.8) brightness(1.24)",
    font: "quake",
    button: [150, 110, 20],
  },
};

// Light color for RGB "Match theme color"; mirrors THEME_RGB in controller.py.
const THEME_RGB: Record<string, [number, number]> = {
  red: [0, 100],
  redgold: [0, 100],
  redyellow: [0, 100],
  blue: [215, 100],
  green: [130, 100],
  gold: [42, 100],
};

// Free (OFL) fonts in the style of each game; the originals can't be bundled.
const FONTS: Record<string, { label: string; family?: string; fallback?: string; about?: string }> = {
  default: { label: "Default" },
  fraktur: {
    label: "Fraktur",
    family: "HHUnifraktur",
    fallback: "serif",
    about: "UnifrakturMaguntia, German blackletter (Fraktur)",
  },
  diablo: {
    label: "Diablo",
    family: "HHCinzel",
    fallback: "serif",
    about: "Cinzel, in the style of Diablo's lettering",
  },
  matrix: {
    label: "Matrix",
    family: "HHShareTechMono",
    fallback: "monospace",
    about: "Share Tech Mono, a terminal-style font",
  },
  doom: {
    label: "Doom",
    family: "HHMetalMania",
    fallback: "serif",
    about: "Metal Mania, a heavy-metal logo style",
  },
  quake: {
    label: "Quake",
    family: "HHGrenzeGotisch",
    fallback: "serif",
    about: "Grenze Gotisch, a gothic style",
  },
};

const fontCss = (name: string, faces: string | null) => {
  const font = FONTS[name];
  if (!font?.family || !faces) return "";
  return `${faces}
    .hhctl-root, .hhctl-root * { font-family: '${font.family}', ${font.fallback ?? "serif"} !important; }`;
};

// Buttons (Check for updates, ...) and dropdowns in a shade of the theme.
// The focused one keeps Steam's own highlight, so gamepad focus stays obvious.
const buttonCss = ([r, g, b]: [number, number, number], text?: string) => {
  const btn = ["DialogButton", "DropDownControlButton"]
    .map((c) => `.hhctl-root [class*="${c}"]:not(.gpfocus):not(:focus-visible)`)
    .join(", ");
  const hover = ["DialogButton", "DropDownControlButton"]
    .map((c) => `.hhctl-root [class*="${c}"]:not(.gpfocus):not(:focus-visible):hover:not(:disabled)`)
    .join(", ");
  // HHC Desktop's dropdowns are <select>s: their open list needs solid colors,
  // so mix the shade over a dark gray like the panel's. They keep the color
  // while focused (the outline shows focus there), and where Chromium can
  // style the list (base-select) the highlighted option is the full shade
  // instead of the system blue.
  const a = text ? 0.9 : 0.55;
  const solid = [r, g, b].map((c) => Math.round(c * a + 24 * (1 - a))).join(", ");
  const fg = text ?? "#fff";
  return `
    ${btn}, .hhctl-root select { background: rgba(${r}, ${g}, ${b}, ${a}) !important;${text ? ` color: ${text} !important;` : ""} }
    ${hover}, .hhctl-root select:hover { background: rgba(${r}, ${g}, ${b}, ${text ? 1 : 0.8}) !important; }
    .hhctl-root [class*="DropDownControlButton"]:disabled, .hhctl-root select:disabled { opacity: 0.5; cursor: default; }
    .hhctl-root .hhctl-off { opacity: 0.5; pointer-events: none; }
    .hhctl-root select option { background: rgb(${solid}); color: ${fg}; }
    @supports (appearance: base-select) {
      .hhctl-root select, .hhctl-root select::picker(select) { appearance: base-select; }
      .hhctl-root select::picker(select) { background: rgb(${solid}); color: ${fg}; border: 0; border-radius: 3px; }
      .hhctl-root select option:is(:hover, :focus, :checked) { background: rgb(${r}, ${g}, ${b}); color: ${fg}; outline: none; }
    }
  `;
};

const themeCss = (name: string, hasImage: boolean) => {
  const t = THEMES[name];
  if (!t?.bg) return "";
  // With a background picture, keep the picture and only color the titles.
  const dlg = gamepadDialogClasses;
  const sl = gamepadSliderClasses;
  // Steam's module class names look like gamepaddialog_Toggle_9Ql-o; the
  // [class*=] forms still match if a class map lookup comes back empty.
  const labels = `.hhctl-root .${dlg.FieldLabel}, .hhctl-root [class*="gamepaddialog_FieldLabel_"]`;
  const descs = `.hhctl-root .${dlg.FieldDescription}, .hhctl-root [class*="gamepaddialog_FieldDescription_"]`;
  const texts = [
    ".hhctl-root",
    `.hhctl-root [class*="gamepaddialog_FieldChildren_"]`,
    `.hhctl-root [class*="gamepadslider_"]`,
    `.hhctl-root [class*="DialogButton"]`,
    `.hhctl-root [class*="DropDownControlButton"]`,
    ".hhctl-root .hhctl-readout",
    ".hhctl-root .hhctl-tdp",
    ".hhctl-root .hhctl-cpu",
  ].join(", ");
  const controls = [
    `.hhctl-root .${dlg.Toggle}`,
    `.hhctl-root [class*="gamepaddialog_Toggle_"]`,
    `.hhctl-root .${sl.SliderControl}`,
    `.hhctl-root [class*="gamepadslider_SliderControl_"]`,
  ].join(", ");
  return `
    ${hasImage ? "" : `.hhctl-root { background: ${t.bg}; border-radius: 8px; padding-bottom: 4px; }`}
    ${texts} { color: ${t.text}; }
    ${descs}, .hhctl-root .hhctl-readout > span:nth-child(-n + 6), .hhctl-root .hhctl-readout .hhctl-free { color: ${t.desc}; }
    ${labels} { color: ${t.label2}; }
    .hhctl-root .${staticClasses.PanelSectionTitle}, .hhctl-root .hhctl-headlabel,
    .hhctl-root .hhctl-readout b, .hhctl-root .hhctl-readout .hhctl-capped:not(.hhctl-free) { color: ${t.title}; }
    ${t.filter ? `${controls} { filter: ${t.filter}; }` : ""}
    ${t.button ? buttonCss(t.button, t.buttonText) : ""}
  `;
};

// Glass buttons: what's behind a button (the background picture) shows through it like through a magnifying
// glass, sharp, not blurred: the middle a little magnified, the rim bent hard the other way (it shows what lies
// beyond the edge, squeezed in), a cubic curve between, so it reads as a thick drop of glass. Chromium does it
// with an SVG displacement map as the backdrop-filter; the map depends on the button's size, so each size gets
// its own filter, in the document the panel is in (Steam's Quick Access window, or HHC Desktop's page).
const GLASS_SEL = '[class*="DialogButton"], [class*="DropDownControlButton"], select';
const GLASS_MAG = 1.07; // the middle's magnification
const GLASS_BEND = 1.8; // how far the rim reaches out, in rim widths
const glassFilters = new WeakMap<Document, Map<string, string>>();

const glassMap = (doc: Document, w: number, h: number, r: number) => {
  const c = doc.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d")!;
  const img = ctx.createImageData(w, h);
  const rim = Math.max(2, Math.min(h * 0.35, w * 0.35, 14));
  const most = rim * GLASS_BEND;
  const zx = 1 / (1 + (2 * most) / w), zy = 1 / (1 + (2 * most) / h); // the rim's zoom
  const R = Math.min(r, w / 2, h / 2);
  const dx = new Float32Array(w * h), dy = new Float32Array(w * h);
  let top = 1;
  for (let j = 0; j < h; j++)
    for (let i = 0; i < w; i++) {
      const px = i + 0.5 - w / 2, py = j + 0.5 - h / 2;
      // distance from the rounded edge, inwards
      const qx = Math.abs(px) - (w / 2 - R), qy = Math.abs(py) - (h / 2 - R);
      const edge = R - (Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0));
      const bend = Math.max(0, 1 - edge / rim) ** 3; // 1 at the rim, 0 inside
      const sx = GLASS_MAG + (zx - GLASS_MAG) * bend, sy = GLASS_MAG + (zy - GLASS_MAG) * bend;
      const k = j * w + i;
      dx[k] = px * (1 / sx - 1);
      dy[k] = py * (1 / sy - 1);
      top = Math.max(top, Math.abs(dx[k]), Math.abs(dy[k]));
    }
  for (let k = 0; k < w * h; k++) {
    img.data[k * 4] = 128 + (127 * dx[k]) / top;
    img.data[k * 4 + 1] = 128 + (127 * dy[k]) / top;
    img.data[k * 4 + 2] = 128;
    img.data[k * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return { url: c.toDataURL(), scale: 2 * top };
};

const glassFilter = (doc: Document, w: number, h: number, r: number) => {
  const key = `${w}x${h}r${r}`;
  let have = glassFilters.get(doc);
  let svg = doc.getElementById("hhctl-glass") as Element | null;
  if (!have || !svg || have.size > 64) {
    svg?.remove();
    const NS = "http://www.w3.org/2000/svg";
    svg = doc.createElementNS(NS, "svg");
    svg.id = "hhctl-glass";
    svg.setAttribute("width", "0");
    svg.setAttribute("height", "0");
    svg.setAttribute("style", "position:absolute;width:0;height:0;pointer-events:none");
    doc.body.appendChild(svg);
    have = new Map<string, string>();
    glassFilters.set(doc, have);
  }
  const id = have.get(key);
  if (id) return id;
  const NS = "http://www.w3.org/2000/svg";
  const next = `hhctl-glass-${have.size}`;
  const { url, scale } = glassMap(doc, w, h, r);
  const set = (el: Element, a: Record<string, string | number>) =>
    Object.entries(a).forEach(([k, v]) => el.setAttribute(k, String(v)));
  const f = doc.createElementNS(NS, "filter");
  set(f, { id: next, x: 0, y: 0, width: w, height: h, filterUnits: "userSpaceOnUse",
           primitiveUnits: "userSpaceOnUse", "color-interpolation-filters": "sRGB" });
  const map = doc.createElementNS(NS, "feImage");
  set(map, { x: 0, y: 0, width: w, height: h, preserveAspectRatio: "none", result: "map", href: url });
  const move = doc.createElementNS(NS, "feDisplacementMap");
  set(move, { in: "SourceGraphic", in2: "map", scale, xChannelSelector: "R", yChannelSelector: "G" });
  f.append(map, move);
  svg!.appendChild(f);
  have.set(key, next);
  return next;
};

// Gives each button its size's filter (or takes them off).
const glassButtons = (root: HTMLElement, on: boolean) => {
  root.querySelectorAll<HTMLElement>(GLASS_SEL).forEach((el) => {
    const w = el.offsetWidth, h = el.offsetHeight;
    const want = on && w > 0 && h > 0
      ? `url(#${glassFilter(root.ownerDocument, w, h, Math.round(parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0))}) saturate(1.25) brightness(1.06)`
      : "";
    if (el.style.backdropFilter !== want) el.style.backdropFilter = want;
  });
};

// The glass look itself: a faint tint of the theme's button color (or white), a bright top edge, a darker
// bottom one and a soft drop shadow. The focused button keeps Steam's own highlight.
const glassCss = (rgb?: [number, number, number]) => {
  const [r, g, b] = rgb ?? [255, 255, 255];
  const tint = rgb ? 0.22 : 0.08;
  const btn = ["DialogButton", "DropDownControlButton"]
    .map((c) => `.hhctl-root [class*="${c}"]:not(.gpfocus):not(:focus-visible)`)
    .concat([".hhctl-root select"])
    .join(", ");
  const hover = ["DialogButton", "DropDownControlButton"]
    .map((c) => `.hhctl-root [class*="${c}"]:not(.gpfocus):not(:focus-visible):hover:not(:disabled)`)
    .concat([".hhctl-root select:hover"])
    .join(", ");
  return `
    ${btn} {
      background: rgba(${r}, ${g}, ${b}, ${tint}) !important;
      border-radius: 12px !important;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5), inset 0 -1px 1px rgba(0, 0, 0, 0.35),
        inset 0 0 0 1px rgba(255, 255, 255, 0.16), 0 3px 8px rgba(0, 0, 0, 0.35) !important;
      color: #fff !important;
      text-shadow: -1px 1px 1px rgba(0, 0, 0, 0.9), -1px 2px 4px rgba(0, 0, 0, 0.6); /* left and bottom */
    }
    ${hover} { background: rgba(${r}, ${g}, ${b}, ${tint + 0.1}) !important; }
  `;
};

// zoom 100 is "cover"; above that the picture's shorter side (relative to the
// panel) grows, so it needs the picture's size and the panel's.
const imageStyle = (
  url: string,
  dimPct: number,
  pos: BgPos,
  img: { w: number; h: number } | null,
  box: { w: number; h: number } | null,
) => {
  const a = Math.max(0, Math.min(90, dimPct)) / 100;
  const zoom = Math.max(100, pos.zoom);
  const size =
    zoom === 100 || !img || !box ? "cover" : img.w / img.h > box.w / box.h ? `auto ${zoom}%` : `${zoom}% auto`;
  return {
    backgroundImage: `linear-gradient(rgba(0, 0, 0, ${a}), rgba(0, 0, 0, ${a})), url("${url}")`,
    backgroundSize: `auto, ${size}`,
    backgroundPosition: `center, ${pos.x}% ${pos.y}%`,
    backgroundRepeat: "no-repeat",
    borderRadius: "8px",
    paddingBottom: "4px",
  };
};

const titleCase = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

function Unavailable({ why }: { why: string }) {
  return (
    <PanelSectionRow>
      <Field label="Unavailable" description={why} />
    </PanelSectionRow>
  );
}

function ErrorRow({ msg }: { msg?: string }) {
  if (!msg) return null;
  return (
    <PanelSectionRow>
      <Field label="Error" description={msg} />
    </PanelSectionRow>
  );
}

const DEFAULT_POS: BgPos = { x: 50, y: 50, zoom: 100 };

// The title bar lives outside the panel; it follows the theme through this.
let themeNow = "gold";
const themeListeners = new Set<() => void>();

// Where the settings were scrolled to: kept while picking a picture and
// while the Quick Access Menu is closed.
let savedScroll = 0;

// Thumbnails survive closing the picker; each is a small JPEG.
const thumbCache = new Map<string, string | null>();

// Downscale to `max` px on the long side as JPEG (thumbnails, and pictures
// over BIG_PICTURE before they become the background).
const BIG_PICTURE = 8 * 1024 * 1024;
const shrink = (url: string, max = 360, quality = 0.8) =>
  new Promise<string>((resolve) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(img.width * k));
      c.height = Math.max(1, Math.round(img.height * k));
      c.getContext("2d")?.drawImage(img, 0, 0, c.width, c.height);
      resolve(c.toDataURL("image/jpeg", quality));
    };
    img.onerror = () => resolve(url);
    img.src = url;
  });

const PICKER_CSS = `
  .hhctl-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; padding: 4px 0 8px; }
  .hhctl-tile {
    position: relative; overflow: hidden; border-radius: 6px; background: rgba(255, 255, 255, 0.08);
    outline: 2px solid transparent;
  }
  .hhctl-tile::after {
    content: ""; position: absolute; inset: 0; border-radius: 6px; border: 3px solid transparent; pointer-events: none;
  }
  .hhctl-tile.hhctl-focus { transform: scale(1.04); z-index: 1; }
  .hhctl-tile.hhctl-focus::after { border-color: #ffffff; box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.6); }
  .hhctl-tile.hhctl-focus span { background: rgba(255, 255, 255, 0.9); color: #000; }
  .hhctl-tile img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .hhctl-tile span {
    position: absolute; left: 0; right: 0; bottom: 0; padding: 2px 4px; font-size: 10px; line-height: 13px;
    background: rgba(0, 0, 0, 0.6); color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .hhctl-tile .hhctl-folder { display: flex; align-items: center; justify-content: center; height: 100%; font-size: 34px; }
`;

// Picture browser inside the panel. Tiles have the panel's shape and show the
// middle of each picture, which is what the background shows until adjusted.
function PicturePicker({
  aspect,
  onPick,
  onClose,
}: {
  aspect: number;
  onPick: (path: string, size: number) => void;
  onClose: () => void;
}) {
  const [list, setList] = useState<Listing | null>(null);
  const [, bump] = useState(0);
  // Which tile has gamepad focus; Steam's own focus ring doesn't show on these.
  const [focused, setFocused] = useState<string | null>(null);
  const focusProps = (key: string, activate: () => void) => ({
    className: `hhctl-tile${focused === key ? " hhctl-focus" : ""}`,
    focusClassName: "hhctl-focus",
    onGamepadFocus: () => setFocused(key),
    onGamepadBlur: () => setFocused((f) => (f === key ? null : f)),
    onFocus: () => setFocused(key),
    onActivate: activate,
    onClick: activate,
  });
  const open = async (folder: string | null) => setList(await listPictures(folder));
  useEffect(() => {
    open(null);
  }, []);
  // One thumbnail at a time, so a big folder doesn't flood the backend.
  useEffect(() => {
    if (!list) return;
    let stop = false;
    (async () => {
      for (const f of list.files) {
        if (stop) return;
        if (!thumbCache.has(f.path)) {
          const raw = await pictureThumb(f.path);
          thumbCache.set(f.path, raw ? await shrink(raw) : null);
          if (!stop) bump((n) => n + 1);
        }
      }
    })();
    return () => {
      stop = true;
    };
  }, [list?.folder]);

  const tile = { aspectRatio: `${aspect}` };
  return (
    <PanelSection>
      <style>{PICKER_CSS}</style>
      <PanelSectionRow>
        <Field
          label={<span className="hhctl-headlabel">Choose a picture</span>}
          description={[
            list?.folder.replace(list.home, "~"),
            list?.error,
            list?.more ? `Newest ${list.files.length} shown` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={onClose}>
          Cancel
        </ButtonItem>
      </PanelSectionRow>
      {list && (
        <Focusable className="hhctl-grid" flow-children="grid">
          {list.parent && (
            <Focusable style={tile} {...focusProps("..", () => open(list.parent))}>
              <div className="hhctl-folder">⬆</div>
              <span>Up</span>
            </Focusable>
          )}
          {list.dirs.map((d) => {
            const path = `${list.folder}/${d}`;
            return (
              <Focusable key={path} style={tile} {...focusProps(path, () => open(path))}>
                <div className="hhctl-folder">📁</div>
                <span>{d}</span>
              </Focusable>
            );
          })}
          {list.files.map((f) => {
            const t = thumbCache.get(f.path);
            return (
              <Focusable key={f.path} style={tile} {...focusProps(f.path, () => onPick(f.path, f.size))}>
                {t ? <img src={t} /> : <div className="hhctl-folder">{t === null ? "?" : "…"}</div>}
                <span>{f.name}</span>
              </Focusable>
            );
          })}
        </Focusable>
      )}
    </PanelSection>
  );
}

function Content() {
  const visible = useQuickAccessVisible();
  const [state, setState] = useState<State | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const timers = useRef<Partial<Record<Section, ReturnType<typeof setTimeout>>>>({});
  const lastEdit = useRef(0);
  const [bright, setBright] = useState<number | null>(null);
  const brightTimer = useRef<ReturnType<typeof setTimeout>>();
  const lastBrightEdit = useRef(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const [fitHeight, setFitHeight] = useState<number | null>(null);
  const fitRef = useRef<(() => void) | null>(null);
  const [view, setViewRaw] = useState<"main" | "pick" | "adjust">("main");
  const bodyRef = useRef<HTMLDivElement>(null);
  const backTo = useRef<HTMLElement | null>(null);
  // Quick Access moves a focus of its own (Steam's navigation tree; no DOM
  // focus events), so it's followed here and the setting it's on gets a frame.
  useEffect(() => {
    if (view !== "main") return;
    let lit: Element | null = null;
    // The frame is made here (not by React) so it can go into whichever copy
    // of the panel the setting is in.
    const r = document.createElement("div");
    r.className = "hhctl-ring";
    const ring = (el: Element | null) => {
      const host = (el?.closest?.(".hhctl-root") as HTMLElement | null) ?? rootRef.current;
      if (!host) return;
      if (r.ownerDocument !== host.ownerDocument || r.parentElement !== host) host.appendChild(host.ownerDocument.adoptNode(r));
      const box = el?.getBoundingClientRect();
      const area = ((host.querySelector(".hhctl-body") as HTMLElement | null) ?? host).getBoundingClientRect();
      if (!el || !box || box.height < 4 || box.bottom < area.top || box.top > area.bottom) {
        r.style.display = "none";
        return;
      }
      const at = host.getBoundingClientRect();
      const top = Math.max(box.top, area.top);
      Object.assign(r.style, {
        display: "block",
        left: `${box.left - at.left}px`,
        top: `${top - at.top}px`,
        width: `${box.width}px`,
        height: `${Math.min(box.bottom, area.bottom) - top}px`,
      });
      if (el !== lit) {
        r.classList.remove("hhctl-flash");
        void r.offsetWidth; // start the flash again
        r.classList.add("hhctl-flash");
      }
    };
    const id = setInterval(() => {
      const root = rootRef.current;
      if (!root || !qamVisible()) return;
      const { nodes, focused } = navNodes(root);
      // Which node Steam's focus is on, by whichever of these Steam keeps up:
      // its focus class on the element, the node's focused value, DOM focus,
      // the tree's last focused node.
      const val = (v: any) => (v && typeof v === "object" ? v.Value ?? v.m_currentValue : v);
      const byClass = nodes.filter((n) => n.m_element?.classList?.contains("gpfocus"));
      const byValue = nodes.filter((n) => val(n.m_Focused) === true);
      const active = root.ownerDocument.activeElement;
      const byDom = nodes.filter((n) => n.m_element === active);
      const one = (l: any[]) => (l.length === 1 ? l[0] : null);
      const cur = one(byClass) ?? one(byValue) ?? one(byDom) ?? focused;
      // Away: focus is outside the panel (a dropdown's list, the tabs, ...).
      const away = !cur || !root.ownerDocument.hasFocus() || !!(active && active !== root.ownerDocument.body && !active.closest?.(".hhctl-root"));
      // Steam's focus is on the control (switch, slider, button); the frame
      // goes around the whole setting it belongs to.
      const ctl0 = away ? null : (cur?.m_element as Element | undefined) ?? null;
      const el =
        ctl0?.closest?.('[class*="gamepaddialog_Field_"], [class*="PanelSectionRow"]') ?? ctl0;
      ring(el);
      lit = el;
    }, 120);
    // Keep the frame on the setting while the panel scrolls.
    const onScroll = () => lit && ring(lit);
    const body = bodyRef.current;
    body?.addEventListener("scroll", onScroll);
    return () => {
      clearInterval(id);
      body?.removeEventListener("scroll", onScroll);
      r.remove();
    };
  }, [view]);
  // Leaving the settings remembers the spot (and the focused button); coming
  // back restores both, so there's no scrolling back down.
  const setView = (v: "main" | "pick" | "adjust") => {
    if (view === "main" && v !== "main") backTo.current = document.activeElement as HTMLElement | null;
    setViewRaw(v);
    if (v === "main")
      requestAnimationFrame(() => {
        if (bodyRef.current) bodyRef.current.scrollTop = savedScroll;
        backTo.current?.focus?.();
      });
  };
  const [pickTag, setPickTag] = useState<string | null>(null);
  useEffect(() => {
    panelShown = true;
    return () => {
      panelShown = false;
    };
  }, []);

  const refresh = async () => {
    const s = await getState();
    setState(s);
    // Don't clobber a slider the user is still dragging.
    if (Date.now() - lastEdit.current > 1500) setDraft(s.settings);
    if (Date.now() - lastBrightEdit.current > 1500) setBright(s.display.brightness);
  };

  // The picture is fetched once per change, not on every 2 s refresh.
  const bgName = state?.settings.ui?.bg ?? null;
  const [bgUrl, setBgUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!bgName) {
      setBgUrl(null);
      return;
    }
    getBackground().then(setBgUrl);
  }, [bgName]);
  const [bgDims, setBgDims] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    setBgDims(null);
    if (!bgUrl) return;
    const img = new Image();
    img.onload = () => setBgDims({ w: img.naturalWidth, h: img.naturalHeight });
    img.src = bgUrl;
  }, [bgUrl]);

  // Like the picture, the font is fetched once per change.
  const fontName = state?.settings.ui?.font ?? "default";
  const [fontFaces, setFontFaces] = useState<string | null>(null);
  useEffect(() => {
    if (!FONTS[fontName]?.family) {
      setFontFaces(null);
      return;
    }
    getFont(fontName).then(setFontFaces);
  }, [fontName]);

  // Glass buttons: each button gets its size's filter, again whenever the panel's content or size changes.
  const glassOn = state?.settings.ui?.glass ?? true;
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const win = root.ownerDocument.defaultView ?? window;
    let frame = 0;
    const run = () => {
      frame = 0;
      glassButtons(root, glassOn);
    };
    const later = () => {
      if (!frame) frame = win.requestAnimationFrame(run);
    };
    run();
    const mo = new MutationObserver(later);
    mo.observe(root, { childList: true, subtree: true, characterData: true });
    const ro = new ResizeObserver(later);
    ro.observe(root);
    return () => {
      mo.disconnect();
      ro.disconnect();
      if (frame) win.cancelAnimationFrame(frame);
    };
  }, [glassOn, !!state]);

  // Make the trial's Keep prompt hard to miss, even outside this panel.
  const sbPending = !!state?.sdr_brightness.pending;
  useEffect(() => {
    if (sbPending)
      toaster.toast({
        title: "Keep the new Game Mode brightness?",
        body: "Press Keep in Handheld Control within 20 s, or it reverts.",
      });
  }, [sbPending]);

  // Size the panel to the QAM's visible area: the header stays put and only
  // the settings below it scroll.
  const themeName = state?.settings.ui?.theme ?? "default";
  useEffect(() => {
    themeNow = themeName;
    themeListeners.forEach((f) => f());
  }, [themeName]);

  const loaded = !!state;
  useEffect(() => {
    const root = rootRef.current;
    let scroller = root?.parentElement ?? null;
    while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)) scroller = scroller.parentElement;
    if (!root || !scroller) return;
    const sc = scroller;
    const fit = () => {
      const top = root.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop;
      // Down to the bottom edge of the visible area (over Decky's padding).
      let h = Math.max(240, Math.floor(sc.clientHeight - top));
      // Switch layouts before measuring: unfitted, the settings overflow the panel.
      root.classList.replace("hhctl-sticky", "hhctl-fit");
      root.style.marginBottom = "0px";
      root.style.height = `${h}px`;
      // Decky's padding and spacers after the panel would make the outer area
      // scroll (taking the header with it); a negative margin lets the panel
      // cover them instead of leaving an empty strip at the bottom.
      const extra = sc.scrollHeight - sc.clientHeight;
      if (extra > 0) root.style.marginBottom = `-${extra}px`;
      // Whatever still overflows (margins that don't give way) comes off the height.
      const left = sc.scrollHeight - sc.clientHeight;
      if (left > 0) {
        h = Math.max(240, h - left);
        root.style.height = `${h}px`;
      }
      sc.scrollTop = 0;
      setFitHeight(h);
    };
    fit();
    fitRef.current = fit;
    if (bodyRef.current) bodyRef.current.scrollTop = savedScroll; // reopened: back where it was
    const ro = new ResizeObserver(fit);
    ro.observe(sc);
    // On the first open Decky's previous page is still sliding out, so the
    // first measurement can come out short (half height); nothing resizes
    // the scroller after it leaves, so keep measuring until it has.
    // Every frame for the first second: corrected the moment it leaves.
    let raf = 0;
    const until = performance.now() + 1000;
    const tick = () => {
      fit();
      if (performance.now() < until) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf);
      fitRef.current = null;
    };
  }, [loaded]);

  useEffect(() => {
    if (!visible || !fitRef.current) return;
    let raf = 0;
    const until = performance.now() + 500;
    const tick = () => {
      fitRef.current?.();
      if (performance.now() < until) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    refresh();
    const id = setInterval(refresh, 2000);
    return () => clearInterval(id);
  }, [visible]);

  if (!state || !draft) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field label="Loading…" />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const change = <S extends Section>(section: S, values: Partial<Settings[S]>, delay = 350) => {
    lastEdit.current = Date.now();
    setDraft((d) => (d ? { ...d, [section]: { ...d[section], ...values } } : d));
    clearTimeout(timers.current[section]);
    timers.current[section] = setTimeout(async () => {
      setState(await setters[section](values));
    }, delay);
  };

  const changeBrightness = (v: number) => {
    lastBrightEdit.current = Date.now();
    setBright(v);
    clearTimeout(brightTimer.current);
    brightTimer.current = setTimeout(async () => setState(await setBrightness(v)), 150);
  };

  const { tdp, fan, battery, rgb, gpu, display } = state;
  const e = draft.epp;
  const { perf } = state;
  const dash = "–";
  const rows: {
    name: string;
    clock: string;
    util: string;
    power: string;
    temp: string;
    mem: string;
    capped: string[];
  }[] = [
    {
      name: "CPU",
      util: perf.cpu_util != null ? `${perf.cpu_util}%` : dash,
      clock: perf.cpu_mhz != null ? ghz(perf.cpu_mhz) : dash,
      power: gpu.cpu_w != null ? `${gpu.cpu_w_est ? "≈" : ""}${gpu.cpu_w} W` : dash,
      temp: perf.cpu_temp_c != null ? `${perf.cpu_temp_c}°C` : dash,
      mem:
        perf.ram_used_mb != null && perf.ram_total_mb
          ? `${(perf.ram_used_mb / 1024).toFixed(1)}/${Math.round(perf.ram_total_mb / 1024)} GB`
          : dash,
      capped: perf.limits.cpu,
    },
    {
      name: "GPU",
      util: gpu.util != null ? `${gpu.util}%` : dash,
      clock: gpu.freq_mhz != null ? `${gpu.freq_mhz} MHz` : dash,
      power: gpu.gpu_w != null ? `${gpu.gpu_w} W` : dash,
      temp: gpu.temp_c != null ? `${gpu.temp_c}°C` : dash,
      // Integrated Intel has no dedicated VRAM: GPU memory used out of system RAM.
      mem:
        gpu.vram_mb != null
          ? gpu.vram_total_mb
            ? `${(gpu.vram_mb / 1024).toFixed(1)}/${Math.round(gpu.vram_total_mb / 1024)} GB`
            : gb(gpu.vram_mb)
          : gpu.mem_mb != null
            ? gb(gpu.mem_mb)
            : dash,
      capped: perf.limits.gpu,
    },
  ];
  const showReadout = !!gpu.driver || perf.cpu_mhz != null;
  const t = draft.tdp;
  const g = draft.gpu;
  const f = draft.fan;
  const b = draft.battery;
  const r = draft.rgb;

  const theme = draft.ui?.theme ?? "default";
  // The device's (firmware) name on its own row on top, so long names
  // ("ONEXPLAYER F1 EVA-02") fit whole; the CPU model next to the TDP below. When the name
  // is the CPU (BIOS placeholders), it stays a single row.
  const deviceName = state.device.name;
  const cpuLine = state.device.cpu;
  const splitName = !!cpuLine && !deviceName.includes(cpuLine);
  const pos = draft.ui?.bg_pos ?? DEFAULT_POS;
  const boxW = rootRef.current?.clientWidth;
  const boxH = fitHeight ?? rootRef.current?.clientHeight;
  const box = boxW && boxH ? { w: boxW, h: boxH } : null;
  const setPos = (v: Partial<BgPos>) => change("ui", { bg_pos: { ...pos, ...v } }, 150);

  return (
    <div
      ref={rootRef}
      className={`hhctl-root ${fitHeight ? "hhctl-fit" : "hhctl-sticky"}`}
      style={{
        ...(bgUrl ? imageStyle(bgUrl, draft.ui?.bg_dim ?? 55, pos, bgDims, box) : {}),
        ...(fitHeight ? { height: fitHeight } : {}),
      }}
    >
      <style>{READOUT_CSS}</style>
      <style>{themeCss(theme, !!bgUrl)}</style>
      <style>{fontCss(fontName, fontFaces)}</style>
      {glassOn && <style>{glassCss(THEMES[theme]?.button)}</style>}
      <div className="hhctl-header">
        {splitName && (
          <div className="hhctl-device hhctl-name" style={{ color: THEMES[theme]?.title ?? "#ffffff" }}>
            {deviceName}
          </div>
        )}
        <div className="hhctl-top">
          {splitName ? (
            <span className="hhctl-cpu">{cpuLine}</span>
          ) : (
            <span className="hhctl-device" style={{ color: THEMES[theme]?.title ?? "#ffffff" }}>
              {deviceName}
            </span>
          )}
          {gpu.pkg_w != null && (
            <span className="hhctl-tdp">
              TDP {gpu.pkg_w}
              {perf.tdp_w ? ` / ${perf.tdp_w}` : ""} W
              {state.battery.remaining_min != null &&
                ` · ${Math.floor(state.battery.remaining_min / 60)}:${String(state.battery.remaining_min % 60).padStart(2, "0")}`}
            </span>
          )}
        </div>
        {showReadout && (
          <div className="hhctl-readout">
            <span />
            <span>Util</span>
            <span>Clock</span>
            <span>Power</span>
            <span>Temp</span>
            <span>Memory</span>
            {rows.map((r) => (
              <Fragment key={r.name}>
                <b>{r.name}</b>
                <span>{r.util}</span>
                <span>{r.clock}</span>
                <span>{r.power}</span>
                <span>{r.temp}</span>
                <span>{r.mem}</span>
                {/* Always shown, so the header keeps its height. */}
                <i className={`hhctl-capped${r.capped.length ? "" : " hhctl-free"}`}>
                  {r.capped.length ? `capped by ${reasons(r.capped)}` : "not capped"}
                </i>
              </Fragment>
            ))}
          </div>
        )}
      </div>
      <div
        ref={bodyRef}
        className="hhctl-body"
        onScroll={(ev) => {
          if (view === "main") savedScroll = (ev.target as HTMLElement).scrollTop;
        }}
        // D-pad focus: keep the focused setting inside the scroll area.
        onFocus={(ev) => fitHeight && (ev.target as HTMLElement).scrollIntoView?.({ block: "nearest" })}
      >
        {view === "pick" && (
          <PicturePicker
            aspect={box ? box.w / box.h : 0.55}
            onClose={() => setView("main")}
            onPick={async (path, size) => {
              if (size > BIG_PICTURE) {
                // Too big to keep as is: shrink it here and send it back.
                const raw = await pictureData(path);
                if (raw) setState(await setBackgroundData(await shrink(raw, 2400, 0.9)));
              } else {
                setState(await setBackground(path));
              }
              setViewRaw("adjust");
            }}
          />
        )}
        {view === "adjust" && (
          <PanelSection>
            <PanelSectionRow>
              <Field
                label={<span className="hhctl-headlabel">Adjust picture</span>}
                description="Pick the part of the picture behind the panel; it moves as you go"
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Zoom"
                description="How much the picture is enlarged; 100% fits it to the panel"
                value={pos.zoom}
                min={100}
                max={400}
                step={10}
                showValue
                valueSuffix="%"
                onChange={(v) => setPos({ zoom: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Left – right"
                description="Which part of the picture shows when it is wider than the panel"
                value={pos.x}
                min={0}
                max={100}
                step={1}
                showValue
                valueSuffix="%"
                onChange={(v) => setPos({ x: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Top – bottom"
                description="Which part of the picture shows when it is taller than the panel"
                value={pos.y}
                min={0}
                max={100}
                step={1}
                showValue
                valueSuffix="%"
                onChange={(v) => setPos({ y: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Picture dimming"
                description="Darkens the picture so the text on top stays readable"
                value={draft.ui.bg_dim ?? 55}
                min={0}
                max={90}
                step={5}
                showValue
                valueSuffix="%"
                onChange={(v) => change("ui", { bg_dim: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => setPos(DEFAULT_POS)}>
                Reset to centered
              </ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => setView("main")}>
                Done
              </ButtonItem>
            </PanelSectionRow>
          </PanelSection>
        )}
        <div className="hhctl-main" style={view === "main" ? undefined : { display: "none" }}>
          <GameProfileRows state={state} setState={setState} />
          <PanelSection>
            {!tdp.backend ? (
              <Unavailable why="No TDP interface found (firmware-attributes, RAPL, ryzenadj or the AMD SMU)." />
            ) : (
              <>
                <PanelSectionRow>
                  <ToggleField
                    label="Custom TDP"
                    description={`via ${tdp.backend}${tdp.current ? ` · now ${tdp.current} W` : ""}${tdp.profile ? ` · ${tdp.profile} profile` : ""}`}
                    checked={t.enabled}
                    onChange={(v) => change("tdp", { enabled: v, watts: t.watts ?? tdp.default }, 0)}
                  />
                </PanelSectionRow>
                {t.enabled && (
                  <>
                    <PanelSectionRow>
                      <SliderField
                        label="Sustained power"
                        description="Whole package: CPU and GPU share this limit"
                        value={t.watts ?? tdp.default}
                        min={tdp.min}
                        max={tdp.max}
                        step={1}
                        showValue
                        valueSuffix=" W"
                        onChange={(v) => change("tdp", { watts: v })}
                      />
                    </PanelSectionRow>
                    <PanelSectionRow>
                      <ToggleField
                        label="Power boost"
                        description="Allow short bursts above the sustained power limit (PL2 / fast PPT)"
                        checked={t.boost}
                        onChange={(v) => change("tdp", { boost: v }, 0)}
                      />
                    </PanelSectionRow>
                  </>
                )}
                <ErrorRow msg={state.errors.tdp} />
              </>
            )}
            {state.epp.available && (
              <>
                <PanelSectionRow>
                  <ToggleField
                    label="Energy preference (EPP)"
                    description={`${state.epp.driver}${state.epp.current ? ` · now ${EPP_LABELS[state.epp.current] ?? state.epp.current}` : ""}`}
                    checked={e.enabled}
                    onChange={(v) => change("epp", { enabled: v }, 0)}
                  />
                </PanelSectionRow>
                {e.enabled && (
                  <PanelSectionRow>
                    <DropdownItem
                      label="Preference"
                      description="Anything but Performance switches the CPU governor to powersave"
                      rgOptions={state.epp.options.map((o) => ({
                        data: o,
                        label: EPP_LABELS[o] ?? titleCase(o),
                      }))}
                      selectedOption={e.value}
                      onChange={(o) => change("epp", { value: o.data }, 0)}
                    />
                  </PanelSectionRow>
                )}
                <ErrorRow msg={state.errors.epp} />
              </>
            )}
            <PanelSectionRow>
              <ToggleField
                label="CPU boost (turbo)"
                description={
                  state.cpu.boost.available
                    ? "Clocks above base frequency. Off saves a lot of power and heat."
                    : `Unavailable: ${state.cpu.boost.unavailable}`
                }
                checked={draft.cpu.boost ?? state.cpu.boost.on ?? false}
                disabled={!state.cpu.boost.available}
                onChange={(v) => change("cpu", { boost: v }, 0)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Preferred cores"
                description={
                  state.cpu.prefcore.available
                    ? "Schedule work on the cores the firmware ranks fastest (CPPC)"
                    : `Unavailable: ${state.cpu.prefcore.unavailable}`
                }
                checked={draft.cpu.prefcore ?? state.cpu.prefcore.on ?? false}
                disabled={!state.cpu.prefcore.available}
                onChange={(v) => change("cpu", { prefcore: v }, 0)}
              />
            </PanelSectionRow>
            {state.cpu.hwp_boost.available && (
              <PanelSectionRow>
                <ToggleField
                  label="HWP dynamic boost"
                  description="Briefly raises the CPU's minimum clock when a task wakes from I/O. Can smooth frame times; costs a little power."
                  checked={draft.cpu.hwp_boost ?? state.cpu.hwp_boost.on ?? false}
                  onChange={(v) => change("cpu", { hwp_boost: v }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.cpu.epp_boost.available && (
              <PanelSectionRow>
                <ToggleField
                  label="EPP boost"
                  description="Busy cores get EPP Performance for a moment, the rest keep your EPP. Steadier frame times than EPP Performance for less power."
                  checked={draft.cpu.epp_boost ?? state.cpu.epp_boost.on ?? false}
                  onChange={(v) => change("cpu", { epp_boost: v }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.cpu.cores.available && (
              <PanelSectionRow>
                <DropdownItem
                  label="Cores for apps and games"
                  description="System services keep every core. Resets on reboot until the plugin applies it again."
                  rgOptions={[
                    {
                      data: "all",
                      label: `All cores (${coreCount(
                        state.cpu.cores.fast_cores + state.cpu.cores.small_cores,
                        state.cpu.cores.fast + state.cpu.cores.small,
                      )})`,
                    },
                    { data: "fast", label: `${state.cpu.cores.fast_name} only (${coreCount(state.cpu.cores.fast_cores, state.cpu.cores.fast)})` },
                    { data: "small", label: `${state.cpu.cores.small_name} only (${coreCount(state.cpu.cores.small_cores, state.cpu.cores.small)})` },
                  ]}
                  selectedOption={draft.cpu.cores ?? (state.cpu.cores.current === "custom" ? null : state.cpu.cores.current) ?? "all"}
                  onChange={(o) => change("cpu", { cores: o.data }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.cpu.smt.available && (
              <PanelSectionRow>
                <ToggleField
                  label="SMT (2 threads per core)"
                  description="Off halves the threads: saves a little power and can steady frame times in older games. Heavily threaded games run slower."
                  checked={draft.cpu.smt ?? state.cpu.smt.on ?? true}
                  onChange={(v) => change("cpu", { smt: v }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.cpu.ccx.available && (
              <PanelSectionRow>
                <ToggleField
                  label="First CCX only"
                  description={`Takes the other CCX offline: ${state.cpu.ccx_keep} of ${state.cpu.ccx_total} threads stay on. Saves some power in light games; heavily threaded games run slower.`}
                  checked={draft.cpu.ccx_off ?? state.cpu.ccx.on ?? false}
                  onChange={(v) => change("cpu", { ccx_off: v }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.cpu.cap.available && (
              <>
                <PanelSectionRow>
                  <ToggleField
                    label="Limit CPU clock"
                    description={`A ceiling, not a lock: leaves more of the TDP for the GPU${
                      state.cpu.cap.avg != null ? ` · now avg ${ghz(state.cpu.cap.avg)}` : ""
                    }`}
                    checked={draft.cpu.cap}
                    onChange={(v) =>
                      change(
                        "cpu",
                        {
                          cap: v,
                          cap_mhz: draft.cpu.cap_mhz ?? state.cpu.cap.max,
                        },
                        0,
                      )
                    }
                  />
                </PanelSectionRow>
                <PanelSectionRow>
                  <div className={draft.cpu.cap ? undefined : "hhctl-off"}>
                    <SliderField
                      label="Max CPU clock"
                      description={
                        !draft.cpu.cap
                          ? "Turn on Limit CPU clock to set it"
                          : state.cpu.cap.limits.length > 1
                            ? `Each core type stays within its own range · now ${state.cpu.cap.limits.map(ghz).join(" / ")}`
                            : undefined
                      }
                      value={draft.cpu.cap_mhz ?? state.cpu.cap.max}
                      min={state.cpu.cap.min}
                      max={state.cpu.cap.max}
                      step={100}
                      showValue
                      valueSuffix=" MHz"
                      disabled={!draft.cpu.cap}
                      onChange={(v) => change("cpu", { cap_mhz: v })}
                    />
                  </div>
                </PanelSectionRow>
              </>
            )}
            {state.thermal.available && state.thermal.range && (
              <>
                <PanelSectionRow>
                  <ToggleField
                    label="Temperature limit"
                    description={`Boosts run full until the CPU reaches it, then it holds there${
                      perf.cpu_temp_c != null ? ` · now ${perf.cpu_temp_c}°C` : ""
                    } · via ${state.thermal.backend}`}
                    checked={draft.cpu.temp_limit != null}
                    onChange={(v) =>
                      change("cpu", { temp_limit: v ? Math.min(85, state.thermal.range![1]) : null }, 0)
                    }
                  />
                </PanelSectionRow>
                <PanelSectionRow>
                  <div className={draft.cpu.temp_limit != null ? undefined : "hhctl-off"}>
                    <SliderField
                      label="Max CPU temperature"
                      description={draft.cpu.temp_limit != null ? undefined : "Turn on Temperature limit to set it"}
                      value={draft.cpu.temp_limit ?? Math.min(85, state.thermal.range[1])}
                      min={state.thermal.range[0]}
                      max={state.thermal.range[1]}
                      step={1}
                      showValue
                      valueSuffix=" °C"
                      disabled={draft.cpu.temp_limit == null}
                      onChange={(v) => change("cpu", { temp_limit: v })}
                    />
                  </div>
                </PanelSectionRow>
                <ErrorRow msg={state.errors.thermal} />
              </>
            )}
            {state.memtweak && Object.keys(state.memtweak.available).length > 0 && (
              <>
                <PanelSectionRow>
                  <ToggleField
                    label="Gaming memory settings (SteamOS)"
                    description={`Fewer stutters, not more fps: no background memory compaction, fairer page locks, and no slow-down of games that do split locks. ${
                      state.memtweak.pending.length === 0
                        ? "Already set this way here."
                        : `Not set here yet: ${state.memtweak.pending.join(", ")}.`
                    } Only changes those values while on; off, unload or a restart puts the old ones back.`}
                    checked={!!draft.cpu.memtweak}
                    onChange={(v) => change("cpu", { memtweak: v }, 0)}
                  />
                </PanelSectionRow>
                <ErrorRow msg={state.errors.memtweak} />
              </>
            )}
            {state.ai && (
              <>
                <PanelSectionRow>
                  <ToggleField
                    label="Tune for AI apps (oneDNN)"
                    description={`For PyTorch, OpenVINO, ONNX Runtime and llama.cpp; games don't use oneDNN, so they're unaffected. Sets ${Object.entries(
                      state.ai.vars,
                    )
                      .map(([k, v]) => `${k}=${v}`)
                      .join(", ")}${
                      state.ai.vars.ONEDNN_DEFAULT_FPMATH_MODE ? " (bf16 math: faster, slightly less precise)" : ""
                    }. Apps started after the next login pick it up.`}
                    checked={state.ai.enabled}
                    onChange={async (v) => setState(await setAi(v))}
                  />
                </PanelSectionRow>
                <ErrorRow msg={state.errors.ai} />
              </>
            )}
            {state.cpu.scx.available && (
              <>
                <PanelSectionRow>
                  <DropdownItem
                    label="CPU scheduler"
                    description={(() => {
                      const sel =
                        draft.cpu.scx ??
                        state.cpu.scx.schedulers.find((x) => x.label === state.cpu.scx.running)?.name ??
                        "off";
                      const note = SCX_NOTES[sel.replace(/^scx_/, "")];
                      return `now ${state.cpu.scx.running ?? "kernel default"}${note ? ` · ${note}` : ""}`;
                    })()}
                    rgOptions={[
                      { data: "off", label: "Kernel default" },
                      ...state.cpu.scx.schedulers.map((x) => ({
                        data: x.name,
                        label: SCX_TAGS[x.label] ? `${x.name} · ${SCX_TAGS[x.label]}` : x.name,
                      })),
                    ]}
                    selectedOption={
                      draft.cpu.scx ??
                      state.cpu.scx.schedulers.find((x) => x.label === state.cpu.scx.running)?.name ??
                      "off"
                    }
                    onChange={(o) => change("cpu", { scx: o.data }, 0)}
                  />
                </PanelSectionRow>
                {draft.cpu.scx != null && draft.cpu.scx !== "off" && (
                  <PanelSectionRow>
                    <DropdownItem
                      label="Scheduler mode"
                      description="How the scheduler above weighs things: Gaming favours the game's threads, Power save keeps fewer cores awake, Low latency answers input fastest; Auto lets it decide"
                      rgOptions={state.cpu.scx.modes.map((m) => ({ data: m, label: SCX_MODES[m] ?? titleCase(m) }))}
                      selectedOption={draft.cpu.scx_mode}
                      onChange={(o) => change("cpu", { scx_mode: o.data }, 0)}
                    />
                  </PanelSectionRow>
                )}
                <ErrorRow msg={state.errors.scx} />
              </>
            )}
            <ErrorRow msg={state.errors.cpu} />
          </PanelSection>

          {gpu.clock.available && (
            <PanelSection>
              {gpu.clock.modes.includes("manual") && (
                <PanelSectionRow>
                  <div className={g.mode === "manual" ? undefined : "hhctl-off"}>
                    <SliderField
                      label="GPU clock"
                      description={g.mode === "manual" ? undefined : "Pick Manual under GPU clock mode to set it"}
                      value={g.mhz ?? gpu.clock.max}
                      min={gpu.clock.min}
                      max={gpu.clock.max}
                      step={50}
                      showValue
                      valueSuffix=" MHz"
                      disabled={g.mode !== "manual"}
                      onChange={(v) => change("gpu", { mode: "manual", mhz: v, profile: null })}
                    />
                  </div>
                </PanelSectionRow>
              )}
              <PanelSectionRow>
                <DropdownItem
                  label="GPU clock mode"
                  description={`${GPU_MODES[g.mode]?.description ?? ""} · ${gpu.clock.min}–${gpu.clock.max} MHz${
                    gpu.freq_mhz != null ? ` · now ${gpu.freq_mhz} MHz` : ""
                  }`}
                  rgOptions={gpu.clock.modes.map((m) => ({
                    data: m,
                    label: GPU_MODES[m].label,
                  }))}
                  selectedOption={g.mode}
                  onChange={(o) =>
                    change(
                      "gpu",
                      { mode: o.data, mhz: g.mhz ?? gpu.clock.max, profile: o.data === "auto" ? g.profile : null },
                      0,
                    )
                  }
                />
              </PanelSectionRow>
              {gpu.profile.available && (
                <PanelSectionRow>
                  <DropdownItem
                    label="GPU power profile"
                    description={
                      g.mode !== "auto"
                        ? "Off while the GPU clock is locked; picking one sets the clock mode back to Auto"
                        : "How eagerly the GPU ramps its clock. Picking one sets the clock mode to Auto."
                    }
                    rgOptions={[
                      { data: "off", label: "Off (driver default)" },
                      ...gpu.profile.options
                        .filter((o) => o !== "BOOTUP_DEFAULT")
                        .map((o) => ({ data: o, label: GPU_PROFILES[o] ?? titleCase(o.toLowerCase()) })),
                    ]}
                    selectedOption={g.profile ?? "off"}
                    onChange={(o) =>
                      change("gpu", o.data === "off" ? { profile: null } : { profile: o.data, mode: "auto" }, 0)
                    }
                  />
                </PanelSectionRow>
              )}
              {gpu.clock.throttle.length > 0 && (
                <PanelSectionRow>
                  <Field
                    label="Held back by"
                    description={`${reasons(gpu.clock.throttle)}. Raise the TDP or limit the CPU clock to give the GPU more power.`}
                  />
                </PanelSectionRow>
              )}
              {gpu.pipes && (
                <PanelSectionRow>
                  <Field
                    label="Second graphics pipe"
                    description={
                      {
                        on: "On: the kernel runs two graphics queues",
                        off: "Off: needs Linux 7.3+ (RDNA 3 / 3.5 APUs only); turns on by itself after a kernel update",
                        unsupported: `Not on this GPU (GFX ${gpu.pipes.gc}): only RDNA 3 / 3.5 APUs have it`,
                        unknown: "Can't tell: debugfs isn't readable (kernel lockdown?)",
                      }[gpu.pipes.state]
                    }
                  />
                </PanelSectionRow>
              )}
              <VramRows state={state} setState={setState} />
              {state.rebar && (
                <PanelSectionRow>
                  <Field
                    label="Resizable BAR"
                    description={`${state.rebar.text} Only the BIOS can switch it; Linux uses what it gets at boot.`}
                  >
                    {{ on: "On", off: "Off", partial: "Partly", "n/a": "–" }[state.rebar.state]}
                  </Field>
                </PanelSectionRow>
              )}
              <UpscaleRows state={state} setState={setState} />
              <ErrorRow msg={state.errors.gpu} />
            </PanelSection>
          )}
          <SystemRows state={state} setState={setState} />
          <TopUseRows state={state} setState={setState} />

          <PanelSection>
            {!fan.backend ? (
              <Unavailable
                why={
                  fan.restarting
                    ? `Fan control stopped (${fan.unavailable}); the firmware runs the fan and the plugin takes it up again in a moment`
                    : `Fan: ${fan.unavailable ?? "no fan control"}`
                }
              />
            ) : (
              <>
                <PanelSectionRow>
                  {/* Full-width row like BATT: in a Field the % ran off the edge. */}
                  <div className="hhctl-statusrow">
                    <span className="hhctl-headlabel">FAN</span>
                    <span>
                      {[
                        fan.temp != null ? `${Math.round(fan.temp)} °C` : null,
                        fan.rpm != null ? `${fan.rpm} RPM` : null,
                        fan.percent != null ? `${fan.percent}%` : null,
                        fan.safety ? "safety 100%" : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </div>
                </PanelSectionRow>
                <PanelSectionRow>
                  <DropdownItem
                    label="Fan mode"
                    description={`via ${fan.backend}${fan.watchdog ? " · watchdog armed" : ""}`}
                    rgOptions={[
                      { data: "auto", label: "Auto (firmware)" },
                      { data: "curve", label: "Curve" },
                      { data: "fixed", label: "Fixed" },
                    ]}
                    selectedOption={f.mode}
                    onChange={(o) => change("fan", { mode: o.data }, 0)}
                  />
                </PanelSectionRow>
                {f.mode === "curve" && (
                  <PanelSectionRow>
                    <DropdownItem
                      label="Curve"
                      description="Fan speed by temperature: Quiet runs slower and warmer, Performance faster and cooler, Custom uses the points you set below"
                      rgOptions={fan.curves.map((c) => ({
                        data: c,
                        label: titleCase(c),
                      }))}
                      selectedOption={f.curve}
                      onChange={(o) => change("fan", { curve: o.data }, 0)}
                    />
                  </PanelSectionRow>
                )}
                {f.mode === "curve" &&
                  f.curve === "custom" &&
                  (fan.custom_temps ?? []).map((temp, i) => {
                    const pts = f.points ?? fan.custom_default ?? [];
                    return (
                      <PanelSectionRow key={temp}>
                        <SliderField
                          label={`At ${temp} °C`}
                          description={i === 0 ? "A point never goes below the one before it" : undefined}
                          value={pts[i] ?? 50}
                          min={0}
                          max={100}
                          step={5}
                          showValue
                          valueSuffix="%"
                          onChange={(v) => {
                            const next = [...pts];
                            next[i] = v;
                            for (let j = i + 1; j < next.length; j++) next[j] = Math.max(next[j], v);
                            for (let j = 0; j < i; j++) next[j] = Math.min(next[j], v);
                            change("fan", { points: next });
                          }}
                        />
                      </PanelSectionRow>
                    );
                  })}
                {f.mode === "fixed" && (
                  <PanelSectionRow>
                    <SliderField
                      label="Speed"
                      description="Forced to 100% above 90 °C"
                      value={f.percent}
                      min={0}
                      max={100}
                      step={5}
                      showValue
                      valueSuffix="%"
                      onChange={(v) => change("fan", { percent: v })}
                    />
                  </PanelSectionRow>
                )}
                {fan.note && (
                  <PanelSectionRow>
                    <Field description={fan.note} />
                  </PanelSectionRow>
                )}
                <ErrorRow msg={state.errors.fan} />
              </>
            )}
          </PanelSection>

          <PanelSection>
            <PanelSectionRow>
              {/* A plain full-width row: in a Field the values only get the right-hand
                  part and the watts were cut off. Kept 3 characters in from the edge. */}
              <div className="hhctl-statusrow">
                <span className="hhctl-headlabel">BATT</span>
                <span>
                  {[
                    battery.capacity != null ? `${battery.capacity}%` : null,
                    battery.ac && battery.inhibited && (battery.power_w ?? 0) < 1 ? "Held" : battery.status,
                    battery.power_w != null ? `${battery.power_w} W` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </div>
            </PanelSectionRow>
            {battery.unavailable && !battery.limit_mode ? (
              <Unavailable why={battery.unavailable} />
            ) : (
              <>
                {battery.limit_mode && (
                  <PanelSectionRow>
                    <SliderField
                      label="Charge limit"
                      description={
                        battery.limit_mode === "emulated" ? "Enforced by the plugin while it runs" : undefined
                      }
                      value={b.limit}
                      min={50}
                      max={100}
                      step={5}
                      showValue
                      valueSuffix="%"
                      onChange={(v) => change("battery", { limit: v })}
                    />
                  </PanelSectionRow>
                )}
                {battery.bypass_options.length > 1 && (
                  <PanelSectionRow>
                    <DropdownItem
                      label="Charge bypass"
                      description="Run from the charger without charging the battery"
                      rgOptions={battery.bypass_options.map((o) => ({
                        data: o,
                        label: BYPASS_LABELS[o] ?? o,
                      }))}
                      selectedOption={b.bypass}
                      onChange={(o) => change("battery", { bypass: o.data }, 0)}
                    />
                  </PanelSectionRow>
                )}
              </>
            )}
            <ErrorRow msg={state.errors.battery} />
          </PanelSection>

          <PanelSection>
            {!rgb.backend ? (
              <Unavailable why="No RGB controller found." />
            ) : (
              <>
                <PanelSectionRow>
                  <ToggleField
                    label="RGB lighting"
                    description={`via ${rgb.backend}`}
                    checked={r.enabled}
                    onChange={(v) => change("rgb", { enabled: v }, 0)}
                  />
                </PanelSectionRow>
                {r.enabled && (
                  <>
                    <PanelSectionRow>
                      <DropdownItem
                        label="Mode"
                        description="How the lights behave: one solid color, an effect, or off"
                        rgOptions={rgb.modes.map((m) => ({
                          data: m,
                          label: titleCase(m),
                        }))}
                        selectedOption={r.mode}
                        onChange={(o) => change("rgb", { mode: o.data }, 0)}
                      />
                    </PanelSectionRow>
                    {r.mode === "effect" && rgb.effects.length > 0 && (
                      <PanelSectionRow>
                        <DropdownItem
                          label="Effect"
                          description="The light animation the controller itself runs"
                          rgOptions={rgb.effects.map((e) => ({
                            data: e,
                            label: titleCase(e),
                          }))}
                          selectedOption={r.effect ?? rgb.effects[0]}
                          onChange={(o) => change("rgb", { effect: o.data }, 0)}
                        />
                      </PanelSectionRow>
                    )}
                    {(r.mode === "solid" || r.mode === "pulse") && (
                      <PanelSectionRow>
                        <ToggleField
                          label="Match theme color"
                          description={
                            THEME_RGB[theme]
                              ? "The lights follow the theme color"
                              : "Pick a colored theme in the last section; Default keeps your own color"
                          }
                          checked={!!r.match_theme}
                          onChange={(v) => change("rgb", { match_theme: v }, 0)}
                        />
                      </PanelSectionRow>
                    )}
                    {(r.mode === "solid" || r.mode === "pulse") && !(r.match_theme && THEME_RGB[theme]) && (
                      <>
                        <PanelSectionRow>
                          <div
                            style={{
                              height: 12,
                              borderRadius: 6,
                              background: `hsl(${r.hue}, ${r.sat}%, ${50 + (100 - r.sat) / 2}%)`,
                            }}
                          />
                        </PanelSectionRow>
                        <PanelSectionRow>
                          <SliderField
                            label="Hue"
                            description="The color, around the color wheel (0 red, 120 green, 240 blue)"
                            value={r.hue}
                            min={0}
                            max={359}
                            step={5}
                            showValue
                            valueSuffix="°"
                            onChange={(v) => change("rgb", { hue: v })}
                          />
                        </PanelSectionRow>
                        <PanelSectionRow>
                          <SliderField
                            label="Saturation"
                            description="0% is white, 100% the full color"
                            value={r.sat}
                            min={0}
                            max={100}
                            step={5}
                            showValue
                            valueSuffix="%"
                            onChange={(v) => change("rgb", { sat: v })}
                          />
                        </PanelSectionRow>
                      </>
                    )}
                    {r.mode !== "off" && (
                      <PanelSectionRow>
                        <SliderField
                          label="Brightness"
                          description="How bright the lights are; 0% is off"
                          value={r.bright}
                          min={0}
                          max={100}
                          step={5}
                          showValue
                          valueSuffix="%"
                          onChange={(v) => change("rgb", { bright: v })}
                        />
                      </PanelSectionRow>
                    )}
                  </>
                )}
                {rgb.zone_test_available && (
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  description={
                    rgb.zone_test
                      ? `Sent: ${rgb.zone_test}. Note which part shows which color (stick rings, top logo, power button ring), then change any RGB setting to go back.`
                      : "Lights every possible zone in its own color at once, to find out which part is which"
                  }
                  onClick={async () => setState(await rgbZoneTest())}
                >
                  Light zone test
                </ButtonItem>
              </PanelSectionRow>
            )}
            {state.sleep.rgb_off_available && (
              <PanelSectionRow>
                <ToggleField
                  label="Lights off while asleep or off"
                  description={
                    draft.rgb.enabled
                      ? "Turned off just before it suspends or powers off, back on when it wakes"
                      : "Turn on the lights setting above first: the plugin only switches lights it controls (it puts them back on after waking)"
                  }
                  disabled={!draft.rgb.enabled}
                  checked={draft.sleep.rgb_off && draft.rgb.enabled}
                  onChange={(v) => change("sleep", { rgb_off: v }, 0)}
                />
              </PanelSectionRow>
            )}
            <ErrorRow msg={state.errors.rgb} />
              </>
            )}
          </PanelSection>

          {(display.backlight || display.bootloader || state.gamescope.running) && (
            <PanelSection>
              {display.backlight && bright != null && (
                <PanelSectionRow>
                  <SliderField
                    label="Screen brightness"
                    description="Writes the backlight directly, so it also works in Game Mode"
                    value={bright}
                    min={5}
                    max={100}
                    step={1}
                    showValue
                    valueSuffix="%"
                    onChange={changeBrightness}
                  />
                </PanelSectionRow>
              )}
              {display.backlight && (
                <>
                  <PanelSectionRow>
                    <ButtonItem
                      layout="below"
                      disabled={display.test.running}
                      description="Steps brightness to 25%, 75% and back, and saves a report. Watch the screen while it runs."
                      onClick={async () => setState(await brightnessTest())}
                    >
                      {display.test.running ? "Testing… watch the screen" : "Run brightness test"}
                    </ButtonItem>
                  </PanelSectionRow>
                  {display.test.verdict && (
                    <PanelSectionRow>
                      <Field
                        label="Result"
                        description={[
                          display.test.verdict,
                          display.test.report ? `Report: ${display.test.report}` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      />
                    </PanelSectionRow>
                  )}
                  {display.test.verdict && display.test.answer === null && (
                    <>
                      <PanelSectionRow>
                        <Field label="When did the screen get darker, then brighter?" />
                      </PanelSectionRow>
                      {(display.test.phases > 1
                        ? [
                            ["first", "Only in the first pass"],
                            ["second", "Only in the second pass"],
                            ["both", "In both passes"],
                            ["neither", "Never"],
                          ]
                        : [
                            ["first", "It changed"],
                            ["neither", "It stayed the same"],
                          ]
                      ).map(([value, label]) => (
                        <PanelSectionRow key={value}>
                          <ButtonItem layout="below" onClick={async () => setState(await brightnessTestAnswer(value))}>
                            {label}
                          </ButtonItem>
                        </PanelSectionRow>
                      ))}
                    </>
                  )}
                  {display.test.answer !== null && (
                    <PanelSectionRow>
                      <Field description={`Answer saved to the report (${display.test.answer}).`} />
                    </PanelSectionRow>
                  )}
                </>
              )}
              {Object.entries(display.toggles).map(([group, t]) => (
                <PanelSectionRow key={group}>
                  <ToggleField
                    label={DISPLAY_TOGGLES[group]?.label ?? group}
                    description={[
                      t.blocked ?? DISPLAY_TOGGLES[group]?.description,
                      t.params.join(" "),
                      t.in_entries === false
                        ? "Saved, but not in the boot entries yet"
                        : t.reboot
                          ? "Reboot required"
                          : t.active
                            ? "Active"
                            : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                    checked={t.configured}
                    disabled={display.busy || (!!t.blocked && !t.configured)}
                    onChange={async (v) => setState(await setDisplay(group, v))}
                  />
                </PanelSectionRow>
              ))}
              {Object.entries(display.toggles)
                .filter(([, t]) => t.in_entries === false && !(t.blocked && t.configured))
                .map(([group, t]) => (
                  <PanelSectionRow key={`${group}-apply`}>
                    <ButtonItem
                      layout="below"
                      disabled={display.busy}
                      description={`Rebuilds the ${display.bootloader} boot entries so the change takes effect at the next boot`}
                      onClick={async () => setState(await setDisplay(group, t.configured))}
                    >
                      Apply to boot entries
                    </ButtonItem>
                  </PanelSectionRow>
                ))}
              {display.message && (
                <PanelSectionRow>
                  <Field description={display.message} />
                </PanelSectionRow>
              )}
              {state.sdr_brightness.available && (
                <>
                  <PanelSectionRow>
                    <ToggleField
                      label="Game Mode brightness"
                      description={[
                        "Dims or brightens the picture through gamescope while HDR stays on, which this panel needs.",
                        state.sdr_brightness.current != null
                          ? `Now ${state.sdr_brightness.current} nits`
                          : "Now at gamescope's default",
                        !state.sdr_brightness.confirmed ? "The first change must be confirmed within 20 s." : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                      checked={draft.sdr_brightness.enabled}
                      onChange={(v) => change("sdr_brightness", { enabled: v, nits: draft.sdr_brightness.nits }, 0)}
                    />
                  </PanelSectionRow>
                  {draft.sdr_brightness.enabled && (
                    <PanelSectionRow>
                      <SliderField
                        label="Brightness"
                        description="How bright Steam and normal (SDR) games look while the screen runs in HDR, in nits"
                        value={draft.sdr_brightness.nits}
                        min={state.sdr_brightness.min}
                        max={state.sdr_brightness.max}
                        step={10}
                        showValue
                        valueSuffix=" nits"
                        onChange={(v) => change("sdr_brightness", { enabled: true, nits: v })}
                      />
                    </PanelSectionRow>
                  )}
                  {state.sdr_brightness.pending && (
                    <>
                      <PanelSectionRow>
                        <Field
                          label="Keep this brightness?"
                          description={`Reverting in ${state.sdr_brightness.pending.seconds} s unless you keep it.`}
                        />
                      </PanelSectionRow>
                      <PanelSectionRow>
                        <ButtonItem layout="below" onClick={async () => setState(await confirmSdrBrightness(true))}>
                          Keep
                        </ButtonItem>
                      </PanelSectionRow>
                      <PanelSectionRow>
                        <ButtonItem layout="below" onClick={async () => setState(await confirmSdrBrightness(false))}>
                          Revert now
                        </ButtonItem>
                      </PanelSectionRow>
                    </>
                  )}
                  <ErrorRow msg={state.errors.sdr_brightness} />
                </>
              )}
              {state.gamescope.running && state.gamescope.blocked && (
                <PanelSectionRow>
                  <Field
                    label="Game Mode HDR"
                    description={`${
                      state.gamescope.hdr_active === null
                        ? ""
                        : `HDR output is ${state.gamescope.hdr_active ? "on" : "off"}. `
                    }${state.gamescope.blocked}`}
                  />
                </PanelSectionRow>
              )}
              {state.gamescope.modes.length > 0 && (
                <>
                  <PanelSectionRow>
                    <DropdownItem
                      label="Game Mode HDR"
                      description={[
                        state.gamescope.hdr_active === null
                          ? null
                          : `HDR output is ${state.gamescope.hdr_active ? "on" : "off"} right now`,
                        "In HDR this panel ignores brightness. Off or Only for HDR games lets the slider work.",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                      rgOptions={state.gamescope.modes.map((m) => ({
                        data: m,
                        label: HDR_MODES[m] ?? m,
                      }))}
                      selectedOption={state.gamescope.mode}
                      onChange={async (o) => setState(await setGamescopeHdr(o.data))}
                    />
                  </PanelSectionRow>
                  {state.gamescope.pending && (
                    <>
                      <PanelSectionRow>
                        <Field
                          label="Keep this setting?"
                          description={`Reverting in ${state.gamescope.pending.seconds} s unless you keep it.`}
                        />
                      </PanelSectionRow>
                      <PanelSectionRow>
                        <ButtonItem layout="below" onClick={async () => setState(await confirmGamescopeHdr(true))}>
                          Keep
                        </ButtonItem>
                      </PanelSectionRow>
                      <PanelSectionRow>
                        <ButtonItem layout="below" onClick={async () => setState(await confirmGamescopeHdr(false))}>
                          Revert now
                        </ButtonItem>
                      </PanelSectionRow>
                    </>
                  )}
                  <ErrorRow msg={state.errors.gamescope} />
                </>
              )}
              <ErrorRow msg={state.errors.display} />
            </PanelSection>
          )}

          <DriveRows />

          {state.ec.supported && (
            <PanelSection title="Advanced">
              <PanelSectionRow>
                <ToggleField
                  label="Allow raw EC writes"
                  description={
                    state.ec.lockdown && state.ec.lockdown !== "none"
                      ? `Blocked: kernel lockdown is '${state.ec.lockdown}' (Secure Boot).`
                      : "Fallback for fan and charging when no kernel driver is loaded. Registers are checked before and after every write; anything unexpected turns EC control off."
                  }
                  checked={draft.ec.enabled}
                  onChange={(v) => change("ec", { enabled: v }, 0)}
                />
              </PanelSectionRow>
            </PanelSection>
          )}

          <PanelSection title="Controller">
            <PanelSectionRow>
              <Field
                label="Remapping is coming"
                description="Back buttons as paddles and the touchpads need a virtual controller; that's next. To help, record what the buttons and touchpads send: start recording, close this menu, then press each button a few times and swipe and click each touchpad. Recording only reads."
              />
            </PanelSectionRow>
            {state.buttons.available && (
              <PanelSectionRow>
                <ToggleField
                  label="Extra buttons"
                  description={`${state.buttons.help ?? ""} · ${
                    state.buttons.listening ? "active" : draft.controller.buttons ? "looking for the buttons…" : "off"
                  }`}
                  checked={draft.controller.buttons}
                  onChange={(v) => change("controller", { buttons: v }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.chord && (
              <PanelSectionRow>
                <DropdownItem
                  label="Controller shortcut for HHC"
                  description={`Opens Handheld Control from any Xbox-style controller (Elite, 8BitDo…). Press both together, quickly: holding Xbox + B makes Steam close the game.${
                    state.chord.value !== "off"
                      ? ` · ${state.chord.pads ? `${state.chord.pads} controller${state.chord.pads > 1 ? "s" : ""} found` : "no controller found yet"}`
                      : ""
                  }`}
                  rgOptions={[
                    { data: "guide_b", label: "Xbox + B" },
                    { data: "select_start", label: "View + Menu" },
                    { data: "off", label: "Off" },
                  ]}
                  selectedOption={state.chord.value}
                  onChange={(o) => change("controller", { chord: o.data }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.desktop_mouse && (
              <PanelSectionRow>
                <ToggleField
                  label="Controller as mouse and keys in Desktop Mode"
                  description={`Like hhd's desktop mode: outside Game Mode the right stick moves the pointer, the left stick scrolls, the right trigger is a left click, the left trigger a right click; D-pad = arrow keys, A = Enter, B = Esc, X = Backspace, Y = Space, RB = Tab, LB = Shift+Tab. The left stick click (L3) opens the on-screen keyboard: Steam's when Steam runs, which you steer with the controller, so HHC stops the pointer and keys until you close it with B (or press L3 again). Without Steam it's Plasma's keyboard, which you tap, so the pointer keeps working; L3 again hides it (also one opened by swiping). Pauses while a Steam game runs, and in Game Mode the controller works as usual. Apps that use a gamepad still get it too. If Steam's own desktop layout also moves the pointer, turn one of them off. ${
                    state.desktop_mouse.error
                      ? `Not working: ${state.desktop_mouse.error}.`
                      : !state.desktop_mouse.on
                        ? ""
                        : state.desktop_mouse.game_mode
                          ? "Now: in Game Mode, so waiting for Desktop Mode."
                          : state.desktop_mouse.game
                            ? "Now: paused, a game is running."
                            : state.desktop_mouse.keyboard === "steam"
                              ? "Now: Steam's keyboard is up, the controller is steering it."
                              : state.desktop_mouse.pads
                              ? "Now: active."
                              : "Now: no controller found yet."
                  }`}
                  checked={state.desktop_mouse.on}
                  onChange={(v) => change("controller", { desktop_mouse: v }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.swipe?.available && (
              <>
                <PanelSectionRow>
                  <ToggleField
                    label="Swipe up for the keyboard"
                    description={
                      draft.controller.swipe_keyboard && state.swipe.learning
                        ? "Setting up: swipe up once from the bottom edge of the screen"
                        : "Swipe up from the bottom edge of the screen to open the Steam keyboard"
                    }
                    checked={!!draft.controller.swipe_keyboard}
                    onChange={(v) => change("controller", { swipe_keyboard: v }, 0)}
                  />
                </PanelSectionRow>
                {draft.controller.swipe_keyboard && !state.swipe.learning && (
                  <PanelSectionRow>
                    <ButtonItem layout="below" onClick={() => change("controller", { swipe_edge: null }, 0)}>
                      Set up the bottom edge again
                    </ButtonItem>
                  </PanelSectionRow>
                )}
              </>
            )}
            {state.sleep.pad_wake_available && (
              <PanelSectionRow>
                <ToggleField
                  label="Wake with the controller"
                  description={[
                    state.sleep.pad_wake_text ??
                      "Any button or a stick move wakes it from sleep (if the controller can). Careful in a bag: a stick bump wakes it too.",
                    state.sleep.note,
                    state.sleep.last_wake,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  checked={state.sleep.pad_wake}
                  onChange={(v) => change("sleep", { pad_wake: v }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.sleep.power_key != null && !(window as any).HHC_DESKTOP && (
              <PanelSectionRow>
                <ToggleField
                  label="Power button sleeps in Game Mode"
                  description={`For systems where a short power press does nothing in Game Mode: HHC passes the press to Steam the way SteamOS does, so Steam plays its sleep animation, turns the screen off and sleeps (like the NEXT 2 and OneXFly). If Steam hasn't slept within 6 s, the system sleeps anyway.${
                    state.sleep.power_key && !state.sleep.power_key_listening?.length ? " No power button found yet." : ""
                  }`}
                  checked={state.sleep.power_key}
                  onChange={(v) => change("sleep", { power_key: v }, 0)}
                />
              </PanelSectionRow>
            )}
            <ErrorRow msg={state.errors.sleep} />
            {state.inputplumber?.configurable && state.inputplumber.status === "missing" && (
              <PanelSectionRow>
                <Field
                  label="Xbox Elite controller"
                  description={`Needs InputPlumber, which shows the controller as an Xbox Elite with the back buttons as paddles. Install it${
                    state.inputplumber.install ? ` (${state.inputplumber.install})` : ""
                  }; the plugin sets it up and turns it on.`}
                />
              </PanelSectionRow>
            )}
            {!!state.inputplumber && (state.inputplumber.devices > 0 || (state.inputplumber.configurable && state.inputplumber.status !== "missing")) && (
              <PanelSectionRow>
                <DropdownItem
                  label="Controller type"
                  description={
                    state.inputplumber.failed
                      ? `Didn't work last time (${state.inputplumber.failed}); the plain controller is in use. Pick a type to try again.`
                      : state.inputplumber.configurable
                        ? "Through InputPlumber; the back buttons become paddles"
                        : "What InputPlumber shows Steam and games"
                  }
                  rgOptions={[
                    ...state.inputplumber.targets.map((t) => ({ data: t, label: IP_TARGETS[t] ?? t })),
                    { data: "none", label: state.inputplumber.configurable ? "Plain (no InputPlumber)" : "InputPlumber's choice" },
                  ]}
                  selectedOption={state.inputplumber.failed ? "none" : draft.controller.ip_target ?? "none"}
                  onChange={(o) => change("controller", { ip_target: o.data === "none" ? null : o.data }, 0)}
                />
              </PanelSectionRow>
            )}
            {state.inputplumber?.configurable && state.inputplumber.status !== "missing" && (
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={async () => setState(await saveIpReport())}>
                  Save InputPlumber report
                </ButtonItem>
              </PanelSectionRow>
            )}
            <ErrorRow msg={state.errors.controller} />
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                disabled={state.input_capture.running}
                onClick={async () => setState(await inputCapture())}
              >
                {state.input_capture.running
                  ? `Recording… ${state.input_capture.left} s left`
                  : "Record controller input (30 s)"}
              </ButtonItem>
            </PanelSectionRow>
            {state.input_capture.summary && (
              <PanelSectionRow>
                <Field
                  label="Result"
                  description={[
                    state.input_capture.summary,
                    state.input_capture.report ? `Report: ${state.input_capture.report}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                />
              </PanelSectionRow>
            )}
          </PanelSection>

          <PanelSection>
            <PanelSectionRow>
              <DropdownItem
                label="Theme color"
                rgOptions={Object.entries(THEMES).map(([data, t]) => ({
                  data,
                  label: t.label,
                }))}
                selectedOption={theme}
                description={THEMES[theme]?.font ? `Brings the ${FONTS[THEMES[theme].font!]?.label} font; change it below` : undefined}
                onChange={(o) => change("ui", { theme: o.data, font: THEMES[o.data]?.font ?? draft.ui.font }, 0)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Font"
                description={FONTS[draft.ui?.font ?? "default"]?.about}
                rgOptions={Object.entries(FONTS).map(([data, f]) => ({
                  data,
                  label: f.label,
                }))}
                selectedOption={draft.ui?.font ?? "default"}
                onChange={(o) => change("ui", { font: o.data }, 0)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Glass buttons"
                description="Buttons and dropdowns show what's behind them like through a magnifying glass: sharp, the middle a little bigger, the rim bent like a drop's. Best with a background picture. Off: plain tinted buttons"
                checked={draft.ui?.glass ?? true}
                onChange={(v) => change("ui", { glass: v }, 0)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                description="PNG, JPG or WebP, up to 40 MB. Pictures over 8 MB are shrunk to fit the screen first; the picture is copied into the plugin's settings, so the original can be moved or deleted."
                onClick={() => setView("pick")}
              >
                {draft.ui?.bg ? "Change background picture" : "Choose background picture"}
              </ButtonItem>
            </PanelSectionRow>
            {draft.ui?.bg && (
              <>
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={() => setView("adjust")}>
                    Adjust picture (zoom, position)
                  </ButtonItem>
                </PanelSectionRow>
                <PanelSectionRow>
                  <SliderField
                    label="Picture dimming"
                    description="Darkens the picture so text stays readable"
                    value={draft.ui.bg_dim ?? 55}
                    min={0}
                    max={90}
                    step={5}
                    showValue
                    valueSuffix="%"
                    onChange={(v) => change("ui", { bg_dim: v })}
                  />
                </PanelSectionRow>
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={async () => setState(await clearBackground())}>
                    Remove background picture
                  </ButtonItem>
                </PanelSectionRow>
              </>
            )}
            <ErrorRow msg={state.errors.ui} />
            {state.device.notes
              // HHC Desktop runs on PCs too: the handheld remark means nothing there.
              .filter((n) => !((window as any).HHC_DESKTOP && n.startsWith("Not one of the supported handhelds")))
              .map((n) => (
              <PanelSectionRow key={n}>
                <Field description={n} />
              </PanelSectionRow>
            ))}
            {state.conflicts.length > 0 && (
              <PanelSectionRow>
                <Field
                  description={state.conflicts
                    .map(
                      (c) =>
                        ({
                          hhd: "Handheld Daemon (HHD) is running: if its TDP, fan, RGB or charging controls are on, they override these settings",
                          powerstation:
                            "PowerStation is running: if you also use Steam's TDP or GPU clock limit (Quick Access › Performance), it overrides the plugin's TDP and GPU clock",
                        })[c] ?? `${c} is running and may override some settings`,
                    )
                    .join(" · ")}
                />
              </PanelSectionRow>
            )}
            {(() => {
              const upd = state.update;
              const installed = state.version ?? "0";
              const sel = upd.releases.find((r) => r.tag === pickTag) ?? upd.releases[0];
              const cmp = sel ? versionCmp(sel.version, installed) : 0;
              return (
                <>
                  <PanelSectionRow>
                    <ButtonItem
                      layout="below"
                      disabled={upd.busy}
                      description={[`Installed: v${installed}`, upd.message, upd.error].filter(Boolean).join(" · ")}
                      onClick={async () => setState(await checkUpdates())}
                    >
                      {upd.releases.length ? "Check again" : "Check for updates"}
                    </ButtonItem>
                  </PanelSectionRow>
                  {sel && (
                    <>
                      <PanelSectionRow>
                        <DropdownItem
                          label="Version"
                          description={[sel.date, firstLine(sel.notes)].filter(Boolean).join(" · ")}
                          rgOptions={upd.releases.map((r, i) => ({
                            data: r.tag,
                            label: `v${r.version}${r.version === installed ? " (installed)" : i === 0 ? " (newest)" : ""}`,
                          }))}
                          selectedOption={sel.tag}
                          onChange={(o) => setPickTag(o.data)}
                        />
                      </PanelSectionRow>
                      <PanelSectionRow>
                        <ButtonItem
                          layout="below"
                          disabled={upd.busy}
                          description={[
                            "Decky restarts to load it; your settings stay.",
                            versionCmp(sel.version, FIRST_WITH_UPDATER) < 0
                              ? `v${sel.version} has no updater: to come back, install from the release URL.`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" ")}
                          onClick={async () => setState(await installUpdate(sel.tag))}
                        >
                          {cmp > 0 ? `Update to v${sel.version}` : cmp < 0 ? `Go back to v${sel.version}` : `Reinstall v${sel.version}`}
                        </ButtonItem>
                      </PanelSectionRow>
                    </>
                  )}
                </>
              );
            })()}
          </PanelSection>
        </div>
      </div>
    </div>
  );
}

// Decky keeps the title bar fixed above the panel; the version sits next to the name.
function TitleView() {
  const [version, setVersion] = useState<string | null>(null);
  const [color, setColor] = useState(THEMES[themeNow]?.title);
  useEffect(() => {
    const on = () => setColor(THEMES[themeNow]?.title);
    themeListeners.add(on);
    return () => {
      themeListeners.delete(on);
    };
  }, []);
  useEffect(() => {
    getVersion().then(setVersion);
  }, []);
  return (
    <div
      className={staticClasses.Title}
      style={{
        display: "flex",
        flexDirection: "row",
        alignItems: "baseline",
        gap: "6px",
        whiteSpace: "nowrap",
        minWidth: 0,
        color,
      }}
    >
      <span
        style={{
          fontSize: "0.8em",
          overflow: "hidden",
          textOverflow: "ellipsis",
          minWidth: 0,
        }}
      >
        {(window as any).HHC_DESKTOP ? "HHC Desktop" : "HHC"}
      </span>
      {version && <span style={{ fontSize: "0.55em", opacity: 0.6, flexShrink: 0 }}>v{version}</span>}
    </div>
  );
}

// Extra buttons (backend listener): runs while the plugin is loaded, panel open or not.
const QAM_DECKY_TAB = 999; // EQuickAccessTab.Decky
let panelShown = false; // Content is mounted: our page is the one in the Decky tab

// Steam's navigation nodes for the panel's elements, in the tree's order,
// and the one Steam's focus is on (its tree's last focused node), if any.
const navNodes = (root: Element): { nodes: any[]; focused: any } => {
  const out: any[] = [];
  let focused: any = null;
  const walk = (n: any) => {
    for (const c of n?.m_rgChildren ?? []) {
      if (c?.m_element && (root.contains(c.m_element) || c.m_element.closest?.(".hhctl-root"))) out.push(c);
      walk(c);
    }
  };
  const seen = new Set<any>();
  const tree = (t: any) => {
    if (!t || seen.has(t)) return;
    seen.add(t);
    if (t.m_Root?.m_element?.ownerDocument === root.ownerDocument) {
      walk(t.m_Root);
      const f = t.m_lastFocusNode;
      if (f?.m_element?.closest?.(".hhctl-root") && f.m_Focused?.Value !== false) focused = f;
    }
    (t.m_rgChildNavTrees ?? []).forEach(tree);
  };
  try {
    // Quick Access is a window of its own with its own navigation context, so
    // every context's trees, not just the active one's.
    const ctl = getFocusNavController();
    for (const c of ctl?.m_rgAllContexts ?? []) (c?.m_rgGamepadNavigationTrees ?? []).forEach(tree);
    (getGamepadNavigationTrees() ?? []).forEach(tree);
  } catch {
    // Steam changed its navigation internals: no highlight, nothing else breaks
  }
  return { nodes: out, focused };
};

// Same check as useQuickAccessVisible, outside React.
const quickAccessOpen = () => {
  try {
    const w = getGamepadNavigationTrees()?.find((t: any) => t?.id === "QuickAccess-NA")?.m_Root?.m_element
      ?.ownerDocument?.defaultView;
    return w ? !w.document.hidden : false;
  } catch {
    return false;
  }
};

// Steam's menu store: which Quick Access tab is showing, and whether it's open.
const QAM_PERF_TAB = 5; // EQuickAccessTab.Perf
const menuStore = () => (window as any).SteamUIStore?.WindowStore?.GamepadUIMainWindowInstance?.MenuStore;
const qamVisible = () => menuStore()?.IsSideMenuVisible?.(2) ?? quickAccessOpen(); // ESideMenu.QuickAccess
// Holding opens Quick Access on Performance, or on the last tab used there
// (not Decky's, which tapping opens).
let holdTab = QAM_PERF_TAB;
const rememberTab = () => {
  const tab = menuStore()?.GetQuickAccessTab?.();
  if (qamVisible() && typeof tab === "number" && tab !== QAM_DECKY_TAB) holdTab = tab;
};

const onButton = (action: string) => {
  rememberTab();
  // Pressing the button again closes what it opened.
  if ((action === "open_plugin" && panelShown && qamVisible()) || (action === "qam" && qamVisible())) {
    Navigation.CloseSideMenus();
    return;
  }
  if (action === "open_plugin") {
    Navigation.OpenQuickAccessMenu(QAM_DECKY_TAB as never);
    // Decky has no public call to pick a plugin in its tab; try its internal state.
    try {
      (window as any).DeckyPluginLoader?.deckyState?.setActivePlugin?.("Handheld Control");
    } catch {
      // the Decky tab opens on its plugin list instead
    }
  } else if (action === "qam") {
    Navigation.OpenQuickAccessMenu(holdTab as never);
  } else if (action === "keyboard") {
    openKeyboard();
  } else if (action === "power_short") {
    // The power button in Game Mode: what SteamOS's power button service
    // sends, so Steam plays its animation, turns the screen off and sleeps.
    const sc = (window as any).SteamClient;
    if (qamVisible()) Navigation.CloseSideMenus();
    try {
      sc.URL.ExecuteSteamURL("steam://shortpowerpress");
    } catch (e) {
      console.warn("hhctl: power press", e);
    }
  } else if (action === "sleep") {
    // The power button in Game Mode, when nothing else passed it to Steam.
    const sc = (window as any).SteamClient;
    if (qamVisible()) Navigation.CloseSideMenus();
    try {
      if (typeof sc?.System?.SuspendPC === "function") sc.System.SuspendPC();
      else sc.URL.ExecuteSteamURL("steam://shortpowerpress");
    } catch (e) {
      console.warn("hhctl: sleep", e);
    }
  }
};

// Steam's keyboard: Quick Access closed first (the keyboard doesn't show over
// it), then Steam's keyboard manager if it has one, else steam://open/keyboard.
function openKeyboard() {
  if (qamVisible()) Navigation.CloseSideMenus();
  setTimeout(() => {
    const win = (window as any).SteamUIStore?.WindowStore?.GamepadUIMainWindowInstance;
    const vkm = win?.VirtualKeyboardManager ?? win?.m_VirtualKeyboardManager;
    for (const fn of ["SetVirtualKeyboardVisible", "ShowVirtualKeyboard", "SetVirtualKeyboardShown"]) {
      try {
        if (typeof vkm?.[fn] === "function") {
          vkm[fn](true);
          console.info("hhctl: keyboard via", fn);
          return;
        }
      } catch (e) {
        console.warn("hhctl: keyboard", fn, e);
      }
    }
    try {
      (window.SteamClient as any).URL.ExecuteSteamURL("steam://open/keyboard");
    } catch (e) {
      console.warn("hhctl: keyboard", e);
    }
  }, 250);
}

// --- What's using the CPU and GPU (topuse.py) ---------------------------------

function TopUseRows({ state, setState }: { state: State; setState: (s: State) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const t = state.topuse;
  const list = (rows: [string, number][]) => (rows.length ? rows.map(([n, p]) => `${n} ${p}%`).join(", ") : "nothing busy");
  const measure = async (delay: number) => {
    setBusy(delay ? "Close this menu now: measuring in 5 s…" : "Measuring for 2 s…");
    try {
      setState(await topUse(delay));
    } finally {
      setBusy(null);
    }
  };
  return (
    <PanelSection title="What's using it">
      <PanelSectionRow>
        <Field
          description={
            t
              ? `Last look at ${t.at}. CPU (share of all cores): ${list(t.cpu)}. GPU (busiest engine): ${list(t.gpu)}.`
              : "Measures every process for 2 s and lists the busiest ones on the CPU and the GPU, for when the device works harder than it should. Only runs when you press a button below."
          }
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!!busy}
          description="Measures right away. Steam keeps redrawing while this menu is open, so steamwebhelper and gamescope show up higher than they would otherwise."
          onClick={() => measure(0)}
        >
          {busy ?? "Measure now"}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={!!busy}
          description="Waits 5 s so you can close this menu (and leave the screen as it is) before it measures. Open the menu again afterwards to read the result."
          onClick={() => measure(5)}
        >
          {busy ? "…" : "Measure with the menu closed"}
        </ButtonItem>
      </PanelSectionRow>
      {state.overlay?.game_mode && (
        <PanelSectionRow>
          <ToggleField
            label="Stop the performance overlay outside games"
            description={[
              "Steam hides its performance overlay when a game closes but leaves the program that draws it (mangoapp) running, so the GPU never rests on the Steam screen. With this on, HHC ends it once no game has run for 10 s; Steam starts it again when a game shows the overlay.",
              state.overlay.idle_since ? `Seen running with no game since ${state.overlay.idle_since}.` : null,
              state.overlay.note,
            ]
              .filter(Boolean)
              .join(" ")}
            checked={!!state.settings.overlay?.stop_idle}
            onChange={async (v) => setState(await setters.overlay({ stop_idle: v }))}
          />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

// --- System toggles (systweak.py) ---------------------------------------------

function SystemRows({ state, setState }: { state: State; setState: (s: State) => void }) {
  const st = state.system;
  const cur = state.settings.system;
  if (!st || !cur || Object.keys(st).length === 0) return null;
  const set = async (v: Partial<Settings["system"]>) => setState(await setters.system(v));
  const now = (v: unknown) => ` · now ${v}`;
  return (
    <PanelSection title="System">
      {st.thp && (
        <PanelSectionRow>
          <DropdownItem
            label="Transparent hugepages"
            description={`madvise (SteamOS): huge pages only for programs that ask. always can stall a game while memory is tidied${now(st.thp.current)}`}
            rgOptions={[{ data: null, label: "System's choice" }, ...st.thp.choices.map((c) => ({ data: c, label: c }))]}
            selectedOption={cur.thp}
            onChange={(o) => set({ thp: o.data })}
          />
        </PanelSectionRow>
      )}
      {st.swappiness && (
        <PanelSectionRow>
          <DropdownItem
            label="Swappiness"
            description={`How readily memory goes to swap. ${
              st.swappiness.zram
                ? "This system swaps to zram (compressed RAM), where high values (100-180) are right"
                : "Swap on a disk: low values (10) keep games in RAM"
            }${now(st.swappiness.current)}`}
            rgOptions={[
              { data: null, label: "System's choice" },
              ...st.swappiness.choices.map((c) => ({ data: c, label: String(c) })),
            ]}
            selectedOption={cur.swappiness}
            onChange={(o) => set({ swappiness: o.data })}
          />
        </PanelSectionRow>
      )}
      {st.usb_awake && (
        <PanelSectionRow>
          <ToggleField
            label="Keep USB devices awake"
            description={`No USB autosuspend, so controllers and dongles never doze off (a little more power). USB drives and card readers are left alone${now(
              st.usb_awake.current === -1 ? "never suspended" : `suspended after ${st.usb_awake.current} s idle`,
            )}`}
            checked={cur.usb_awake}
            onChange={(v) => set({ usb_awake: v })}
          />
        </PanelSectionRow>
      )}
      {st.wifi_ps_off && (
        <PanelSectionRow>
          <ToggleField
            label="Wi-Fi power saving off"
            description={`Steadier game streaming and online play, a little more power${now(
              st.wifi_ps_off.current ? "on" : "off",
            )}`}
            checked={cur.wifi_ps_off}
            onChange={(v) => set({ wifi_ps_off: v })}
          />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <Field description="Changed only while set here: picking System's choice, unloading or a restart puts the old values back. Nothing here touches drives or SD cards." />
      </PanelSectionRow>
      <ErrorRow msg={state.errors.system} />
    </PanelSection>
  );
}

// --- Per-game profiles: tell the backend which game runs ----------------------

let sentGame: string | null | undefined;
function watchGame() {
  if ((window as any).HHC_DESKTOP) return; // there the backend finds the game itself
  const g = runningGame();
  const id = g ? String(g.appid) : null;
  if (id === sentGame) return;
  sentGame = id;
  setGame(id, g?.name ?? null).catch(() => (sentGame = undefined));
}

function GameProfileRows({ state, setState }: { state: State; setState: (s: State) => void }) {
  const gs = state.games;
  if (!gs) return null;
  const saved = gs.count ? ` Saved so far: ${gs.count} game${gs.count === 1 ? "" : "s"}.` : "";
  // Why a switch can't be used right now (it stays visible, grayed out).
  const why = !gs.steam
    ? "Needs Steam: profiles follow the games Steam starts, and Steam isn't installed here."
    : !gs.enabled
      ? "Game profiles are switched off below."
      : !gs.game
        ? `No game running: start a game from Steam, then come back here.${saved}`
        : null;
  const name = gs.game?.name ?? "the running game";
  return (
    <PanelSection title="Game profile">
      <PanelSectionRow>
        <div className={why ? "hhctl-off" : undefined}>
          <ToggleField
            label={gs.game ? `Profile for ${name}` : "Profile for this game"}
            description={
              why ??
              (gs.profile
                ? `On: the TDP, CPU, GPU and fan settings in this panel belong to ${name}. Whatever you change while it runs is kept for it, and your usual settings come back when it closes. Off deletes the profile and brings your usual settings back now.`
                : `Saves the TDP, CPU (boost, SMT, cores, clock and temperature limits, scheduler), GPU and fan settings you have now for ${name}. They're applied every time it starts and your usual settings come back when it closes. Lights, battery and controller settings stay the same for every game.`)
            }
            checked={gs.profile}
            disabled={!!why}
            onChange={async (v) => setState(await saveGameProfile(v))}
          />
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <div className={why || !gs.auto_available ? "hhctl-off" : undefined}>
          <ToggleField
            label="Auto-tune"
            description={
              why ??
              (!gs.auto_available
                ? "Needs a CPU clock limit, CPU boost or TDP control, and this device offers none of them."
                : gs.auto
                  ? `On: every 20 s it looks at what holds ${name} back and moves one setting a step, so no power goes where the game doesn't need it. Light load (frame cap, menus, simple games): CPU clock limit down 200 MHz at a time to half the top clock, then CPU boost off, then EPP to balance_power, then the TDP down 1 W at a time to 60% of where it started. GPU busy: a lowered TDP goes back up first, then the CPU gives way so the shared power goes to the GPU. A CPU core maxed out: everything goes back in reverse. Cores and SMT are never touched. What it settles on is kept in the profile, and your usual settings come back when the game closes. Changing the CPU clock limit, CPU boost, EPP or TDP by hand turns this off, so your own tuning stays. ${
                      gs.auto_status ? `Last decision: ${gs.auto_status}.` : "Watching the first 20 s…"
                    }`
                  : `Lets HHC share the power between CPU and GPU for ${name} by itself while it plays, with the CPU clock limit, CPU boost, EPP and TDP, so it doesn't waste power the game doesn't need. Turning it on also saves a profile for the game if it has none.`)
            }
            checked={gs.auto}
            disabled={!!why || !gs.auto_available}
            onChange={async (v) => setState(await setAutotune(v))}
          />
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <div className={gs.steam ? undefined : "hhctl-off"}>
          <ToggleField
            label="Use game profiles"
            description={
              gs.enabled
                ? `On: a game with a profile gets its settings when it starts${
                    gs.desktop ? " (HHC Desktop notices it within 5 s)" : ""
                  }. Off keeps your usual settings for every game, without deleting the profiles.`
                : "Off: every game runs with your usual settings; saved profiles are kept for when this is on again."
            }
            checked={gs.enabled}
            disabled={!gs.steam}
            onChange={async (v) => setState(await setters.games({ enabled: v }))}
          />
        </div>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => {
  addEventListener<[string]>("hhctl_button", onButton);
  // Runs while Steam does, panel open or not, so profiles switch by themselves.
  const gameWatch = setInterval(watchGame, 2000);
  // Closing with B isn't seen by the plugin, so note the tab while it's open.
  const tabWatch = setInterval(rememberTab, 1000);
  return {
    name: "Handheld Control",
    titleView: <TitleView />,
    content: <Content />,
    icon: <FaMicrochip />,
    onDismount: () => {
      removeEventListener("hhctl_button", onButton);
      clearInterval(tabWatch);
      clearInterval(gameWatch);
    },
  };
});
