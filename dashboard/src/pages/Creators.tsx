import { useState, type FormEvent } from "react";
import { api, post, type Creator } from "../api";
import { useAction, useLoad } from "../useLoad";

const empty = { handle: "", display_name: "", topics: "", audience_note: "" };

export default function Creators() {
  const { data, error, reload } = useLoad(() => api<Creator[]>("/creators"));
  const action = useAction();
  const [form, setForm] = useState(empty);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post("/creators", {
      handle: form.handle, display_name: form.display_name || null, audience_note: form.audience_note || null,
      topics: form.topics.split(",").map((t) => t.trim()).filter(Boolean),
    }), () => { setForm(empty); reload(); });
  };

  return (
    <>
      <h1>Creator network</h1>
      <form className="card" onSubmit={submit}>
        <div className="grid">
          <div><label htmlFor="h">Handle</label><input id="h" required placeholder="@handle" value={form.handle} onChange={set("handle")} /></div>
          <div><label htmlFor="d">Display name</label><input id="d" value={form.display_name} onChange={set("display_name")} /></div>
          <div><label htmlFor="t">Topics (comma separated)</label><input id="t" placeholder="ai, python, trading" value={form.topics} onChange={set("topics")} /></div>
        </div>
        <label htmlFor="a">Audience note</label>
        <input id="a" placeholder="Who follows them?" value={form.audience_note} onChange={set("audience_note")} />
        <p><button className="primary" disabled={action.busy}>Add creator</button></p>
        {action.error && <p className="error">{action.error}</p>}
      </form>
      {error && <p className="error">{error}</p>}
      <div className="card scroll">
        <table>
          <thead><tr><th>Handle</th><th>Name</th><th>Topics</th><th>Audience</th></tr></thead>
          <tbody>{data?.map((c) => (
            <tr key={c.id}>
              <td><a href={`https://www.instagram.com/${c.handle}/`} target="_blank" rel="noopener noreferrer">@{c.handle}</a></td>
              <td>{c.display_name}</td>
              <td>{c.topics.map((t) => <span key={t} className="chip">{t}</span>)}</td>
              <td className="muted">{c.audience_note}</td>
            </tr>
          ))}</tbody>
        </table>
        {data?.length === 0 && <p className="muted">No creators yet.</p>}
      </div>
    </>
  );
}
