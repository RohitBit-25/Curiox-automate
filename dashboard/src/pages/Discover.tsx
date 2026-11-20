import { useState, type FormEvent } from "react";
import { api, post, type Creator, type RecentPost } from "../api";
import { useAction, useLoad } from "../useLoad";

export default function Discover() {
  const creators = useLoad(() => api<Creator[]>("/creators"));
  const recent = useLoad(() => api<RecentPost[]>("/posts?limit=30"));
  const action = useAction();
  const [form, setForm] = useState({ url: "", caption: "", creator_id: "", author_handle: "", published_at: "" });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post("/posts", {
      url: form.url, caption: form.caption,
      creator_id: form.creator_id ? Number(form.creator_id) : null,
      author_handle: form.author_handle || null,
      published_at: form.published_at ? new Date(form.published_at).toISOString() : null,
    }), () => { setForm({ url: "", caption: "", creator_id: "", author_handle: "", published_at: "" }); recent.reload(); });
  };

  return (
    <>
      <h1>Discover</h1>
      <form className="card" onSubmit={submit}>
        <label htmlFor="url">Instagram post link</label>
        <input id="url" required type="url" placeholder="https://www.instagram.com/p/…" value={form.url} onChange={set("url")} />
        <label htmlFor="caption">Caption <span className="muted">(paste it; third-party pages are never fetched)</span></label>
        <textarea id="caption" required minLength={10} value={form.caption} onChange={set("caption")} />
        <div className="grid">
          <div>
            <label htmlFor="creator">Curated creator</label>
            <select id="creator" value={form.creator_id} onChange={set("creator_id")}>
              <option value="">Not in list</option>
              {creators.data?.map((c) => <option key={c.id} value={c.id}>@{c.handle}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="author">Author handle</label>
            <input id="author" placeholder="@handle" value={form.author_handle} onChange={set("author_handle")} />
          </div>
          <div>
            <label htmlFor="date">Posted on</label>
            <input id="date" type="date" value={form.published_at} onChange={set("published_at")} />
          </div>
        </div>
        <p><button className="primary" disabled={action.busy}>Analyze post</button></p>
        {action.error && <p className="error">{action.error}</p>}
      </form>

      <h2>Recently submitted</h2>
      {recent.error && <p className="error">{recent.error}</p>}
      <div className="card scroll">
        <table>
          <thead><tr><th>Post</th><th>Status</th><th>Score</th><th>Reason</th></tr></thead>
          <tbody>{recent.data?.map((p) => (
            <tr key={p.id}>
              <td>{p.url ? <a href={p.url} target="_blank" rel="noopener noreferrer">{p.author_handle ? `@${p.author_handle}` : `#${p.id}`}</a> : `@${p.author_handle}`}
                <div className="muted">{p.text.slice(0, 90)}</div></td>
              <td><span className="chip">{p.status}</span></td>
              <td>{p.relevance_score?.toFixed(2) ?? "–"}</td>
              <td className="muted">{p.skip_reason ?? ""}</td>
            </tr>
          ))}</tbody>
        </table>
        <p><button onClick={recent.reload}>Refresh</button></p>
      </div>
    </>
  );
}
