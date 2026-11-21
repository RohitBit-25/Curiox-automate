import { useState, type FormEvent } from "react";
import { api, patch, post, type BulkResult, type Creator } from "../api";
import { useAction, useLoad } from "../useLoad";
import { Badge, Button, Empty, ErrorNote, Field, Page, Panel, Skeleton, igProfile, timeAgo, useToast } from "../ui";

const empty = { handle: "", display_name: "", topics: "", audience_note: "" };

export default function Creators() {
  const { data, error, reload } = useLoad(() => api<Creator[]>("/creators"));
  const action = useAction();
  const toast = useToast();
  const [f, setF] = useState(empty);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post("/creators", {
      handle: f.handle, display_name: f.display_name || null, audience_note: f.audience_note || null,
      topics: f.topics.split(",").map((t) => t.trim()).filter(Boolean),
    }), () => { setF(empty); reload(); toast("Creator added", "live"); });
  };

  const [bulk, setBulk] = useState({ handles: "", topics: "" });
  const addBulk = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post<BulkResult>("/creators/bulk", { handles: bulk.handles, topics: bulk.topics.split(",").map((t) => t.trim()).filter(Boolean) }),
      (r) => { setBulk({ handles: "", topics: bulk.topics }); reload(); toast(`Added ${r.added.length}${r.skipped.length ? `, ${r.skipped.length} already in your list` : ""}`, "live"); });
  };

  return (
    <Page title="Creators" subtitle="Accounts in your niche whose audience would like Curiox. Your daily engagement round rotates through them, least recently engaged first.">
      <form className="panel stack" onSubmit={submit} style={{ marginBottom: 16 }}>
        <div className="form-grid">
          <Field label="Handle"><input required value={f.handle} onChange={set("handle")} placeholder="@handle" /></Field>
          <Field label="Name"><input value={f.display_name} onChange={set("display_name")} /></Field>
          <Field label="Topics" hint="Comma separated"><input value={f.topics} onChange={set("topics")} placeholder="ai agents, python" /></Field>
          <Field label="Their audience"><input value={f.audience_note} onChange={set("audience_note")} placeholder="Indie hackers, early-stage founders" /></Field>
        </div>
        <div><Button variant="primary" busy={action.busy}>Add creator</Button></div>
        <ErrorNote error={action.error} />
      </form>
      <form className="panel stack" onSubmit={addBulk} style={{ marginBottom: 16 }}>
        <Field label="Add many at once" hint="Paste @handles or profile links, separated by spaces, commas or new lines. Up to 200.">
          <textarea required rows={3} value={bulk.handles} onChange={(e) => setBulk({ ...bulk, handles: e.target.value })} placeholder="@creator_one, @creator_two&#10;https://www.instagram.com/creator_three/" />
        </Field>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input aria-label="Topics for all" value={bulk.topics} onChange={(e) => setBulk({ ...bulk, topics: e.target.value })} placeholder="Topics for all (optional): react native, indian devs" />
          <Button busy={action.busy}>Add all</Button>
        </div>
      </form>
      <ErrorNote error={error} />
      {!data ? <Skeleton lines={4} /> : data.length === 0 ? <Empty title="No creators yet" body="Add 15 to 30 accounts you'd genuinely enjoy talking with. Quality over size: a 5k-follower builder with an engaged audience beats a 500k page." /> : (
        <Panel className="scroll-x">
          <table>
            <thead><tr><th>Creator</th><th>Topics</th><th>Audience</th><th>Last engaged</th><th>Follow</th><th></th></tr></thead>
            <tbody>{data.map((c) => (
              <tr key={c.id} style={{ opacity: c.active ? 1 : .5 }}>
                <td><a className="link" href={igProfile(c.handle)} target="_blank" rel="noopener noreferrer">@{c.handle}</a>{c.display_name && <div className="xs muted">{c.display_name}</div>}</td>
                <td><div className="row">{c.topics.map((t) => <Badge key={t}>{t}</Badge>)}</div></td>
                <td className="small dim">{c.audience_note}</td>
                <td className="small">{timeAgo(c.last_engaged_at)}</td>
                <td className="small">{!c.followed_at ? <span className="muted">not yet</span> : c.followed_back ? <Badge tone="live">follows back</Badge> : c.followed_back === false ? <span className="muted">no follow-back</span> : "followed"}</td>
                <td><div className="row">
                  <a className="btn sm" href={`#engagement?tab=add&creator=${c.id}`}>Paste a post</a>
                  <Button size="sm" variant="ghost" onClick={() => action.run(() => patch(`/creators/${c.id}`, { active: !c.active }), reload)}>{c.active ? "Pause" : "Resume"}</Button>
                </div></td>
              </tr>
            ))}</tbody>
          </table>
        </Panel>
      )}
    </Page>
  );
}
