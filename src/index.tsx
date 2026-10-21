import { callable, definePlugin, useQuickAccessVisible } from "@decky/api";
import {
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
};

type Section = keyof Settings;

const getState = callable<[], State>("get_state");
const setters: Record<Section, (v: object) => Promise<State>> = {
  tdp: callable<[object], State>("set_tdp"),
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

  const refresh = async () => {
    const s = await getState();
    setState(s);
    // Don't clobber a slider the user is still dragging.
    if (Date.now() - lastEdit.current > 1500) setDraft(s.settings);
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

  const { tdp, fan, battery, rgb } = state;
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
                    label="Boost"
                    description="Allow short bursts above the sustained limit"
                    checked={t.boost}
                    onChange={(v) => change("tdp", { boost: v }, 0)}
                  />
                </PanelSectionRow>
              </>
            )}
            <ErrorRow msg={state.errors.tdp} />
          </>
        )}
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
