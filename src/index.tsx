import { callable, definePlugin, useQuickAccessVisible } from "@decky/api";
import {
  ButtonItem,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  staticClasses,
} from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { FaMicrochip } from "react-icons/fa";

type Settings = {
  tdp: { enabled: boolean; watts: number | null; boost: boolean };
  epp: { enabled: boolean; value: string };
  cpu: { boost: boolean | null; prefcore: boolean | null };
  fan: { mode: "auto" | "fixed" | "curve"; percent: number; curve: string };
  rgb: { enabled: boolean; mode: string; hue: number; sat: number; bright: number; effect: string | null };
  battery: { limit: number; bypass: string };
  ec: { enabled: boolean };
};

type State = {
  device: { name: string; board: string; vendor: string; notes: string[] };
  conflicts: string[];
  settings: Settings;
  errors: Record<string, string>;
  tdp: { backend: string | null; min: number; max: number; default: number; current: number | null };
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
    ac?: boolean;
  };
  rgb: { backend: string | null; modes: string[]; effects: string[] };
  ec: { supported: boolean; lockdown: string | null };
  epp: {
    available: boolean;
    unavailable: string | null;
    driver: string | null;
    options: string[];
    current: string | null;
  };
  cpu: Record<"boost" | "prefcore", { available: boolean; unavailable: string | null; on: boolean | null }>;
  gpu: {
    driver: string | null;
    gpu_w: number | null;
    pkg_w: number | null;
    freq_mhz: number | null;
    vram_mb: number | null;
    gtt_mb: number | null;
    mem_mb: number | null;
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
    toggles: Record<string, { params: string[]; configured: boolean; active: boolean; reboot: boolean }>;
  };
};

type Section = keyof Settings;

const getState = callable<[], State>("get_state");
const setBrightness = callable<[number], State>("set_brightness");
const setDisplay = callable<[string, boolean], State>("set_display");
const brightnessTest = callable<[], State>("brightness_test");
const brightnessTestAnswer = callable<[string], State>("brightness_test_answer");
const setters: Record<Section, (v: object) => Promise<State>> = {
  tdp: callable<[object], State>("set_tdp"),
  epp: callable<[object], State>("set_epp"),
  cpu: callable<[object], State>("set_cpu"),
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

const gb = (mb: number) => `${(mb / 1024).toFixed(1)} GB`;

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

  const refresh = async () => {
    const s = await getState();
    setState(s);
    // Don't clobber a slider the user is still dragging.
    if (Date.now() - lastEdit.current > 1500) setDraft(s.settings);
    if (Date.now() - lastBrightEdit.current > 1500) setBright(s.display.brightness);
  };

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
  const perf = [
    gpu.pkg_w != null ? `Package ${gpu.pkg_w} W` : null,
    gpu.gpu_w != null ? `GPU ${gpu.gpu_w} W` : null,
    gpu.vram_mb != null ? `VRAM ${gb(gpu.vram_mb)}` : null,
    gpu.gtt_mb != null ? `GTT ${gb(gpu.gtt_mb)}` : null,
    gpu.mem_mb != null ? `GPU mem ${gb(gpu.mem_mb)}` : null,
    gpu.freq_mhz != null ? `${gpu.freq_mhz} MHz` : null,
  ].filter(Boolean);
  const t = draft.tdp;
  const f = draft.fan;
  const b = draft.battery;
  const r = draft.rgb;

  return (
    <>
      <PanelSection title={state.device.name}>
        {state.conflicts.length > 0 && (
          <PanelSectionRow>
            <Field
              label="Conflict"
              description={`${state.conflicts.join(", ")} is running and may override these settings. Disable its TDP/RGB/fan options or stop it.`}
            />
          </PanelSectionRow>
        )}
        {state.device.notes.map((n) => (
          <PanelSectionRow key={n}>
            <Field description={n} />
          </PanelSectionRow>
        ))}
      </PanelSection>

      <PanelSection title="TDP">
        {perf.length > 0 && (
          <PanelSectionRow>
            <Field label="Now" description={perf.join(" · ")} />
          </PanelSectionRow>
        )}
        {!tdp.backend ? (
          <Unavailable why="No TDP interface found (firmware-attributes, RAPL or ryzenadj)." />
        ) : (
          <>
            <PanelSectionRow>
              <ToggleField
                label="Custom TDP"
                description={`via ${tdp.backend}${tdp.current ? ` · now ${tdp.current} W` : ""}`}
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
        <ErrorRow msg={state.errors.cpu} />
      </PanelSection>

      <PanelSection title="Fan">
        {!fan.backend ? (
          <Unavailable why={fan.unavailable ?? "No fan control"} />
        ) : (
          <>
            <PanelSectionRow>
              <Field
                label="Status"
                description={[
                  fan.temp != null ? `${Math.round(fan.temp)} °C` : null,
                  fan.rpm != null ? `${fan.rpm} RPM` : null,
                  fan.percent != null ? `${fan.percent}%` : null,
                  fan.safety ? "safety override: 100%" : null,
                  `via ${fan.backend}`,
                  fan.watchdog ? "watchdog armed" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Mode"
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

      <PanelSection title="Battery">
        <PanelSectionRow>
          <Field
            label="Status"
            description={[
              battery.capacity != null ? `${battery.capacity}%` : null,
              battery.status,
              battery.power_w ? `${battery.power_w} W` : null,
              battery.ac ? "on AC" : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          />
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

      <PanelSection title="RGB">
        {!rgb.backend ? (
          <Unavailable why="No RGB controller found." />
        ) : (
          <>
            <PanelSectionRow>
              <ToggleField
                label="Control lighting"
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

      {(display.backlight || display.bootloader) && (
        <PanelSection title="Display">
          {display.backlight && bright != null && (
            <PanelSectionRow>
              <SliderField
                label="Brightness"
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
                  description="Steps brightness to 25%, 75% and back, twice: normally, then with Panel Self Refresh off. Watch the screen during both passes."
                  onClick={async () => setState(await brightnessTest())}
                >
                  {display.test.running
                    ? `Pass ${display.test.phase ?? 1} of ${display.test.phases}… watch the screen`
                    : "Run brightness test"}
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
                  DISPLAY_TOGGLES[group]?.description,
                  t.params.join(" "),
                  t.reboot ? "Reboot required" : t.active ? "Active" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
                checked={t.configured}
                disabled={display.busy}
                onChange={async (v) => setState(await setDisplay(group, v))}
              />
            </PanelSectionRow>
          ))}
          {display.message && (
            <PanelSectionRow>
              <Field description={display.message} />
            </PanelSectionRow>
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
    </>
  );
}

export default definePlugin(() => ({
  name: "Handheld Control",
  titleView: <div className={staticClasses.Title}>Handheld Control</div>,
  content: <Content />,
  icon: <FaMicrochip />,
}));
