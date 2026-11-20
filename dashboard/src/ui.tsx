import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
  type ButtonHTMLAttributes, type ReactNode,
} from "react";
import type { Status, Today } from "./api";

// ---------- routing: #page/id?query ----------
export type Route = { page: string; id: string | null; query: URLSearchParams };
export function parseHash(): Route {
  const [path, qs = ""] = location.hash.slice(1).split("?");
  const [page = "", id = null] = path.split("/");
  return { page, id, query: new URLSearchParams(qs) };
}
export function useRoute(): Route {
  const [r, setR] = useState(parseHash);
  useEffect(() => {
    const on = () => setR(parseHash());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return r;
}
export const go = (href: string) => { location.hash = href.replace(/^#/, ""); };

// ---------- app-wide status ----------
type Ctx = { status: Status | null; today: Today | null; refresh: () => void };
export const StatusContext = createContext<Ctx>({ status: null, today: null, refresh: () => {} });
export const useStatus = () => useContext(StatusContext);

// ---------- toasts ----------
type Toast = { id: number; text: string; tone?: "live" | "bad" };
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone?: Toast["tone"]) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, tone }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.tone ?? ""}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}

// ---------- layout ----------
export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  useEffect(() => { document.title = `${title} | Curiox OS`; }, [title]);
  return (
    <>
      <div className="page-head">
        <div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
        {actions && <div className="row">{actions}</div>}
      </div>
      {children}
    </>
  );
}

export function Panel({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && <div className="panel-head"><h3>{title}</h3>{actions && <div className="row">{actions}</div>}</div>}
      {children}
    </section>
  );
}

export function Stat({ label, value, delta, children }: { label: string; value: ReactNode; delta?: number | null; children?: ReactNode }) {
  return (
    <div className="panel stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta != null && <div className={`delta ${delta > 0 ? "up" : delta < 0 ? "down" : ""}`}>{delta > 0 ? "+" : ""}{fmtNum(delta)} in 7 days</div>}
      {children}
    </div>
  );
}

export type Tone = "live" | "signal" | "bad" | "info" | "";
export const Badge = ({ tone = "", children }: { tone?: Tone; children: ReactNode }) => <span className={`badge ${tone}`}>{children}</span>;

export function Button({ variant, size, busy, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger"; size?: "sm"; busy?: boolean;
}) {
  return (
    <button {...rest} disabled={busy || rest.disabled} className={`btn ${variant ?? ""} ${size ?? ""} ${rest.className ?? ""}`}>
      {busy && <span className="spin" aria-hidden />}{children}
    </button>
  );
}

export function Tabs<T extends string>({ items, value, onChange, label }: { items: [T, string][]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div className="tabs" role="group" aria-label={label}>
      {items.map(([k, l]) => <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)}>{l}</button>)}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

export function Empty({ title, body, action }: { title: string; body?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><h3>{title}</h3>{body && <p>{body}</p>}{action}</div>;
}

export function Skeleton({ h = 18, w = "100%", lines = 1 }: { h?: number; w?: string | number; lines?: number }) {
  return <div className="stack" aria-busy="true">{Array.from({ length: lines }, (_, i) =>
    <div key={i} className="skel" style={{ height: h, width: i === lines - 1 && lines > 1 ? "60%" : w }} />)}</div>;
}

export function ErrorNote({ error }: { error: Error | null | string }) {
  if (!error) return null;
  return <p className="bad-text small pre" role="alert">{typeof error === "string" ? error : error.message}</p>;
}

/** What's missing and the exact step to fix it. */
const SETUP: Record<string, [string, string]> = {
  llm: ["AI isn't configured", "Add ANTHROPIC_API_KEY to .env and restart the API and worker. Planning, drafts, research and replies need it."],
  instagram: ["Instagram isn't connected", "Paste a long-lived Instagram token in Settings to connect @curiox.os."],
  publishing: ["Publishing needs a public URL", "Set PUBLIC_BASE_URL to the https address of this server so Instagram can fetch your media."],
  webhooks: ["Webhooks aren't set up", "Set IG_APP_SECRET and IG_WEBHOOK_VERIFY_TOKEN, then subscribe the app to comments and messages."],
};
export function SetupPrompt({ need }: { need: keyof typeof SETUP }) {
  const [title, body] = SETUP[need];
  return (
    <div className="callout signal">
      <span className="dot signal" style={{ marginTop: 7 }} />
      <div><b>{title}</b><p className="dim small">{body} <a className="link" href="#settings">Open Settings</a></p></div>
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const toast = useToast();
  return <Button size="sm" onClick={() => navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Couldn't copy", "bad"))}>{label}</Button>;
}

export function Drawer({ open, onClose, title, actions, children }: { open: boolean; onClose: () => void; title: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="drawer" onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      {open && <>
        <div className="drawer-head"><h2>{title}</h2><div className="row">{actions}<Button variant="ghost" onClick={onClose} aria-label="Close">Close</Button></div></div>
        <div className="drawer-body">{children}</div>
      </>}
    </dialog>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return <Tabs items={options} value={value} onChange={onChange} label={label} />;
}

export function Meter({ value, max, tone }: { value: number; max: number; tone?: "live" }) {
  return <div className={`meter ${tone ?? ""}`} role="meter" aria-valuenow={value} aria-valuemax={max}><i style={{ width: `${Math.min(100, (value / Math.max(max, 1)) * 100)}%` }} /></div>;
}

// ---------- formatting ----------
export const fmtNum = (n: number | null | undefined) =>
  n == null ? "–" : Intl.NumberFormat(undefined, { notation: Math.abs(n) >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(n);
export const fmtPct = (n: number | null | undefined) => (n == null ? "–" : `${(n * 100).toFixed(1)}%`);
export const fmtDate = (s: string | null | undefined, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" }) =>
  s ? new Date(s).toLocaleString(undefined, opts) : "–";
export function timeAgo(s: string | null | undefined) {
  if (!s) return "never";
  const sec = (Date.now() - new Date(s).getTime()) / 1000;
  const fut = sec < 0;
  const a = Math.abs(sec);
  const v = a < 60 ? "just now" : a < 3600 ? `${Math.round(a / 60)}m` : a < 86400 ? `${Math.round(a / 3600)}h` : `${Math.round(a / 86400)}d`;
  return v === "just now" ? v : fut ? `in ${v}` : `${v} ago`;
}
export const pillarColor = (pillars: string[], name: string) => `var(--p${(Math.max(pillars.indexOf(name), 0) % 4) + 1})`;
export const igProfile = (handle: string) => `https://www.instagram.com/${handle.replace(/^@/, "")}/`;
