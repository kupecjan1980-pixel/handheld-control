// HHC Desktop: Handheld Control as a desktop app, laid out like Mission
// Center. The sidebar lists every device with a live mini graph (CPU,
// memory, GPU, disks, network), then processes and sensors, then Controls:
// the plugin's own panel with everything it can change on this PC.
import { callable } from "@decky/api";
import { ReactNode, useEffect, useRef, useState } from "react";

type Mon = {
  cpu: {
    model: string | null;
    cores: number | null;
    threads: number | null;
    usage: number;
    per_core: number[];
    freq_mhz: (number | null)[];
    load: number[];
    uptime_s: number;
    processes: number;
    threads_total: number;
  };
  memory: { total: number; available: number; used: number; cached: number; swap_total: number; swap_used: number };
  gpu: {
    busy: number | null;
    vram_used: number | null;
    vram_total: number | null;
    gtt_used: number | null;
    clock_mhz: number | null;
    temp: number | null;
    power_w: number | null;
    driver: string | null;
    name: string | null;
  } | null;
  disks: { name: string; model: string | null; size: number; rotational: boolean; read_bps: number; write_bps: number; busy: number }[];
  filesystems: { mount: string; device: string; type: string; total: number; used: number }[];
  network: { name: string; rx_bps: number; tx_bps: number; rx_total: number; tx_total: number; up: boolean; ips: string[] }[];
  sensors: { chip: string; label: string; kind: "temp" | "fan"; value: number }[];
  processes: { pid: number; name: string; user: string; cpu: number; rss: number; threads: number }[];
  system: { kernel: string; hostname: string };
};

const getMonitor = callable<[], Mon>("get_monitor");
const getState = callable<[], { settings: { ui: { theme: string } } }>("get_state");

const HISTORY = 60; // samples, one a second
// Mirrors the panel's theme headline colors (src/index.tsx THEMES).
const ACCENT: Record<string, string> = {
  default: "#1a9fff",
  red: "#ff6b6b",
  blue: "#6aa8ff",
  green: "#5fd877",
  gold: "#ffc940",
};
const SECOND = "#c8ccd0"; // second line (network send, disk write)

