// Browser stand-ins for the @decky/ui pieces the panel uses, shaped like
// Steam's Quick Access controls. Class names follow Steam's
// "<module>_<Name>_<hash>" pattern, so the panel's theme CSS (which matches
// both the class map and [class*="gamepaddialog_..."]) works unchanged.
import { CSSProperties, ReactNode, useState } from "react";

export const gamepadDialogClasses: Record<string, string> = {
  Field: "gamepaddialog_Field_d1",
  FieldLabel: "gamepaddialog_FieldLabel_d1",
  FieldDescription: "gamepaddialog_FieldDescription_d1",
  FieldChildren: "gamepaddialog_FieldChildren_d1",
  Toggle: "gamepaddialog_Toggle_d1",
  ToggleRail: "gamepaddialog_ToggleRail_d1",
  ToggleSwitch: "gamepaddialog_ToggleSwitch_d1",
  On: "gamepaddialog_On_d1",
};
export const gamepadSliderClasses: Record<string, string> = {
  SliderControl: "gamepadslider_SliderControl_d1",
  SliderTrack: "gamepadslider_SliderTrack_d1",
};
export const staticClasses: Record<string, string> = {
  Title: "staticclasses_Title_d1",
  PanelSectionTitle: "quickaccesscontrols_PanelSectionTitle_d1",
};

const dlg = gamepadDialogClasses;

export const Navigation = {
  OpenQuickAccessMenu: (_tab?: unknown) => {},
  CloseSideMenus: () => {},
  Navigate: (_route: string) => {},
};

export const getGamepadNavigationTrees = (): any[] => [];

export function PanelSection({ title, children }: { title?: string; children?: ReactNode }) {
  return (
    <div className="hhc-section">
      {title && <div className={staticClasses.PanelSectionTitle}>{title}</div>}
      {children}
    </div>
  );
}

export function PanelSectionRow({ children }: { children?: ReactNode }) {
  return <div className="hhc-row">{children}</div>;
}

type FieldProps = {
  label?: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  bottomSeparator?: string;
  [k: string]: unknown;
};

export function Field({ label, description, children }: FieldProps) {
  return (
    <div className={dlg.Field}>
      <div className="hhc-field-top">
        {label != null && <div className={dlg.FieldLabel}>{label}</div>}
        {children != null && <div className={dlg.FieldChildren}>{children}</div>}
      </div>
      {description && <div className={dlg.FieldDescription}>{description}</div>}
    </div>
  );
}

export function ToggleField(p: {
  label?: ReactNode;
  description?: ReactNode;
  checked: boolean;
  disabled?: boolean;
  onChange?: (v: boolean) => void;
}) {
  return (
    <Field label={p.label} description={p.description}>
      <button
        className={`${dlg.Toggle}${p.checked ? ` ${dlg.On}` : ""}`}
        disabled={p.disabled}
        onClick={() => p.onChange?.(!p.checked)}
        aria-pressed={p.checked}
      >
        <span className={dlg.ToggleRail} />
        <span className={dlg.ToggleSwitch} />
      </button>
    </Field>
  );
}

export function SliderField(p: {
  label?: ReactNode;
  description?: ReactNode;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  showValue?: boolean;
  valueSuffix?: string;
  disabled?: boolean;
  onChange?: (v: number) => void;
}) {
  const min = p.min ?? 0;
  const max = p.max ?? 100;
  const norm = max > min ? (p.value - min) / (max - min) : 0;
  return (
    <div className={dlg.Field}>
      <div className="hhc-field-top">
        <div className={dlg.FieldLabel}>{p.label}</div>
        {p.showValue && (
          <div className={dlg.FieldChildren}>
            {p.value}
            {p.valueSuffix ?? ""}
          </div>
        )}
      </div>
      <div className={gamepadSliderClasses.SliderControl} style={{ ["--normalized-slider-value" as string]: norm }}>
        <input
          type="range"
          className={gamepadSliderClasses.SliderTrack}
          min={min}
          max={max}
          step={p.step ?? 1}
          value={p.value}
          disabled={p.disabled}
          onChange={(e) => p.onChange?.(Number(e.target.value))}
        />
      </div>
      {p.description && <div className={dlg.FieldDescription}>{p.description}</div>}
    </div>
  );
}

export function DropdownItem(p: {
  label?: ReactNode;
  description?: ReactNode;
  rgOptions: { data: any; label: ReactNode }[];
  selectedOption: any;
  disabled?: boolean;
  onChange?: (o: { data: any; label: ReactNode }) => void;
}) {
  const idx = p.rgOptions.findIndex((o) => o.data === p.selectedOption);
  return (
    <Field label={p.label} description={p.description}>
      <select
        className="hhc-select DialogButton_d1"
        value={idx}
        disabled={p.disabled}
        onChange={(e) => p.onChange?.(p.rgOptions[Number(e.target.value)])}
      >
        {p.rgOptions.map((o, i) => (
          <option key={i} value={i}>
            {typeof o.label === "string" ? o.label : String(o.data)}
          </option>
        ))}
      </select>
    </Field>
  );
}

export function ButtonItem(p: {
  layout?: string;
  description?: ReactNode;
  disabled?: boolean;
  onClick?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className={dlg.Field}>
      <button className="hhc-button DialogButton_d1" disabled={p.disabled} onClick={p.onClick}>
        {p.children}
      </button>
      {p.description && <div className={dlg.FieldDescription}>{p.description}</div>}
    </div>
  );
}

// Steam-only props are dropped; Enter / click activate, focus shows the class.
export function Focusable(props: {
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  focusClassName?: string;
  onActivate?: (e: any) => void;
  onClick?: (e: any) => void;
  onFocus?: (e: any) => void;
  onGamepadFocus?: (e: any) => void;
  onGamepadBlur?: (e: any) => void;
  [k: string]: unknown;
}) {
  const [focused, setFocused] = useState(false);
  const { children, className, style, focusClassName, onActivate, onClick, onFocus, onGamepadFocus, onGamepadBlur } =
    props;
  const activate = onActivate ?? onClick;
  return (
    <div
      className={[className, focused && focusClassName].filter(Boolean).join(" ")}
      style={style}
      tabIndex={activate ? 0 : undefined}
      onClick={(e) => (onClick ?? onActivate)?.(e)}
      onKeyDown={(e) => e.key === "Enter" && activate?.(e)}
      onFocus={(e) => {
        setFocused(true);
        onFocus?.(e);
        onGamepadFocus?.(e);
      }}
      onBlur={(e) => {
        setFocused(false);
        onGamepadBlur?.(e);
      }}
    >
      {children}
    </div>
  );
}
