import { callable, definePlugin, openFilePicker, toaster, useQuickAccessVisible } from "@decky/api";
import type { FileSelectionType } from "@decky/api/dist/types";
import {
  ButtonItem,
  DropdownItem,
  Field,
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
  };
  gpu: { mode: GpuMode; mhz: number | null };
  fan: { mode: "auto" | "fixed" | "curve"; percent: number; curve: string };
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
  ui: { theme: string; font: string; bg: string | null; bg_dim: number };
  sdr_brightness: { enabled: boolean; nits: number };
};

type GpuMode = "auto" | "min" | "max" | "manual";

type State = {
  device: { name: string; board: string; vendor: string; notes: string[] };
  conflicts: string[];
  settings: Settings;
  errors: Record<string, string>;
  tdp: { backend: string | null; min: number; max: number; default: number; current: number | null; profile: string | null };
  fan: {
    backend: string | null;
    unavailable: string | null;
    curves: string[];
    rpm: number | null;
    percent: number | null;
    temp: number | null;
    safety: boolean;
    watchdog: boolean;
  };
  battery: {
    limit_mode: string | null;
    bypass_options: string[];
    unavailable: string | null;
    capacity?: number | null;
    status?: string | null;
    power_w?: number | null;
    inhibited?: boolean;
    ac?: boolean;
  };
  rgb: { backend: string | null; modes: string[]; effects: string[] };
  ec: { supported: boolean; lockdown: string | null };
  version: string | null;
  home: string | null;
  perf: {
    cpu_mhz: number | null;
    cpu_peak_mhz: number | null;
    cpu_temp_c: number | null;
    ram_used_mb: number | null;
    ram_total_mb: number | null;
    tdp_w: number | null;
    limits: { cpu: string[]; gpu: string[] };
  };
  input_capture: { running: boolean; left: number; summary: string | null; report: string | null };
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
  cpu: Record<"boost" | "prefcore" | "ccx", { available: boolean; unavailable: string | null; on: boolean | null }> & {
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
    vram_mb: number | null;
    vram_total_mb: number | null;
    gtt_mb: number | null;
    mem_mb: number | null;
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
const setDisplay = callable<[string, boolean], State>("set_display");
const setGamescopeHdr = callable<[string], State>("set_gamescope_hdr");
const confirmSdrBrightness = callable<[boolean], State>("confirm_sdr_brightness");
const setBackground = callable<[string], State>("set_background");
const clearBackground = callable<[], State>("clear_background");
const getBackground = callable<[], string | null>("get_background");
const getFont = callable<[string], string | null>("get_font");
const confirmGamescopeHdr = callable<[boolean], State>("confirm_gamescope_hdr");

const HDR_MODES: Record<string, string> = {
  steam: "Steam default",
  content: "Only for HDR games",
  off: "Off",
};
const brightnessTest = callable<[], State>("brightness_test");
const inputCapture = callable<[], State>("input_capture");
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
  .hhctl-readout {
    display: grid; grid-template-columns: auto auto auto auto minmax(0, 1fr); gap: 0 8px;
    font-size: 12px; line-height: 15px; color: #dcdedf; padding-top: 2px; white-space: nowrap;
  }
  .hhctl-readout > span { overflow: hidden; text-overflow: ellipsis; }
  .hhctl-readout > span:nth-child(-n + 5) { color: #8b929a; font-size: 11px; }
  .hhctl-readout .hhctl-capped {
    grid-column: 2 / -1; color: #ffb35c; font-style: normal; font-size: 11px;
    overflow: hidden; text-overflow: ellipsis;
  }
  .hhctl-readout .hhctl-capped.hhctl-free { color: #8b929a; }
  .hhctl-headlabel { font-weight: bold; }
  .hhctl-inline { white-space: nowrap; }
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
const THEMES: Record<string, { label: string; bg?: string; title?: string; filter?: string }> = {
  default: { label: "Default (black)" },
  red: {
    label: "Red",
    bg: "linear-gradient(180deg, rgba(170, 25, 35, 0.40) 0%, rgba(70, 10, 15, 0.25) 100%)",
    title: "#ff7a7a",
    filter: "hue-rotate(150deg) saturate(1.6) brightness(0.94)",
  },
  blue: {
    label: "Blue",
    bg: "linear-gradient(180deg, rgba(25, 80, 190, 0.40) 0%, rgba(10, 30, 80, 0.25) 100%)",
    title: "#7ab4ff",
  },
  green: {
    label: "Green",
    bg: "linear-gradient(180deg, rgba(25, 140, 60, 0.40) 0%, rgba(10, 60, 25, 0.25) 100%)",
    title: "#7ee08a",
    filter: "hue-rotate(-84deg) saturate(0.7) brightness(1.06)",
  },
  gold: {
    label: "Gold",
    bg: "linear-gradient(180deg, rgba(185, 140, 20, 0.40) 0%, rgba(80, 60, 10, 0.25) 100%)",
    title: "#ffd166",
    filter: "hue-rotate(-156deg) saturate(0.8) brightness(1.24)",
  },
};

// Light color for RGB "Match theme color"; mirrors THEME_RGB in controller.py.
const THEME_RGB: Record<string, [number, number]> = { red: [0, 100], blue: [215, 100], green: [130, 100], gold: [42, 100] };

// Free (OFL) fonts in the style of each game; the originals can't be bundled.
const FONTS: Record<string, { label: string; family?: string; fallback?: string; about?: string }> = {
  default: { label: "Default" },
  diablo: { label: "Diablo", family: "HHCinzel", fallback: "serif", about: "Cinzel, in the style of Diablo's lettering" },
  matrix: { label: "Matrix", family: "HHShareTechMono", fallback: "monospace", about: "Share Tech Mono, a terminal-style font" },
  doom: { label: "Doom", family: "HHMetalMania", fallback: "serif", about: "Metal Mania, a heavy-metal logo style" },
  quake: { label: "Quake", family: "HHGrenzeGotisch", fallback: "serif", about: "Grenze Gotisch, a gothic style" },
};

const fontCss = (name: string, faces: string | null) => {
  const font = FONTS[name];
  if (!font?.family || !faces) return "";
  return `${faces}
    .hhctl-root, .hhctl-root * { font-family: '${font.family}', ${font.fallback ?? "serif"} !important; }`;
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
  const controls = [
    `.hhctl-root .${dlg.Toggle}`,
    `.hhctl-root [class*="gamepaddialog_Toggle_"]`,
    `.hhctl-root .${sl.SliderControl}`,
    `.hhctl-root [class*="gamepadslider_SliderControl_"]`,
  ].join(", ");
  return `
    ${hasImage ? "" : `.hhctl-root { background: ${t.bg}; border-radius: 8px; padding-bottom: 4px; }`}
    .hhctl-root .${staticClasses.PanelSectionTitle}, .hhctl-root .hhctl-headlabel, ${labels} { color: ${t.title}; }
    ${t.filter ? `${controls} { filter: ${t.filter}; }` : ""}
  `;
};

const imageStyle = (url: string, dimPct: number) => {
  const a = Math.max(0, Math.min(90, dimPct)) / 100;
  return {
    backgroundImage: `linear-gradient(rgba(0, 0, 0, ${a}), rgba(0, 0, 0, ${a})), url("${url}")`,
    backgroundSize: "cover",
    backgroundPosition: "center",
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
  const loaded = !!state;
  useEffect(() => {
    const root = rootRef.current;
    let scroller = root?.parentElement ?? null;
    while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)) scroller = scroller.parentElement;
    if (!root || !scroller) return;
    const sc = scroller;
    const fit = () => {
      const top = root.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop;
      const pad = parseFloat(getComputedStyle(sc).paddingBottom) || 0;
      const h = Math.max(240, Math.floor(sc.clientHeight - top - pad));
      // Switch layouts before measuring: unfitted, the settings overflow the panel.
      root.classList.replace("hhctl-sticky", "hhctl-fit");
      root.style.height = `${h}px`;
      // Anything after the panel (Decky's own padding or spacers) still makes
      // the outer area scroll: take that off too, so only the settings scroll.
      const extra = sc.scrollHeight - sc.clientHeight;
      const fitted = extra > 0 ? Math.max(240, h - extra) : h;
      root.style.height = `${fitted}px`;
      sc.scrollTop = 0;
      setFitHeight(fitted);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(sc);
    return () => ro.disconnect();
  }, [loaded]);

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
  const rows: { name: string; clock: string; power: string; temp: string; mem: string; capped: string[] }[] = [
    {
      name: "CPU",
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

  return (
    <div
      ref={rootRef}
      className={`hhctl-root ${fitHeight ? "hhctl-fit" : "hhctl-sticky"}`}
      style={{
        ...(bgUrl ? imageStyle(bgUrl, draft.ui?.bg_dim ?? 55) : {}),
        ...(fitHeight ? { height: fitHeight } : {}),
      }}
    >
      <style>{READOUT_CSS}</style>
      <style>{themeCss(theme, !!bgUrl)}</style>
      <style>{fontCss(fontName, fontFaces)}</style>
      <div className="hhctl-header">
        <div className="hhctl-top">
          <span className="hhctl-device" style={{ color: THEMES[theme]?.title ?? "#ffffff" }}>
            {state.device.name}
          </span>
          {gpu.pkg_w != null && (
            <span className="hhctl-tdp">
              TDP {gpu.pkg_w}
              {perf.tdp_w ? ` / ${perf.tdp_w}` : ""} W
            </span>
          )}
        </div>
        {showReadout && (
          <div className="hhctl-readout">
            <span />
            <span>Clock</span>
            <span>Power</span>
            <span>Temp</span>
            <span>Memory</span>
            {rows.map((r) => (
              <Fragment key={r.name}>
                <b>{r.name}</b>
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
        className="hhctl-body"
        // D-pad focus: keep the focused setting inside the scroll area.
        onFocus={(ev) => fitHeight && (ev.target as HTMLElement).scrollIntoView?.({ block: "nearest" })}
      >
        <PanelSection>
          {!tdp.backend ? (
            <Unavailable why="No TDP interface found (firmware-attributes, RAPL or ryzenadj)." />
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
                    rgOptions={state.epp.options.map((o) => ({ data: o, label: EPP_LABELS[o] ?? titleCase(o) }))}
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
                    change("cpu", { cap: v, cap_mhz: draft.cpu.cap_mhz ?? state.cpu.cap.max }, 0)
                  }
                />
              </PanelSectionRow>
              {draft.cpu.cap && (
                <PanelSectionRow>
                  <SliderField
                    label="Max CPU clock"
                    description={
                      state.cpu.cap.limits.length > 1
                        ? `Each core type stays within its own range · now ${state.cpu.cap.limits.map(ghz).join(" / ")}`
                        : undefined
                    }
                    value={draft.cpu.cap_mhz ?? state.cpu.cap.max}
                    min={state.cpu.cap.min}
                    max={state.cpu.cap.max}
                    step={100}
                    showValue
                    valueSuffix=" MHz"
                    onChange={(v) => change("cpu", { cap_mhz: v })}
                  />
                </PanelSectionRow>
              )}
            </>
          )}
          <ErrorRow msg={state.errors.cpu} />
        </PanelSection>

        {gpu.clock.available && (
          <PanelSection>
            {gpu.clock.modes.includes("manual") && (
              <PanelSectionRow>
                <SliderField
                  label="GPU clock"
                  description={g.mode === "manual" ? undefined : "Moving it locks the clock (Mode: Manual)"}
                  value={g.mhz ?? gpu.clock.max}
                  min={gpu.clock.min}
                  max={gpu.clock.max}
                  step={50}
                  showValue
                  valueSuffix=" MHz"
                  onChange={(v) => change("gpu", { mode: "manual", mhz: v })}
                />
              </PanelSectionRow>
            )}
            <PanelSectionRow>
              <DropdownItem
                label="GPU clock mode"
                description={`${GPU_MODES[g.mode]?.description ?? ""} · ${gpu.clock.min}–${gpu.clock.max} MHz${
                  gpu.freq_mhz != null ? ` · now ${gpu.freq_mhz} MHz` : ""
                }`}
                rgOptions={gpu.clock.modes.map((m) => ({ data: m, label: GPU_MODES[m].label }))}
                selectedOption={g.mode}
                onChange={(o) => change("gpu", { mode: o.data, mhz: g.mhz ?? gpu.clock.max }, 0)}
              />
            </PanelSectionRow>
            {gpu.clock.throttle.length > 0 && (
              <PanelSectionRow>
                <Field
                  label="Held back by"
                  description={`${reasons(gpu.clock.throttle)}. Raise the TDP or limit the CPU clock to give the GPU more power.`}
                />
              </PanelSectionRow>
            )}
            <ErrorRow msg={state.errors.gpu} />
          </PanelSection>
        )}

        <PanelSection>
          {!fan.backend ? (
            <Unavailable why={`Fan: ${fan.unavailable ?? "no fan control"}`} />
          ) : (
            <>
              <PanelSectionRow>
                <Field label={<span className="hhctl-headlabel">Fan status</span>}>
                  <span className="hhctl-inline">
                    {[
                      fan.temp != null ? `${Math.round(fan.temp)} °C` : null,
                      fan.rpm != null ? `${fan.rpm} RPM` : null,
                      fan.percent != null ? `${fan.percent}%` : null,
                      fan.safety ? "safety 100%" : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </Field>
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
                    rgOptions={fan.curves.map((c) => ({ data: c, label: titleCase(c) }))}
                    selectedOption={f.curve}
                    onChange={(o) => change("fan", { curve: o.data }, 0)}
                  />
                </PanelSectionRow>
              )}
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
              <ErrorRow msg={state.errors.fan} />
            </>
          )}
        </PanelSection>

        <PanelSection>
          <PanelSectionRow>
            <Field label={<span className="hhctl-headlabel">Battery status</span>}>
              <span className="hhctl-inline">
                {[
                  battery.capacity != null ? `${battery.capacity}%` : null,
                  battery.ac && battery.inhibited && (battery.power_w ?? 0) < 1 ? "Held" : battery.status,
                  battery.power_w != null ? `${battery.power_w} W` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </Field>
          </PanelSectionRow>
          {battery.unavailable && !battery.limit_mode ? (
            <Unavailable why={battery.unavailable} />
          ) : (
            <>
              {battery.limit_mode && (
                <PanelSectionRow>
                  <SliderField
                    label="Charge limit"
                    description={battery.limit_mode === "emulated" ? "Enforced by the plugin while it runs" : undefined}
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
                    rgOptions={battery.bypass_options.map((o) => ({ data: o, label: BYPASS_LABELS[o] ?? o }))}
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
                      rgOptions={rgb.modes.map((m) => ({ data: m, label: titleCase(m) }))}
                      selectedOption={r.mode}
                      onChange={(o) => change("rgb", { mode: o.data }, 0)}
                    />
                  </PanelSectionRow>
                  {r.mode === "effect" && rgb.effects.length > 0 && (
                    <PanelSectionRow>
                      <DropdownItem
                        label="Effect"
                        rgOptions={rgb.effects.map((e) => ({ data: e, label: titleCase(e) }))}
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
                      description={[display.test.verdict, display.test.report ? `Report: ${display.test.report}` : null]
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
                      state.sdr_brightness.current != null ? `Now ${state.sdr_brightness.current} nits` : "Now at gamescope's default",
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
                    state.gamescope.hdr_active === null ? "" : `HDR output is ${state.gamescope.hdr_active ? "on" : "off"}. `
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
                    rgOptions={state.gamescope.modes.map((m) => ({ data: m, label: HDR_MODES[m] ?? m }))}
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
              description="First, record what the extra buttons and touchpads send. Start recording, close this menu, then press each extra button a few times, one after another, and swipe and click each touchpad. Recording only reads; nothing is remapped yet."
            />
          </PanelSectionRow>
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
              rgOptions={Object.entries(THEMES).map(([data, t]) => ({ data, label: t.label }))}
              selectedOption={theme}
              onChange={(o) => change("ui", { theme: o.data }, 0)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <DropdownItem
              label="Font"
              description={FONTS[draft.ui?.font ?? "default"]?.about}
              rgOptions={Object.entries(FONTS).map(([data, f]) => ({ data, label: f.label }))}
              selectedOption={draft.ui?.font ?? "default"}
              onChange={(o) => change("ui", { font: o.data }, 0)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              description="PNG, JPG or WebP, up to 8 MB. It's copied into the plugin's settings."
              onClick={async () => {
                try {
                  const res = await openFilePicker(
                    0 as FileSelectionType, // FILE
                    state.home ?? "/home",
                    true,
                    true,
                    undefined,
                    ["png", "jpg", "jpeg", "webp"],
                  );
                  setState(await setBackground(res.realpath || res.path));
                } catch {
                  // picker closed without choosing
                }
              }}
            >
              {draft.ui?.bg ? "Change background picture" : "Choose background picture"}
            </ButtonItem>
          </PanelSectionRow>
          {draft.ui?.bg && (
            <>
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
          {state.device.notes.map((n) => (
            <PanelSectionRow key={n}>
              <Field description={n} />
            </PanelSectionRow>
          ))}
          {state.conflicts.length > 0 && (
            <PanelSectionRow>
              <Field
                description={`${state.conflicts.join(", ")} is running. If it also controls TDP, RGB or the fan, it may override these settings.`}
              />
            </PanelSectionRow>
          )}
        </PanelSection>
      </div>
    </div>
  );
}

// Decky keeps the title bar fixed above the panel; the version sits next to the name.
function TitleView() {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    getVersion().then(setVersion);
  }, []);
  return (
    <div
      className={staticClasses.Title}
      style={{ display: "flex", flexDirection: "row", alignItems: "baseline", gap: "6px", whiteSpace: "nowrap", minWidth: 0 }}
    >
      <span style={{ fontSize: "0.8em", overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>Handheld Control</span>
      {version && <span style={{ fontSize: "0.55em", opacity: 0.6, flexShrink: 0 }}>v{version}</span>}
    </div>
  );
}

export default definePlugin(() => ({
  name: "Handheld Control",
  titleView: <TitleView />,
  content: <Content />,
  icon: <FaMicrochip />,
}));
