import { useEffect, useState } from "react";
import Overview from "./pages/Overview";
import Discover from "./pages/Discover";
import Queue from "./pages/Queue";
import Creators from "./pages/Creators";
import Analytics from "./pages/Analytics";
import Settings from "./pages/Settings";
import { getToken } from "./api";

const PAGES = {
  overview: ["Overview", Overview],
  discover: ["Discover", Discover],
  queue: ["Engagement queue", Queue],
  creators: ["Creator network", Creators],
  analytics: ["Analytics", Analytics],
  settings: ["Settings", Settings],
} as const;
type Page = keyof typeof PAGES;

const fromHash = (): Page => {
  const h = location.hash.slice(1) as Page;
  if (h in PAGES) return h;
  return getToken() ? "overview" : "settings";
};

export default function App() {
  const [page, setPage] = useState<Page>(fromHash);
  useEffect(() => {
    const onHash = () => setPage(fromHash());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  const [, Current] = PAGES[page];

  return (
    <>
      <header className="top">
        <div className="brand">CURIOX <span>/ growth</span></div>
        <nav>
          {(Object.keys(PAGES) as Page[]).map((p) => (
            <a key={p} href={`#${p}`} aria-current={p === page ? "page" : undefined}>{PAGES[p][0]}</a>
          ))}
        </nav>
      </header>
      <main>
        <Current />
      </main>
    </>
  );
}
