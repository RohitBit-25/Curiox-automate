import { useEffect, useState } from "react";
import { ApiError, api, del, patch, post, type AppSettings, type FunnelRule, type FunnelSend, type IgMedia } from "../api";
import { useAction, useLoad } from "../useLoad";
import { Badge, Button, Drawer, Empty, ErrorNote, Field, Page, Panel, SetupPrompt, Skeleton, go, timeAgo, useRoute, useStatus, useToast } from "../ui";

type Draft = Omit<FunnelRule, "id" | "sent_7d" | "sent_total" | "failed_total">;
const blank: Draft = { name: "", keywords: [], media_id: null, dm_text: "", public_replies: [], active: true };

export default function Funnels() {
  const route = useRoute();
  const rules = useLoad(() => api<FunnelRule[]>("/funnels"));
  const settings = useLoad(() => api<AppSettings>("/settings"));
  const { status } = useStatus();
  const editing = route.query.has("new") ? "new" : route.id ? Number(route.id) : null;

  return (
    <Page title="Keyword DMs" subtitle="When someone comments a keyword on your post, they get a DM from you automatically. It's the cleanest way to turn a comment into a conversation."
      actions={<Button variant="primary" onClick={() => go("funnels?new=1")}>New keyword DM</Button>}>
      <div className="callout info" style={{ marginBottom: 16 }}>
        <span className="small">Only fires on comments on <b>your</b> posts, at most one DM per person per keyword, within 7 days of the comment,
          and no more than {settings.data?.cap_funnel_dms_per_hour ?? 60} an hour. Put "comment GUIDE" in a caption to use one.</span>
      </div>
      {!status?.instagram.connected && <div style={{ marginBottom: 12 }}><SetupPrompt need="instagram" /></div>}
      {!status?.webhooks.configured && <div style={{ marginBottom: 12 }}><SetupPrompt need="webhooks" /></div>}
      <ErrorNote error={rules.error} />
      {!rules.data ? <Skeleton h={120} lines={2} /> : rules.data.length === 0 ? (
        <Empty title="No keyword DMs yet" body='Example: keyword "GUIDE" sends your free checklist link. Mention it at the end of a reel caption and watch the comments turn into DMs.'
          action={<Button variant="primary" onClick={() => go("funnels?new=1")}>Create the first one</Button>} />
      ) : (
        <div className="grid g2">{rules.data.map((r) => <RuleCard key={r.id} r={r} onChange={rules.reload} />)}</div>
      )}
      <Editor id={editing} rules={rules.data ?? []} onClose={() => go("funnels")} onSaved={rules.reload} />
    </Page>
  );
}

function RuleCard({ r, onChange }: { r: FunnelRule; onChange: () => void }) {
  const action = useAction();
  const sends = useLoad(() => api<FunnelSend[]>(`/funnels/${r.id}/sends?limit=5`), [r.id]);
  return (
    <Panel title={r.name} actions={<>
      <Badge tone={r.active ? "live" : ""}>{r.active ? "on" : "off"}</Badge>
      <Button size="sm" onClick={() => go(`funnels/${r.id}`)}>Edit</Button>
    </>}>
      <div className="stack" style={{ gap: 10 }}>
        <div className="row">{r.keywords.map((k) => <Badge key={k} tone="signal">{k}</Badge>)}<span className="xs muted">{r.media_id ? "on one post" : "on all posts"}</span></div>
        <blockquote className="quote small">{r.dm_text}</blockquote>
        <div className="row small dim tnum"><span>{r.sent_7d} sent this week</span><span>{r.sent_total} total</span>{r.failed_total > 0 && <span className="bad-text">{r.failed_total} failed</span>}</div>
        {sends.data && sends.data.length > 0 && (
          <details><summary className="small">Recent sends</summary>
            <div className="divide small">{sends.data.map((s) => (
              <div key={s.id} className="item row spread"><span className="dim">user …{s.commenter_id.slice(-4)}</span>
                <span className="row"><Badge tone={s.status === "sent" ? "live" : s.status === "failed" ? "bad" : ""}>{s.status}</Badge><span className="xs muted">{timeAgo(s.created_at)}</span></span></div>
            ))}</div>
          </details>
        )}
        <div className="row">
          <Button size="sm" busy={action.busy} onClick={() => action.run(
            () => r.active ? del(`/funnels/${r.id}`) : patch(`/funnels/${r.id}`, { ...r, active: true }), onChange)}>{r.active ? "Turn off" : "Turn on"}</Button>
        </div>
        <ErrorNote error={action.error} />
      </div>
    </Panel>
  );
}

