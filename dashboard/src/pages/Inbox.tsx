import { useEffect, useState } from "react";
import { api, post, type Conversation, type Lead } from "../api";
import { useAction, useLoad } from "../useLoad";
import { Badge, Button, Empty, ErrorNote, Page, SetupPrompt, Skeleton, Tabs, go, timeAgo, useRoute, useStatus, useToast } from "../ui";
import { QueueList } from "./Queue";

export default function Inbox() {
  const route = useRoute();
  const tab = route.query.get("tab") === "comments" ? "comments" : "dms";
  return (
    <Page title="Inbox" subtitle="Messages people send you and comments on your posts. Replies go out only inside Instagram's 24-hour window.">
      <div style={{ marginBottom: 14 }}>
        <Tabs label="Inbox view" value={tab} onChange={(t) => go(t === "dms" ? "inbox" : "inbox?tab=comments")}
          items={[["dms", "Direct messages"], ["comments", "Comments on your posts"]]} />
      </div>
      {tab === "dms" ? <Dms selected={route.id ? Number(route.id) : null} /> : <QueueList source="own_comment" />}
    </Page>
  );
}

const name = (c: Conversation) => (c.username ? `@${c.username}` : `Instagram user …${c.igsid.slice(-4)}`);
function windowLeft(c: Conversation) {
  if (!c.last_inbound_at) return null;
  const h = 24 - (Date.now() - new Date(c.last_inbound_at).getTime()) / 3600e3;
  return h > 0 ? `${Math.floor(h)}h left to reply` : null;
}

function Dms({ selected }: { selected: number | null }) {
  const { status } = useStatus();
  const [filter, setFilter] = useState<"needs_reply" | "all">("all");
  const list = useLoad(() => api<Conversation[]>(`/inbox/conversations?filter=${filter}`), [filter], 30_000);
  if (!list.data) return <Skeleton h={300} />;
  return (
    <div className="stack">
      {!status?.webhooks.configured && <SetupPrompt need="webhooks" />}
      <div className={`inbox ${selected ? "has-thread" : ""}`}>
        <div className="conv-list">
          <div style={{ padding: 10, borderBottom: "1px solid var(--line)" }}>
            <Tabs label="Filter" value={filter} onChange={setFilter} items={[["all", "All"], ["needs_reply", "Needs reply"]]} />
          </div>
          {list.data.length === 0 && <p className="muted small" style={{ padding: 16 }}>No conversations yet. When someone messages @curiox.os, it shows up here with a drafted reply.</p>}
          {list.data.map((c) => (
            <button key={c.id} type="button" className="conv" aria-current={c.id === selected} onClick={() => go(`inbox/${c.id}`)}>
              <div className="row spread">
                <span className="row"><b className="small">{name(c)}</b>{c.needs_reply && <span className="dot signal" />}</span>
                <span className="xs muted">{timeAgo(c.last_message_at)}</span>
              </div>
              <p>{c.preview}</p>
              <div className="row" style={{ marginTop: 4 }}>
                {c.lead && <Badge tone="info">lead: {c.lead.stage}</Badge>}
                {c.draft_id && <Badge tone="signal">draft ready</Badge>}
                {!c.window_open && <Badge>window closed</Badge>}
              </div>
            </button>
          ))}
        </div>
        <div className="thread">
          {selected ? <Thread id={selected} onChange={list.reload} /> : <div style={{ padding: 24 }}><Empty title="Pick a conversation" body="Drafted replies are waiting in the ones marked with a dot." /></div>}
        </div>
      </div>
    </div>
  );
}

