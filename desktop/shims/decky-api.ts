// Browser stand-in for @decky/api: callable() becomes a POST to server.py,
// backend events arrive over server-sent events.

const TOKEN = new URLSearchParams(location.search).get("token") ?? "";

export const callable =
  <Args extends any[] = [], Ret = unknown>(name: string) =>
  async (...args: Args): Promise<Ret> => {
    let r: Response;
    try {
      r = await fetch(`/api/${name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-HHC-Token": TOKEN },
        body: JSON.stringify(args),
      });
    } catch (e) {
      // The backend restarts after an update; reload once it's back.
      reloadWhenBack();
      throw e;
    }
    // systemd holds the port while the backend restarts after an update, so
    // calls wait for the new version instead of failing: notice it by its boot id.
    const boot = r.headers.get("X-HHC-Boot");
    if (boot && bootSeen && boot !== bootSeen) location.reload();
    bootSeen ??= boot;
    const data = await r.json();
    if (!r.ok) throw new Error(String(data));
    return data as Ret;
  };

let bootSeen: string | null = null;
let reloading = false;
const reloadWhenBack = () => {
  if (reloading) return;
  reloading = true;
  const tryReload = async () => {
    try {
      if ((await fetch("/ping")).ok) return location.reload();
    } catch {
      // still restarting
    }
    setTimeout(tryReload, 1000);
  };
  setTimeout(tryReload, 1500);
};

type Listener = (...args: any[]) => any;
const listeners = new Map<string, Set<Listener>>();
let source: EventSource | null = null;

export const addEventListener = <Args extends any[] = []>(event: string, fn: (...args: Args) => any) => {
  if (!source) {
    source = new EventSource(`/events?token=${encodeURIComponent(TOKEN)}`);
    source.onmessage = (e) => {
      const { event: name, args } = JSON.parse(e.data);
      listeners.get(name)?.forEach((f) => f(...args));
    };
  }
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event)!.add(fn as Listener);
  return fn;
};

export const removeEventListener = <Args extends any[] = []>(event: string, fn: (...args: Args) => any) => {
  listeners.get(event)?.delete(fn as Listener);
};

export const toaster = {
  toast: ({ title, body }: { title?: string; body?: string }) => {
    const el = document.createElement("div");
    el.className = "hhc-toast";
    el.textContent = [title, body].filter(Boolean).join(" · ");
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 5000);
  },
};

// The window is the panel, so it's always "open".
export const useQuickAccessVisible = () => true;

// Returns the factory; desktop/entry.tsx calls it once React is in place.
export const definePlugin = <T,>(fn: () => T) => fn as unknown as T;
