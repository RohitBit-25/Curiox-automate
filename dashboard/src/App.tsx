import { useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { api, getToken, type Status, type Today as TodayT } from "./api";
import { useLoad } from "./useLoad";
import { Button, StatusContext, go, useRoute } from "./ui";
import Today from "./pages/Today";
import Content from "./pages/Content";
import Engagement from "./pages/Engagement";
import Inbox from "./pages/Inbox";
import Funnels from "./pages/Funnels";
import Leads from "./pages/Leads";
import Creators from "./pages/Creators";
import Analytics from "./pages/Analytics";
import Brand from "./pages/Brand";
import Settings from "./pages/Settings";

type PageDef = { key: string; label: string; group: string; Comp: ComponentType };
const PAGES: PageDef[] = [
  { key: "today", label: "Today", group: "", Comp: Today },
  { key: "content", label: "Content", group: "Create", Comp: Content },
  { key: "engagement", label: "Engagement", group: "Engage", Comp: Engagement },
  { key: "inbox", label: "Inbox", group: "Engage", Comp: Inbox },
  { key: "funnels", label: "Keyword DMs", group: "Engage", Comp: Funnels },
  { key: "leads", label: "Leads", group: "Grow", Comp: Leads },
  { key: "creators", label: "Creators", group: "Grow", Comp: Creators },
  { key: "analytics", label: "Analytics", group: "Grow", Comp: Analytics },
  { key: "brand", label: "Brand & voice", group: "System", Comp: Brand },
  { key: "settings", label: "Settings", group: "System", Comp: Settings },
];

type Theme = "dark" | "light";
const storedTheme = (): Theme => {
  try { const t = localStorage.getItem("curiox_theme"); if (t === "dark" || t === "light") return t; } catch { /* */ }
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
};

export default function App() {
  const route = useRoute();
  const authed = !!getToken();
  const page = PAGES.find((p) => p.key === route.page) ?? (authed ? PAGES[0] : PAGES[PAGES.length - 1]);
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const [navOpen, setNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const status = useLoad(() => api<Status>("/status"), [route.page, authed]);
  const today = useLoad(() => api<TodayT>("/today"), [route.page, authed], 60_000);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("curiox_theme", theme); } catch { /* */ }
  }, [theme]);
  useEffect(() => setNavOpen(false), [route.page]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaletteOpen((o) => !o); }
    };
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, []);

  const badges: Record<string, number> = {
    inbox: today.data?.inbox.needs_reply ?? 0,
    engagement: today.data ? today.data.approvals.comments : 0,
    content: today.data?.approvals.content ?? 0,
  };
  const ig = status.data?.instagram;
  const paused = status.data?.automation_paused;
  const modes = today.data?.modes;
  const ctx = useMemo(() => ({
    status: status.data, today: today.data, refresh: () => { status.reload(); today.reload(); },
  }), [status, today]);

  let lastGroup: string | null = null;
  return (
    <StatusContext.Provider value={ctx}>
      <div className="shell">
        <aside className={`side ${navOpen ? "open" : ""}`} aria-label="Main">
          <div className="brand"><b>CURIOX</b><span>OS</span></div>
          <div className="acct" title={ig?.paused_reason ?? undefined}>
            <span className={`dot ${!ig?.connected ? "" : ig.active ? "live" : "bad"}`} />
            {ig?.connected ? `@${ig.handle}` : "Instagram not connected"}
          </div>
          <nav className="nav">
            {PAGES.map((p) => {
              const head = p.group !== lastGroup && p.group ? <div className="nav-group" key={`g-${p.group}`}>{p.group}</div> : null;
              lastGroup = p.group;
              return [head, (
                <a key={p.key} href={`#${p.key}`} aria-current={p.key === page.key ? "page" : undefined}>
                  {p.label}{badges[p.key] ? <span className="count">{badges[p.key]}</span> : null}
                </a>
              )];
            })}
          </nav>
          <div className="side-foot">
            <div className="row xs dim">
              <span className={`dot ${paused ? "bad" : modes && (modes.comments === "auto" || modes.dms === "auto") ? "live" : "signal"}`} />
              {paused ? "Automation paused" : modes ? `Replies: comments ${modes.comments}, DMs ${modes.dms}` : "Loading"}
            </div>
            <div className="row">
              <Button size="sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
                {theme === "dark" ? "Light theme" : "Dark theme"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setPaletteOpen(true)}><kbd>⌘K</kbd></Button>
            </div>
          </div>
        </aside>
        {navOpen && <div className="scrim" onClick={() => setNavOpen(false)} aria-hidden />}
        <div className="main">
          <header className="topbar">
            <Button className="menu" size="sm" onClick={() => setNavOpen((o) => !o)} aria-expanded={navOpen}>Menu</Button>
            <span className="dim small">{page.group ? `${page.group} / ` : ""}{page.label}</span>
            <span className="spacer" />
            {!authed && <span className="badge signal">Add your dashboard token in Settings</span>}
          </header>
          <main className="content" key={page.key}>
            <page.Comp />
          </main>
        </div>
      </div>
      <Palette open={paletteOpen} onClose={() => setPaletteOpen(false)} toggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")} />
    </StatusContext.Provider>
  );
}

function Palette({ open, onClose, toggleTheme }: { open: boolean; onClose: () => void; toggleTheme: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const actions: [string, string, () => void][] = [
    ...PAGES.map((p): [string, string, () => void] => [p.label, "Go to", () => go(p.key)]),
    ["Plan this week", "Content", () => go("content?plan=1")],
    ["New post", "Content", () => go("content?new=1")],
    ["Add a post to comment on", "Engagement", () => go("engagement?tab=add")],
    ["Add lead", "Leads", () => go("leads?new=1")],
    ["New keyword DM", "Keyword DMs", () => go("funnels?new=1")],
    ["Switch theme", "Appearance", toggleTheme],
  ];
  const list = actions.filter(([l]) => l.toLowerCase().includes(q.toLowerCase()));
  useEffect(() => {
    const d = ref.current!;
    if (open && !d.open) { setQ(""); setSel(0); d.showModal(); }
    if (!open && d.open) d.close();
  }, [open]);
  const pick = (i: number) => { list[i]?.[2](); onClose(); };
  return (
    <dialog ref={ref} className="palette" onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      <input autoFocus placeholder="Jump to a page or action" value={q} aria-label="Command"
        onChange={(e) => { setQ(e.target.value); setSel(0); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, list.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
          if (e.key === "Enter") pick(sel);
        }} />
      <ul role="listbox">
        {list.map(([l, g], i) => (
          <li key={l} role="option" aria-selected={i === sel} onMouseEnter={() => setSel(i)} onClick={() => pick(i)}>
            <span>{l}</span><span className="muted small">{g}</span>
          </li>
        ))}
        {!list.length && <li className="muted">No match</li>}
      </ul>
    </dialog>
  );
}