function Thread({ id, onChange }: { id: number; onChange: () => void }) {
  const conv = useLoad(() => api<Conversation>(`/inbox/conversations/${id}`), [id], 20_000);
  const leads = useLoad(() => api<Lead[]>("/leads"));
  const { status, refresh } = useStatus();
  const action = useAction();
  const toast = useToast();
  const c = conv.data;
  const draft = c?.messages?.find((m) => m.id === c.draft_id);
  const [text, setText] = useState("");
  useEffect(() => setText(draft?.text ?? ""), [draft?.id, draft?.text]);
  if (!c) return <div style={{ padding: 16 }}><Skeleton lines={4} /></div>;
  const bytes = new TextEncoder().encode(text).length;
  const done = (msg: string) => () => { conv.reload(); onChange(); refresh(); toast(msg, "live"); };
  const send = () => action.run(() => draft
    ? post(`/inbox/messages/${draft.id}/send`, { text })
    : post(`/inbox/conversations/${c.id}/reply`, { text }), done("Sent"));
  const left = windowLeft(c);

  return (
    <>
      <div className="thread-head">
        <div className="row">
          <Button size="sm" variant="ghost" onClick={() => go("inbox")} className="menu-back">Back</Button>
          <b>{name(c)}</b>
          {left ? <Badge tone="live">{left}</Badge> : <Badge>window closed</Badge>}
        </div>
        <div className="row">
          {c.lead ? <a className="badge info" href={`#leads/${c.lead.id}`} style={{ textDecoration: "none" }}>Lead @{c.lead.handle}: {c.lead.stage}</a> : (
            <select aria-label="Link to lead" style={{ width: 180 }} value="" onChange={(e) => action.run(() => post(`/inbox/conversations/${c.id}/lead`,
              e.target.value === "new" ? { create: true } : { lead_id: Number(e.target.value) }), done("Linked to lead"))}>
              <option value="" disabled>Link to a lead…</option>
              {c.username && <option value="new">Create lead @{c.username}</option>}
              {leads.data?.map((l) => <option key={l.id} value={l.id}>@{l.handle}</option>)}
            </select>
          )}
        </div>
      </div>
      <div className="bubbles">
        {c.messages?.map((m) => m.status === "draft" ? null : (
          <div key={m.id} className={`bubble ${m.direction}`}>
            {m.text || <i className="muted">(attachment)</i>}
            <small>{timeAgo(m.created_at)}{m.direction === "out" && ` · ${m.actor === "auto" ? "auto-reply" : m.actor === "instagram_app" ? "sent from Instagram" : m.status}`}{m.error && ` · ${m.error}`}</small>
          </div>
        ))}
      </div>
      <div className="composer">
        {draft?.meta?.lead_signal && <div className="callout info small"><span><b>Looks like a lead.</b> {draft.meta.lead_summary}</span></div>}
        {draft?.meta && <p className="xs muted">Draft: {draft.meta.category}, confidence {Math.round((draft.meta.confidence ?? 0) * 100)}%
          {draft.meta.blocked && ` · held for review (${draft.meta.blocked.replace(/_/g, " ")})`}{draft.meta.needs_human_reason && ` · ${draft.meta.needs_human_reason}`}</p>}
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} disabled={!c.window_open}
          placeholder={c.window_open ? "Write a reply" : "Instagram only allows replies within 24 hours of their last message."} aria-label="Reply" />
        <div className="row spread">
          <span className={`xs tnum ${bytes > 1000 ? "bad-text" : "muted"}`}>{bytes}/1000 bytes</span>
          <div className="row">
            {draft && <Button size="sm" variant="ghost" busy={action.busy} onClick={() => action.run(() => post(`/inbox/messages/${draft.id}/discard`), done("Draft discarded"))}>Discard</Button>}
            <Button size="sm" busy={action.busy} disabled={!status?.llm.configured || !c.window_open}
              onClick={() => action.run(() => post(`/inbox/conversations/${c.id}/suggest`), done("New draft ready"))}>{draft ? "Redraft" : "Draft with AI"}</Button>
            <Button size="sm" variant="primary" busy={action.busy} disabled={!text.trim() || bytes > 1000 || !c.window_open} onClick={send}>Send</Button>
          </div>
        </div>
        <ErrorNote error={action.error} />
      </div>
    </>
  );
}
