import { api, post, type Overview as O } from "../api";
import { useAction, useLoad } from "../useLoad";

export default function Overview() {
  const { data, error, reload } = useLoad(() => api<O>("/analytics/overview"));
  const action = useAction();
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const c = data.counts_by_status;
  const kpis: [string, string | number][] = [
    ["Review queue", c.pending ?? 0],
    ["Approved, not posted", c.approved ?? 0],
    ["Published", data.published],
    ["Approval rate", `${Math.round(data.approval_rate * 100)}%`],
    ["Avg relevance", data.avg_relevance ?? "–"],
    ["Skipped", c.skipped ?? 0],
  ];

  return (
    <>
      <h1>Overview</h1>
      {data.accounts.filter((a) => a.status === "paused").map((a) => (
        <div className="banner row spread" key={a.id} role="alert">
          <span>@{a.handle} is paused: {a.paused_reason}</span>
          <button disabled={action.busy} onClick={() => action.run(() => post(`/accounts/${a.id}/resume`), reload)}>Resume</button>
        </div>
      ))}
      {action.error && <p className="error">{action.error}</p>}
      <div className="grid">
        {kpis.map(([l, n]) => (
          <div className="card kpi" key={l}><div className="n">{n}</div><div className="l">{l}</div></div>
        ))}
      </div>
      <h2>Top skip reasons</h2>
      <div className="card">
        {data.top_skip_reasons.length === 0 ? <span className="muted">Nothing skipped yet.</span> : (
          <table><tbody>{data.top_skip_reasons.map((s) => (
            <tr key={s.reason}><td>{s.reason}</td><td>{s.count}</td></tr>
          ))}</tbody></table>
        )}
      </div>
      <h2>Connected accounts</h2>
      <div className="card">
        {data.accounts.length === 0 ? <span className="muted">No account connected. Add one in <a href="#settings">Settings</a>.</span>
          : data.accounts.map((a) => (
            <div key={a.id} className="row"><strong>@{a.handle}</strong><span className={`chip ${a.status === "active" ? "ok" : "bad"}`}>{a.status}</span></div>
          ))}
      </div>
    </>
  );
}