const bytes = (n: number) => {
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) (n /= 1024), i++;
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
};
const rate = (n: number) => `${bytes(n)}/s`;
const pct = (n: number) => `${Math.round(n)}%`;
const uptime = (s: number) => {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${d ? `${d}d ` : ""}${h}h ${m}m`;
};
const store = {
  get: (k: string) => {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set: (k: string, v: string) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      // private window or blocked storage: just not remembered
    }
  },
};

// --- data -----------------------------------------------------------------

function useMonitor() {
  const [mon, setMon] = useState<Mon | null>(null);
  const hist = useRef<Record<string, number[]>>({});
  useEffect(() => {
    let stop = false;
    const push = (key: string, v: number) => {
      const h = (hist.current[key] ??= []);
      h.push(v);
      if (h.length > HISTORY) h.shift();
    };
    const tick = async () => {
      if (document.visibilityState === "visible") {
        try {
          const m = await getMonitor();
          push("cpu", m.cpu.usage);
          m.cpu.per_core.forEach((v, i) => push(`core${i}`, v));
          push("mem", m.memory.used);
          push("swap", m.memory.swap_used);
          if (m.gpu?.busy != null) push("gpu", m.gpu.busy);
          if (m.gpu?.vram_used != null) push("vram", m.gpu.vram_used);
          m.disks.forEach((d) => {
            push(`disk:${d.name}`, d.busy);
            push(`dr:${d.name}`, d.read_bps);
            push(`dw:${d.name}`, d.write_bps);
          });
          m.network.forEach((n) => {
            push(`rx:${n.name}`, n.rx_bps);
            push(`tx:${n.name}`, n.tx_bps);
          });
          if (!stop) setMon(m);
        } catch {
          // backend restarting; the next tick tries again
        }
      }
      if (!stop) setTimeout(tick, 1000);
    };
    tick();
    return () => {
      stop = true;
    };
  }, []);
  return { mon, hist: hist.current };
}

function useAccent() {
  const [theme, setTheme] = useState("default");
  useEffect(() => {
    const load = () => getState().then((s) => setTheme(s.settings.ui.theme), () => {});
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, []);
  return ACCENT[theme] ?? ACCENT.default;
}

// --- pieces ---------------------------------------------------------------

function Graph({
  series,
  max,
  colors,
  height,
  label,
}: {
  series: number[][];
  max: number;
  colors: string[];
  height: number;
  label?: string;
}) {
  const w = HISTORY - 1;
  return (
    <div className="g-wrap">
      {label && <span className="g-top">{label}</span>}
      <svg className="g" viewBox={`0 0 ${w} 100`} preserveAspectRatio="none" style={{ height }}>
        {[20, 40, 60, 80].map((y) => (
          <line key={y} x1={0} x2={w} y1={y} y2={y} className="g-grid" />
        ))}
        {[10, 20, 30, 40, 50].map((x) => (
          <line key={x} x1={(x * w) / 60} x2={(x * w) / 60} y1={0} y2={100} className="g-grid" />
        ))}
        {series.map((vals, k) => {
          const pts = Array.from({ length: HISTORY }, (_, i) => {
            const v = vals[vals.length - HISTORY + i] ?? 0;
            return `${i},${100 - Math.min(100, (100 * v) / (max || 1))}`;
          });
          return (
            <g key={k}>
              <polygon points={`0,100 ${pts.join(" ")} ${w},100`} fill={colors[k]} opacity={k === 0 ? 0.2 : 0.08} />
              <polyline points={pts.join(" ")} fill="none" stroke={colors[k]} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
            </g>
          );
        })}
      </svg>
      {height > 60 && <span className="g-bottom">60 seconds</span>}
    </div>
  );
}

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  return (
    <div className="bar">
      <div style={{ width: `${Math.min(100, (100 * value) / (max || 1))}%`, background: color }} />
    </div>
  );
}

const Stats = ({ rows }: { rows: [string, ReactNode][] }) => (
  <dl className="stats">
    {rows
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
  </dl>
);

function Page({ title, sub, value, children }: { title: string; sub?: string | null; value?: string; children: ReactNode }) {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {sub && <div className="page-sub">{sub}</div>}
        </div>
        {value && <div className="page-value">{value}</div>}
      </header>
      {children}
    </div>
  );
}

// --- pages ----------------------------------------------------------------

function CpuPage({ mon, hist, a }: { mon: Mon; hist: Record<string, number[]>; a: string }) {
  const [perCore, setPerCore] = useState(store.get("hhc.percore") === "1");
  const { cpu } = mon;
  const temp = mon.sensors.find((s) => s.kind === "temp" && /k10temp|coretemp|zenpower/.test(s.chip));
  const freqs = cpu.freq_mhz.filter((f): f is number => f != null);
  const avg = freqs.length ? freqs.reduce((x, y) => x + y, 0) / freqs.length : null;
  return (
    <Page title="CPU" sub={cpu.model} value={pct(cpu.usage)}>
      <div className="seg">
        {[
          ["Overall", false],
          ["Per core", true],
        ].map(([label, v]) => (
          <button
            key={String(label)}
            className={perCore === v ? "on" : ""}
            onClick={() => {
              setPerCore(v as boolean);
              store.set("hhc.percore", v ? "1" : "0");
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {perCore ? (
        <div className="cores">
          {cpu.per_core.map((v, i) => (
            <div key={i}>
              <Graph series={[hist[`core${i}`] ?? []]} max={100} colors={[a]} height={70} />
              <span className="core-label">
                CPU {i} · {pct(v)}
                {cpu.freq_mhz[i] ? ` · ${(cpu.freq_mhz[i]! / 1000).toFixed(2)} GHz` : ""}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <Graph series={[hist.cpu ?? []]} max={100} colors={[a]} height={300} label="% Utilization" />
      )}
      <Stats
        rows={[
          ["Utilization", pct(cpu.usage)],
          ["Speed", avg ? `${(avg / 1000).toFixed(2)} GHz` : null],
          ["Temperature", temp ? `${temp.value} °C` : null],
          ["Processes", cpu.processes],
          ["Threads", cpu.threads_total],
          ["Up time", uptime(cpu.uptime_s)],
          ["Cores", cpu.cores],
          ["Logical processors", cpu.threads],
          ["Load average", cpu.load.map((x) => x.toFixed(2)).join(" · ")],
        ]}
      />
    </Page>
  );
}

function MemoryPage({ mon, hist, a }: { mon: Mon; hist: Record<string, number[]>; a: string }) {
  const m = mon.memory;
  return (
    <Page title="Memory" sub={`${bytes(m.total)} total`} value={`${bytes(m.used)} / ${bytes(m.total)}`}>
      <Graph series={[hist.mem ?? []]} max={m.total} colors={[a]} height={260} label="Memory usage" />
      <div className="compose">
        <div style={{ width: `${(100 * (m.used - 0)) / m.total}%`, background: a }} title="In use" />
        <div style={{ width: `${(100 * Math.min(m.cached, m.available)) / m.total}%`, background: `${a}55` }} title="Cached" />
      </div>
      <Stats
        rows={[
          ["In use", `${bytes(m.used)} (${pct((100 * m.used) / (m.total || 1))})`],
          ["Available", bytes(m.available)],
          ["Cached", bytes(m.cached)],
          ["Swap used", m.swap_total ? `${bytes(m.swap_used)} / ${bytes(m.swap_total)}` : "no swap"],
        ]}
      />
      {m.swap_total > 0 && (
        <>
          <h2>Swap</h2>
          <Graph series={[hist.swap ?? []]} max={m.swap_total} colors={[a]} height={90} />
        </>
      )}
    </Page>
  );
}

function GpuPage({ mon, hist, a }: { mon: Mon; hist: Record<string, number[]>; a: string }) {
  const g = mon.gpu!;
  return (
    <Page title="GPU" sub={g.name ?? g.driver} value={g.busy != null ? pct(g.busy) : undefined}>
      {g.busy != null && <Graph series={[hist.gpu ?? []]} max={100} colors={[a]} height={260} label="% Utilization" />}
      {g.vram_total ? (
        <>
          <h2>Video memory</h2>
          <Graph series={[hist.vram ?? []]} max={g.vram_total} colors={[a]} height={90} />
        </>
      ) : null}
      <Stats
        rows={[
          ["Utilization", g.busy != null ? pct(g.busy) : null],
          ["Clock", g.clock_mhz ? `${g.clock_mhz} MHz` : null],
          ["Temperature", g.temp != null ? `${g.temp} °C` : null],
          ["Power", g.power_w != null ? `${g.power_w} W` : null],
          ["Video memory", g.vram_total ? `${bytes(g.vram_used ?? 0)} / ${bytes(g.vram_total)}` : null],
          ["Shared memory (GTT)", g.gtt_used != null ? bytes(g.gtt_used) : null],
          ["Driver", g.driver],
        ]}
      />
    </Page>
  );
}

function DiskPage({ mon, hist, a, name }: { mon: Mon; hist: Record<string, number[]>; a: string; name: string }) {
  const d = mon.disks.find((x) => x.name === name);
  if (!d) return <Page title={`Disk ${name}`}>Disconnected.</Page>;
  const max = Math.max(1024 * 1024, ...(hist[`dr:${name}`] ?? []), ...(hist[`dw:${name}`] ?? []));
  return (
    <Page title={`Disk ${name}`} sub={[d.model, d.rotational ? "HDD" : "SSD"].filter(Boolean).join(" · ")} value={pct(d.busy)}>
      <Graph series={[hist[`disk:${name}`] ?? []]} max={100} colors={[a]} height={200} label="Active time" />
      <h2>Transfer rate</h2>
      <Graph series={[hist[`dr:${name}`] ?? [], hist[`dw:${name}`] ?? []]} max={max} colors={[a, SECOND]} height={110} label={rate(max)} />
      <Stats
        rows={[
          ["Active time", pct(d.busy)],
          ["Read speed", rate(d.read_bps)],
          ["Write speed", rate(d.write_bps)],
          ["Capacity", bytes(d.size)],
          ["Type", d.rotational ? "HDD" : "SSD"],
        ]}
      />
      {mon.filesystems.some((f) => f.device.includes(name)) && <h2>File systems</h2>}
      {mon.filesystems
        .filter((f) => f.device.includes(name))
        .map((f) => (
          <Fs key={f.device} f={f} a={a} />
        ))}
    </Page>
  );
}

const Fs = ({ f, a }: { f: Mon["filesystems"][number]; a: string }) => (
  <div className="fs">
    <div className="fs-top">
      <span>{f.mount}</span>
      <span>
        {bytes(f.used)} / {bytes(f.total)} · {f.type}
      </span>
    </div>
    <Bar value={f.used} max={f.total} color={a} />
  </div>
);

function NetPage({ mon, hist, a, name }: { mon: Mon; hist: Record<string, number[]>; a: string; name: string }) {
  const n = mon.network.find((x) => x.name === name);
  if (!n) return <Page title={name}>Disconnected.</Page>;
  const max = Math.max(64 * 1024, ...(hist[`rx:${name}`] ?? []), ...(hist[`tx:${name}`] ?? []));
  return (
    <Page title={netLabel(name)} sub={name} value={n.up ? undefined : "Disconnected"}>
      <Graph series={[hist[`rx:${name}`] ?? [], hist[`tx:${name}`] ?? []]} max={max} colors={[a, SECOND]} height={280} label={rate(max)} />
      <div className="legend">
        <span style={{ color: a }}>■ Receiving</span>
        <span style={{ color: SECOND }}>■ Sending</span>
      </div>
      <Stats
        rows={[
          ["Receiving", rate(n.rx_bps)],
          ["Sending", rate(n.tx_bps)],
          ["Received in total", bytes(n.rx_total)],
          ["Sent in total", bytes(n.tx_total)],
          ["IP address", n.ips.join(", ") || "none"],
          ["State", n.up ? "Connected" : "Disconnected"],
        ]}
      />
    </Page>
  );
}

const netLabel = (n: string) => (n.startsWith("w") ? "Wi-Fi" : n.startsWith("e") ? "Ethernet" : n.startsWith("tun") || n.startsWith("wg") ? "VPN" : "Network");

function ProcessesPage({ mon }: { mon: Mon }) {
  const [sort, setSort] = useState<"cpu" | "rss" | "name" | "pid">((store.get("hhc.sort") as any) || "cpu");
  const [q, setQ] = useState("");
  const rows = mon.processes
    .filter((p) => !q || p.name.toLowerCase().includes(q.toLowerCase()) || String(p.pid) === q)
    .sort((x, y) =>
      sort === "name" ? x.name.localeCompare(y.name) : sort === "pid" ? x.pid - y.pid : (y[sort] as number) - (x[sort] as number),
    );
  const head = (key: typeof sort, label: string, num = false) => (
    <th
      className={`${num ? "num " : ""}${sort === key ? "sorted" : ""}`}
      onClick={() => {
        setSort(key);
        store.set("hhc.sort", key);
      }}
    >
      {label}
    </th>
  );
  return (
    <Page title="Processes" sub={`${mon.cpu.processes} processes · ${mon.cpu.threads_total} threads`}>
      <input className="search" placeholder="Search by name or PID" value={q} onChange={(e) => setQ(e.target.value)} />
      <table className="table">
        <thead>
          <tr>
            {head("name", "Name")}
            <th>User</th>
            {head("pid", "PID", true)}
            {head("cpu", "CPU", true)}
            {head("rss", "Memory", true)}
            <th className="num">Threads</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 300).map((p) => (
            <tr key={p.pid}>
              <td>{p.name}</td>
              <td className="dim">{p.user}</td>
              <td className="num dim">{p.pid}</td>
              <td className="num">{p.cpu.toFixed(1)}%</td>
              <td className="num">{bytes(p.rss)}</td>
              <td className="num dim">{p.threads}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Page>
  );
}

function SensorsPage({ mon, a }: { mon: Mon; a: string }) {
  const chips = Array.from(new Set(mon.sensors.map((s) => s.chip)));
  return (
    <Page title="Sensors" sub={`${mon.sensors.length} readings`}>
      <div className="chips">
        {chips.map((c) => (
          <section key={c} className="chip">
            <h2 style={{ color: a }}>{c}</h2>
            {mon.sensors
              .filter((s) => s.chip === c)
              .map((s, i) => (
                <div key={i} className="sensor">
                  <span>{s.label}</span>
                  <span className="num">{s.kind === "temp" ? `${s.value} °C` : `${s.value} RPM`}</span>
                </div>
              ))}
          </section>
        ))}
      </div>
      <h2>File systems</h2>
      {mon.filesystems.map((f) => (
        <Fs key={f.device} f={f} a={a} />
      ))}
      <div className="foot">
        {mon.system.hostname} · Linux {mon.system.kernel}
      </div>
    </Page>
  );
}

// --- app ------------------------------------------------------------------

type Item = { id: string; label: string; detail: string; series?: number[][]; max?: number; group: string };

export function App({ title, panel }: { title: ReactNode; panel: ReactNode }) {
  const { mon, hist } = useMonitor();
  const a = useAccent();
  const [page, setPage] = useState(store.get("hhc.page") || "controls");
  const go = (id: string) => {
    setPage(id);
    store.set("hhc.page", id);
  };

  const items: Item[] = [];
  if (mon) {
    const { cpu, memory: m, gpu } = mon;
    const f = cpu.freq_mhz.filter((x): x is number => x != null);
    items.push({
      id: "cpu",
      group: "Performance",
      label: "CPU",
      detail: `${pct(cpu.usage)}${f.length ? ` · ${(f.reduce((x, y) => x + y, 0) / f.length / 1000).toFixed(2)} GHz` : ""}`,
      series: [hist.cpu ?? []],
      max: 100,
    });
    items.push({
      id: "memory",
      group: "Performance",
      label: "Memory",
      detail: `${bytes(m.used)} / ${bytes(m.total)} (${pct((100 * m.used) / (m.total || 1))})`,
      series: [hist.mem ?? []],
      max: m.total,
    });
    if (gpu)
      items.push({
        id: "gpu",
        group: "Performance",
        label: "GPU",
        detail: [gpu.busy != null ? pct(gpu.busy) : null, gpu.temp != null ? `${gpu.temp} °C` : null].filter(Boolean).join(" · "),
        series: [hist.gpu ?? []],
        max: 100,
      });
    mon.disks.forEach((d) =>
      items.push({
        id: `disk:${d.name}`,
        group: "Performance",
        label: `Disk ${d.name}`,
        detail: `${d.rotational ? "HDD" : "SSD"} · ${pct(d.busy)}`,
        series: [hist[`disk:${d.name}`] ?? []],
        max: 100,
      }),
    );
    mon.network
      .filter((n) => n.up)
      .forEach((n) =>
        items.push({
          id: `net:${n.name}`,
          group: "Performance",
          label: netLabel(n.name),
          detail: `↓ ${rate(n.rx_bps)} ↑ ${rate(n.tx_bps)}`,
          series: [hist[`rx:${n.name}`] ?? [], hist[`tx:${n.name}`] ?? []],
          max: Math.max(64 * 1024, ...(hist[`rx:${n.name}`] ?? []), ...(hist[`tx:${n.name}`] ?? [])),
        }),
      );
    items.push({ id: "processes", group: "System", label: "Processes", detail: `${cpu.processes} running` });
    items.push({ id: "sensors", group: "System", label: "Sensors", detail: `${mon.sensors.length} readings` });
  }
  items.push({ id: "controls", group: "Handheld Control", label: "Controls", detail: "TDP, clocks, fan, RGB, themes" });

  let body: ReactNode = null;
  if (page === "controls") {
    body = (
      <div className="controls">
        <div className="controls-title">{title}</div>
        <div className="controls-scroll">{panel}</div>
      </div>
    );
  } else if (!mon) body = <div className="loading">Starting the system monitor…</div>;
  else if (page === "cpu") body = <CpuPage mon={mon} hist={hist} a={a} />;
  else if (page === "memory") body = <MemoryPage mon={mon} hist={hist} a={a} />;
  else if (page === "gpu" && mon.gpu) body = <GpuPage mon={mon} hist={hist} a={a} />;
  else if (page.startsWith("disk:")) body = <DiskPage mon={mon} hist={hist} a={a} name={page.slice(5)} />;
  else if (page.startsWith("net:")) body = <NetPage mon={mon} hist={hist} a={a} name={page.slice(4)} />;
  else if (page === "processes") body = <ProcessesPage mon={mon} />;
  else if (page === "sensors") body = <SensorsPage mon={mon} a={a} />;
  else body = <CpuPage mon={mon} hist={hist} a={a} />;

  const groups = Array.from(new Set(items.map((i) => i.group)));
  return (
    <div className="app" style={{ ["--accent" as string]: a }}>
      <nav className="side">
        <div className="brand">HHC Desktop</div>
        {groups.map((g) => (
          <div key={g}>
            <div className="side-group">{g}</div>
            {items
              .filter((i) => i.group === g)
              .map((i) => (
                <button key={i.id} className={`side-item${page === i.id ? " on" : ""}`} onClick={() => go(i.id)}>
                  {i.series && (
                    <span className="side-graph">
                      <Graph series={i.series} max={i.max ?? 100} colors={[a, SECOND]} height={36} />
                    </span>
                  )}
                  <span className="side-text">
                    <span className="side-label">{i.label}</span>
                    <span className="side-detail">{i.detail}</span>
                  </span>
                </button>
              ))}
          </div>
        ))}
      </nav>
      <main className="main">{body}</main>
    </div>
  );
}
