import { api, type Overview, type WeekRow } from "../api";
import { useLoad } from "../useLoad";

const COLS = ["discovered", "queued", "approved", "rejected", "published"] as const;

export default function Analytics() {
  const weeks = useLoad(() => api<WeekRow[]>("/analytics/weekly?weeks=8"));
  const ov = useLoad(() => api<Overview>("/analytics/overview"));
  const max = Math.max(1, ...(weeks.data ?? []).map((w) => w.discovered));
  const langs = Object.entries(ov.data?.languages ?? {});
  const langTotal = langs.reduce((n, [, c]) => n + c, 0) || 1;

  return (
    <>
      <h1>Analytics</h1>
      <p className="muted">Measures discovery quality and your review decisions. Instagram follower and profile metrics are not collected yet.</p>
      {weeks.error && <p className="error">{weeks.error}</p>}
      <h2>Weekly</h2>
      <div className="card scroll">
        <table>
          <thead><tr><th>Week</th>{COLS.map((c) => <th key={c}>{c}</th>)}<th style={{ width: "30%" }}>Queued / discovered</th></tr></thead>
          <tbody>{weeks.data?.map((w) => (
            <tr key={w.week}>
              <td>{w.week}</td>{COLS.map((c) => <td key={c}>{w[c]}</td>)}
              <td>
                <div className="bar" style={{ width: `${(w.discovered / max) * 100}%` }} title={`${w.discovered} discovered`} />
                <div className="bar green" style={{ width: `${(w.queued / max) * 100}%`, marginTop: 2 }} title={`${w.queued} queued`} />
              </td>
            </tr>
          ))}</tbody>
        </table>
        {weeks.data?.length === 0 && <p className="muted">No data yet.</p>}
      </div>
      <h2>Languages</h2>
      <div className="card">
        {langs.length === 0 ? <span className="muted">No data yet.</span> : langs.map(([l, n]) => (
          <div key={l} className="row" style={{ marginBottom: 6 }}>
            <span style={{ width: 80 }}>{l}</span>
            <div className="bar" style={{ width: `${(n / langTotal) * 60}%` }} />
            <span className="muted">{n}</span>
          </div>
        ))}
      </div>
    </>
  );
}
