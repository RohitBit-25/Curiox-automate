import { useState, type FormEvent } from "react";
import { api, post, type Creator, type EngagementToday, type RecentPost } from "../api";
import { useAction, useLoad } from "../useLoad";
import {
  Badge, Button, Empty, ErrorNote, Field, Meter, Page, Panel, Skeleton, Tabs, go, igProfile, timeAgo, useRoute, useStatus, useToast,
} from "../ui";
import { OpportunityCard, QueueList } from "./Queue";

type Tab = "round" | "queue" | "add";

export default function Engagement() {
  const route = useRoute();
  const tab = (route.query.get("tab") as Tab) || "round";
  const setTab = (t: Tab) => go(`engagement?tab=${t}`);
  return (
    <Page title="Engagement" subtitle="Get noticed in your niche with comments worth reading. The AI drafts them; you post every one yourself.">
      <div style={{ marginBottom: 16 }}>
        <Tabs<Tab> label="Engagement view" value={tab} onChange={setTab}
          items={[["round", "Today's round"], ["queue", "Drafted comments"], ["add", "Add post"]]} />
      </div>
      {tab === "round" && <Round />}
      {tab === "queue" && <QueueList source="manual_link" />}
      {tab === "add" && <AddPost creatorId={route.query.get("creator")} />}
    </Page>
  );
}

function Round() {
  const { data, error, reload } = useLoad(() => api<EngagementToday>("/engagement/today"));
  const action = useAction();
  const toast = useToast();
  const { refresh } = useStatus();
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Skeleton h={90} lines={3} />;
  const done = (id: number) => action.run(() => post(`/engagement/tasks/${id}/done`), () => { reload(); refresh(); });

  return (
    <div className="stack">
      <Panel>
        <div className="row spread" style={{ marginBottom: 8 }}>
          <b>{data.done} of {data.target} done today</b>
          <span className="dim small">{data.streak ? `${data.streak}-day streak` : "Start a streak today"}</span>
        </div>
        <Meter value={data.done} max={data.target} tone="live" />
      </Panel>
      {data.tasks.length === 0 && <Empty title="No one to engage with yet"
        body="Add creators in your niche, or paste posts you'd like to comment on. Each day picks the ones you haven't engaged with for the longest."
        action={<div className="row"><a className="btn primary" href="#creators">Add creators</a><a className="btn" href="#engagement?tab=add">Add a post</a></div>} />}
      {data.tasks.map((t) => t.kind === "opportunity" && t.opportunity ? (
        <div key={t.id} style={{ opacity: t.done_at ? .55 : 1 }}>
          <OpportunityCard o={t.opportunity} onChange={reload} onPosted={() => done(t.id)} />
          {!t.done_at && t.opportunity.status !== "pending" && t.opportunity.status !== "approved" &&
            <Button size="sm" onClick={() => done(t.id)} style={{ marginTop: 6 }}>Mark done</Button>}
        </div>
      ) : t.creator && (
        <Panel key={t.id} className={t.done_at ? "flat" : ""}>
          <div className="row spread">
            <div className="stack" style={{ gap: 4 }}>
              <div className="row"><b>@{t.creator.handle}</b>{t.creator.topics.slice(0, 3).map((x) => <Badge key={x}>{x}</Badge>)}</div>
              <span className="small muted">Last engaged {timeAgo(t.creator.last_engaged_at)}. Open their latest post, leave a comment that adds something, then mark it done.</span>
            </div>
            <div className="row">
              <a className="btn sm" href={igProfile(t.creator.handle)} target="_blank" rel="noopener noreferrer">Open profile</a>
              <a className="btn sm" href={`#engagement?tab=add&creator=${t.creator.id}`}>Draft with AI</a>
              {t.done_at ? <Badge tone="live">done</Badge> : <Button size="sm" variant="primary" busy={action.busy} onClick={() => { done(t.id); toast("Nice. Logged."); }}>Done</Button>}
            </div>
          </div>
        </Panel>
      ))}
      <ErrorNote error={action.error} />
    </div>
  );
}

function AddPost({ creatorId }: { creatorId: string | null }) {
  const creators = useLoad(() => api<Creator[]>("/creators"));
  const recent = useLoad(() => api<RecentPost[]>("/posts?limit=20"));
  const action = useAction();
  const toast = useToast();
  const { status } = useStatus();
  const empty = { url: "", caption: "", creator_id: creatorId ?? "", author_handle: "" };
  const [f, setF] = useState(empty);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post("/posts", {
      url: f.url, caption: f.caption, creator_id: f.creator_id ? Number(f.creator_id) : null, author_handle: f.author_handle || null,
    }), () => { setF({ ...empty, creator_id: "" }); recent.reload(); toast("Added. Drafts appear under Drafted comments in a moment.", "live"); });
  };
  return (
    <div className="grid g2">
      <form className="panel stack" onSubmit={submit}>
        <h3>Paste a post you want to comment on</h3>
        {!status?.llm.configured && <p className="small muted">AI isn't configured yet, so the post is saved and drafted once you add a key.</p>}
        <Field label="Instagram post link"><input required type="url" placeholder="https://www.instagram.com/p/…" value={f.url} onChange={set("url")} /></Field>
        <Field label="Caption" hint="Paste it in. Curiox OS never fetches other people's pages."><textarea required minLength={10} value={f.caption} onChange={set("caption")} /></Field>
        <div className="form-grid">
          <Field label="Creator">
            <select value={f.creator_id} onChange={set("creator_id")}>
              <option value="">Not in my list</option>
              {creators.data?.map((c) => <option key={c.id} value={c.id}>@{c.handle}</option>)}
            </select>
          </Field>
          <Field label="Author handle"><input placeholder="@handle" value={f.author_handle} onChange={set("author_handle")} /></Field>
        </div>
        <div><Button variant="primary" busy={action.busy}>Draft comments</Button></div>
        <ErrorNote error={action.error} />
      </form>
      <Panel title="Recently added">
        {!recent.data ? <Skeleton lines={4} /> : recent.data.length === 0 ? <p className="muted small">Nothing yet.</p> : (
          <div className="divide">
            {recent.data.map((p) => (
              <div key={p.id} className="item stack" style={{ gap: 2 }}>
                <div className="row spread">
                  {p.url ? <a className="link small" href={p.url} target="_blank" rel="noopener noreferrer">{p.author_handle ? `@${p.author_handle}` : `Post ${p.id}`}</a> : <span className="small">@{p.author_handle}</span>}
                  <Badge tone={p.status === "pending" ? "signal" : p.status === "skipped" ? "" : "live"}>{p.status}</Badge>
                </div>
                <span className="xs muted">{p.skip_reason ? `Skipped: ${p.skip_reason.replace(/_/g, " ")}` : p.text.slice(0, 90)}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}
