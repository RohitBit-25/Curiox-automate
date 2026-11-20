import { useState } from "react";
import { api, post, type Opportunity, type Suggestion } from "../api";
import { useAction, useLoad } from "../useLoad";
import { Badge, Button, CopyButton, Empty, ErrorNote, Skeleton, Tabs, useStatus, useToast } from "../ui";

const TABS: [string, string][] = [["pending", "To review"], ["approved", "Approved"], ["published", "Posted"],
  ["funnel", "Keyword DMs"], ["skipped", "Skipped"], ["rejected", "Rejected"], ["failed", "Failed"]];

/** Review list for one source: third-party posts (manual_link) or comments on our own posts (own_comment). */
export function QueueList({ source }: { source: "manual_link" | "own_comment" }) {
  const tabs = source === "own_comment" ? TABS : TABS.filter(([k]) => k !== "funnel");
  const [tab, setTab] = useState("pending");
  const { data, error, loading, reload } = useLoad(() => api<Opportunity[]>(`/opportunities?status=${tab}&source=${source}`), [tab, source]);
  return (
    <div className="stack">
      <div className="row spread">
        <Tabs items={tabs} value={tab} onChange={setTab} label="Status" />
        <Button size="sm" variant="ghost" onClick={reload}>Refresh</Button>
      </div>
      <ErrorNote error={error} />
      {loading && !data && <Skeleton h={120} lines={2} />}
      {data?.length === 0 && <Empty title={tab === "pending" ? "Nothing to review" : "Nothing here yet"}
        body={source === "manual_link" && tab === "pending" ? "Paste a post from your niche under Add post and the AI drafts comments for you to choose from." : undefined} />}
      {data?.map((o) => <OpportunityCard key={o.id} o={o} onChange={reload} />)}
    </div>
  );
}

export function OpportunityCard({ o, onChange, onPosted }: { o: Opportunity; onChange: () => void; onPosted?: () => void }) {
  const action = useAction();
  const toast = useToast();
  const { refresh } = useStatus();
  const who = o.creator ? `@${o.creator.handle}` : o.author_handle ? `@${o.author_handle}` : "Unknown author";
  const done = () => { onChange(); refresh(); };

  return (
    <article className="panel stack">
      <div className="row spread">
        <div className="row">
          <b>{who}</b>
          {o.url && <a className="link small" href={o.url} target="_blank" rel="noopener noreferrer">Open post</a>}
          <Badge>{o.source === "own_comment" ? "comment on your post" : "post in your niche"}</Badge>
          {o.language && <Badge>{o.language}</Badge>}
          {o.topics.slice(0, 3).map((t) => <Badge key={t}>{t}</Badge>)}
        </div>
        {o.relevance_score != null && <span className="tnum small dim" title="Relevance score">{Math.round(o.relevance_score * 100)}% match</span>}
      </div>
      {o.parent_text && <p className="xs muted">On your post: {o.parent_text.slice(0, 160)}</p>}
      <blockquote className="quote">{o.text}</blockquote>
      {o.post_summary && <p className="small dim">{o.post_summary}</p>}
      {o.auto && <p className="xs muted">Auto-reply check: {o.auto.blocked ? `held for review (${o.auto.blocked.replace(/_/g, " ")})` : "passed"}. {o.auto.reason}</p>}
      {o.skip_reason && <p className="xs muted">Skipped: {o.skip_reason.replace(/_/g, " ")}</p>}

      {(o.relevance_reason || o.factuality_warnings.length > 0) && (
        <details>
          <summary className="small">Why this comment?</summary>
          <div className="stack small dim" style={{ gap: 6 }}>
            {o.relevance_reason && <p>{o.relevance_reason}</p>}
            {o.response_strategy && <p>Approach: {o.response_strategy}. Tone: {o.detected_tone}.</p>}
            {o.factuality_warnings.length > 0 && <p className="bad-text">Check before posting: {o.factuality_warnings.join("; ")}</p>}
          </div>
        </details>
      )}

      {o.status === "pending" && (
        <>
          {o.needs_human_review && <Badge tone="signal">read carefully before approving</Badge>}
          {o.suggestions.map((s, i) => <SuggestionEditor key={s.id} n={i + 1} o={o} s={s} onDone={done} />)}
          <div className="row">
            <Button variant="danger" size="sm" busy={action.busy} onClick={() => {
              const reason = window.prompt("Why reject? (optional)");
              if (reason !== null) action.run(() => post(`/opportunities/${o.id}/reject`, { reason }), () => { toast("Rejected"); done(); });
            }}>Reject all</Button>
          </div>
        </>
      )}

      {o.approval && o.status !== "pending" && (
        <div className="stack" style={{ gap: 6 }}>
          <p className="xs muted">{o.approval.decision} by {o.approval.actor === "auto" ? "auto-reply" : o.approval.actor} · {new Date(o.approval.created_at).toLocaleString()}</p>
          {o.approval.final_text && <blockquote className="quote">{o.approval.final_text}</blockquote>}
          {o.status === "approved" && o.source === "manual_link" && o.approval.final_text && (
            <div className="row">
              <CopyButton text={o.approval.final_text} label="Copy comment" />
              {o.url && <a className="btn sm" href={o.url} target="_blank" rel="noopener noreferrer">Open post</a>}
              <Button size="sm" variant="primary" busy={action.busy} onClick={() => action.run(
                () => post(`/opportunities/${o.id}/mark-posted`), () => { toast("Marked as posted", "live"); onPosted?.(); done(); })}>
                I posted it
              </Button>
            </div>
          )}
        </div>
      )}
      {o.status === "failed" && <p className="bad-text small">{o.skip_reason}</p>}
      <ErrorNote error={action.error} />
    </article>
  );
}

function SuggestionEditor({ n, o, s, onDone }: { n: number; o: Opportunity; s: Suggestion; onDone: () => void }) {
  const [text, setText] = useState(s.text);
  const action = useAction();
  const toast = useToast();
  const id = `s-${s.id}`;
  const approve = () => action.run(async () => {
    if (text !== s.text) await post(`/opportunities/${o.id}/edit`, { suggestion_id: s.id, text });
    await post(`/opportunities/${o.id}/approve`, { suggestion_id: s.id });
  }, () => { toast(o.source === "own_comment" ? "Approved. The reply will post in a moment." : "Approved. Copy it and post it yourself.", "live"); onDone(); });

  return (
    <div className="stack" style={{ gap: 6, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
      <label htmlFor={id} className="row small">
        <b>Option {n}</b><Badge>{s.strategy}</Badge>{s.edited && <Badge>edited</Badge>}
        {s.quality_flags.map((f) => <Badge key={f} tone="signal">{f.replace(/_/g, " ")}</Badge>)}
      </label>
      <textarea id={id} value={text} onChange={(e) => setText(e.target.value)} maxLength={2200} rows={2} />
      <div className="row spread">
        <span className="xs muted tnum">{text.length} characters</span>
        <div className="row">
          <Button size="sm" disabled={text === s.text} busy={action.busy}
            onClick={() => action.run(() => post(`/opportunities/${o.id}/edit`, { suggestion_id: s.id, text }), () => { toast("Saved"); onDone(); })}>Save edit</Button>
          <Button size="sm" variant="primary" busy={action.busy} onClick={approve}>{o.source === "own_comment" ? "Approve and reply" : "Approve"}</Button>
        </div>
      </div>
      <ErrorNote error={action.error} />
    </div>
  );
}