function Editor({ id, rules, onClose, onSaved }: { id: number | "new" | null; rules: FunnelRule[]; onClose: () => void; onSaved: () => void }) {
  const existing = typeof id === "number" ? rules.find((r) => r.id === id) : undefined;
  const [f, setF] = useState<Draft>(blank);
  const [kw, setKw] = useState("");
  const [variants, setVariants] = useState("");
  const [pick, setPick] = useState(false);
  const action = useAction();
  const toast = useToast();
  useEffect(() => {
    const src = existing ?? blank;
    setF(src); setKw(src.keywords.join(", ")); setVariants(src.public_replies.join("\n")); setPick(false);
  }, [id, existing]);
  const bytes = new TextEncoder().encode(f.dm_text).length;
  const save = () => action.run(() => {
    const body = { ...f, keywords: kw.split(",").map((k) => k.trim()).filter(Boolean),
      public_replies: variants.split("\n").map((v) => v.trim()).filter(Boolean) };
    return existing ? patch(`/funnels/${existing.id}`, body) : post("/funnels", body);
  }, () => { toast(existing ? "Keyword DM updated" : "Keyword DM is live", "live"); onSaved(); onClose(); });

  return (
    <Drawer open={id != null} onClose={onClose} title={existing ? `Edit ${existing.name}` : "New keyword DM"}>
      <Field label="Name" hint="Only you see this."><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Celery checklist" /></Field>
      <Field label="Keywords" hint="Comma separated. Whole words only, so LINK won't match linkedin."><input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="GUIDE, checklist" /></Field>
      <Field label="Which posts">
        <div className="row">
          <span className="small">{f.media_id ? `One post (${f.media_id})` : "All my posts"}</span>
          <Button size="sm" onClick={() => setPick(!pick)}>{pick ? "Close picker" : "Choose a post"}</Button>
          {f.media_id && <Button size="sm" variant="ghost" onClick={() => setF({ ...f, media_id: null })}>Use all posts</Button>}
        </div>
      </Field>
      {pick && <MediaPicker onPick={(m) => { setF({ ...f, media_id: m }); setPick(false); }} />}
      <Field label="DM they receive" hint={<span className={bytes > 1000 ? "bad-text" : ""}>{bytes}/1000 bytes. Include the link you promised.</span>}>
        <textarea rows={5} value={f.dm_text} onChange={(e) => setF({ ...f, dm_text: e.target.value })} placeholder="Here's the Celery retry checklist I mentioned: https://…  Reply here if you get stuck." />
      </Field>
      <Field label="Public reply under their comment (optional)" hint="One per line. Rotated so they don't repeat.">
        <textarea rows={3} value={variants} onChange={(e) => setVariants(e.target.value)} placeholder={"Sent, check your DMs\nJust sent it over"} />
      </Field>
      <label className="row small"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> On</label>
      <div><Button variant="primary" busy={action.busy} disabled={!f.name || !kw.trim() || !f.dm_text || bytes > 1000} onClick={save}>{existing ? "Save" : "Turn on"}</Button></div>
      <ErrorNote error={action.error} />
    </Drawer>
  );
}

function MediaPicker({ onPick }: { onPick: (id: string) => void }) {
  const media = useLoad(() => api<IgMedia[]>("/instagram/media"));
  if (media.error) return media.error instanceof ApiError && media.error.code === "instagram_not_connected"
    ? <SetupPrompt need="instagram" /> : <ErrorNote error={media.error} />;
  if (!media.data) return <Skeleton h={120} />;
  return (
    <div className="thumbs">
      {media.data.map((m) => (
        <button key={m.id} type="button" className="thumb" style={{ padding: 0, cursor: "pointer" }} onClick={() => onPick(m.id)} title={m.caption?.slice(0, 100)}>
          {m.thumbnail_url || m.media_url ? <img src={m.thumbnail_url || m.media_url} alt={m.caption?.slice(0, 60) ?? "post"} /> : <span className="xs">{m.caption?.slice(0, 40)}</span>}
        </button>
      ))}
    </div>
  );
}
