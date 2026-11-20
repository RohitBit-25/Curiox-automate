import { useState, type FormEvent } from "react";
import { api, getToken, post, setToken, type Account, type AppConfig, type AuditRow, type Knowledge } from "../api";
import { useAction, useLoad } from "../useLoad";

export default function Settings() {
  const [token, setTok] = useState(getToken());
  const [saved, setSaved] = useState(false);
  const config = useLoad(() => api<AppConfig>("/config"), [saved]);

  return (
    <>
      <h1>Settings</h1>
      <form className="card" onSubmit={(e) => { e.preventDefault(); setToken(token); setSaved(!saved); }}>
        <label htmlFor="tok">Dashboard token</label>
        <div className="row">
          <input id="tok" type="password" style={{ flex: 1 }} value={token} onChange={(e) => setTok(e.target.value)} />
          <button className="primary">Save</button>
        </div>
        {config.error ? <p className="error">{config.error}</p> : config.data && <p className="chip ok">Connected</p>}
      </form>
      {config.data && <>
        <h2>Safety flags (read only)</h2>
        <div className="card"><pre>{JSON.stringify(config.data, null, 2)}</pre></div>
        <Accounts />
        <KnowledgeSection />
        <Audit />
      </>}
    </>
  );
}

function Accounts() {
  const { data, reload } = useLoad(() => api<Account[]>("/accounts"));
  const action = useAction();
  const [tok, setTok] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post("/accounts", { platform: "instagram", access_token: tok }), () => { setTok(""); reload(); });
  };
  return (
    <>
      <h2>Instagram account</h2>
      <div className="card">
        {data?.map((a) => (
          <div key={a.id} className="row">
            <strong>@{a.handle}</strong>
            <span className={`chip ${a.status === "active" ? "ok" : "bad"}`}>{a.status}</span>
            {a.token_expires_at && <span className="muted">token valid until {new Date(a.token_expires_at).toLocaleDateString()}</span>}
            {a.paused_reason && <span className="error">{a.paused_reason}</span>}
          </div>
        ))}
        <form onSubmit={submit}>
          <label htmlFor="igtok">Long-lived access token (write only; stored encrypted, never shown again)</label>
          <div className="row">
            <input id="igtok" type="password" required style={{ flex: 1 }} autoComplete="off" value={tok} onChange={(e) => setTok(e.target.value)} />
            <button className="primary" disabled={action.busy}>Connect</button>
          </div>
        </form>
        {action.error && <p className="error">{action.error}</p>}
      </div>
    </>
  );
}

const emptyK = { kind: "project", title: "", content: "", tags: "", verified: false };

function KnowledgeSection() {
  const { data, reload } = useLoad(() => api<Knowledge[]>("/knowledge"));
  const action = useAction();
  const [f, setF] = useState(emptyK);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post("/knowledge", { ...f, tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean) }),
      () => { setF(emptyK); reload(); });
  };
  return (
    <>
      <h2>Brand knowledge</h2>
      <p className="muted">What the AI may say about Curiox. Only <em>verified projects</em> can back "we built…" comments.</p>
      <form className="card" onSubmit={submit}>
        <div className="grid">
          <div><label htmlFor="kk">Kind</label>
            <select id="kk" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              {["profile", "project", "fact", "style"].map((k) => <option key={k}>{k}</option>)}
            </select></div>
          <div><label htmlFor="kt">Title</label><input id="kt" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
          <div><label htmlFor="kg">Tags (comma separated)</label><input id="kg" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} /></div>
        </div>
        <label htmlFor="kc">Content</label>
        <textarea id="kc" required value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} />
        <label className="row"><input type="checkbox" checked={f.verified} onChange={(e) => setF({ ...f, verified: e.target.checked })} /> Verified (true and checkable)</label>
        <p><button className="primary" disabled={action.busy}>Add</button></p>
        {action.error && <p className="error">{action.error}</p>}
      </form>
      <div className="card scroll">
        <table><tbody>{data?.map((k) => (
          <tr key={k.id}>
            <td><span className="chip">{k.kind}</span>{k.verified && <span className="chip ok">verified</span>}</td>
            <td><strong>{k.title}</strong><div className="muted">{k.content}</div></td>
            <td>{k.tags.map((t) => <span key={t} className="chip">{t}</span>)}</td>
          </tr>
        ))}</tbody></table>
        {data?.length === 0 && <p className="muted">Add a "profile" entry describing Curiox first.</p>}
      </div>
    </>
  );
}

function Audit() {
  const { data, reload } = useLoad(() => api<AuditRow[]>("/audit?limit=50"));
  return (
    <>
      <h2 className="row spread">Audit log <button onClick={reload}>Refresh</button></h2>
      <div className="card scroll">
        <table>
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead>
          <tbody>{data?.map((a) => (
            <tr key={a.id}>
              <td className="muted">{new Date(a.created_at).toLocaleString()}</td>
              <td>{a.actor}</td><td>{a.action}</td><td>{a.entity} {a.entity_id ?? ""}</td>
              <td><pre>{Object.keys(a.data).length ? JSON.stringify(a.data) : ""}</pre></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}
