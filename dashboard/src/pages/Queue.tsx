import { useState } from "react";
import { api, post, type Opportunity, type Suggestion } from "../api";
import { useAction, useLoad } from "../useLoad";

const TABS = [["pending", "Pending"], ["approved", "Approved (post manually)"], ["published", "Published"],
  ["rejected", "Rejected"], ["failed", "Failed"]] as const;

export default function Queue() {
  const [tab, setTab] = useState<string>("pending");
  const { data, error, reload } = useLoad(() => api<Opportunity[]>(`/opportunities?status=${tab}`), [tab]);

  return (
    <>
      <h1>Engagement queue</h1>
      <div className="tabs" role="group" aria-label="Status">
        {TABS.map(([k, l]) => <button key={k} aria-pressed={tab === k} onClick={() => setTab(k)}>{l}</button>)}
        <button onClick={reload}>Refresh</button>
      </div>
      {error && <p className="error">{error}</p>}
      {data?.length === 0 && <p className="muted">Nothing here.</p>}
      {data?.map((o) => <Card key={o.id} o={o} onChange={reload} />)}
    </>
  );
}

function Card({ o, onChange }: { o: Opportunity; onChange: () => void }) {
  const action = useAction();
  const who = o.creator ? `@${o.creator.handle}` : o.author_handle ? `@${o.author_handle}` : "unknown author";

  return (
    <article className="card">
      <div className="row spread">
        <div className="row">
          <strong>{who}</strong>
          {o.url && <a href={o.url} target="_blank" rel="noopener noreferrer">open post</a>}
          <span className="chip">{o.source === "own_comment" ? "comment on our post" : "third-party post"}</span>
          {o.language && <span className="chip">{o.language}</span>}
          {o.topics.map((t) => <span className="chip" key={t}>{t}</span>)}
        </div>
        <span title="Relevance score">{o.relevance_score?.toFixed(2) ?? "–"}</span>
      </div>
      {o.parent_text && <blockquote className="muted">On our post: {o.parent_text.slice(0, 200)}</blockquote>}
      <blockquote>{o.text}</blockquote>
      {o.post_summary && <p className="muted">{o.post_summary}</p>}
      {o.needs_human_review && <span className="chip warn">needs careful review</span>}
      <details>
        <summary>Why this comment?</summary>
        <p><strong>Relevance:</strong> {o.relevance_reason}</p>
        <p><strong>Strategy:</strong> {o.response_strategy} · <strong>Tone:</strong> {o.detected_tone}</p>
        {o.factuality_warnings.length > 0 && (
          <><strong>Double-check:</strong><ul>{o.factuality_warnings.map((w) => <li key={w}>{w}</li>)}</ul></>
        )}
      </details>

      {o.status === "pending" ? (
        <>
          {o.suggestions.map((s, i) => <SuggestionEditor key={s.id} n={i + 1} o={o} s={s} onChange={onChange} />)}
          <p className="row">
            <button className="danger" disabled={action.busy} onClick={() => {
              const reason = window.prompt("Why reject? (optional)") ?? null;
              if (reason !== null) action.run(() => post(`/opportunities/${o.id}/reject`, { reason }), onChange);
            }}>Reject all</button>
          </p>
        </>
      ) : o.approval && (
        <div className="suggestion">
          <p className="muted">{o.approval.decision} by {o.approval.actor} · {new Date(o.approval.created_at).toLocaleString()}</p>
          {o.approval.final_text && <blockquote>{o.approval.final_text}</blockquote>}
          {o.approval.reason && <p className="muted">Reason: {o.approval.reason}</p>}
          {o.status === "approved" && o.source === "manual_link" && o.approval.final_text && (
            <div className="row">
              <button onClick={() => navigator.clipboard.writeText(o.approval!.final_text!)}>Copy</button>
              <button className="primary" disabled={action.busy}
                onClick={() => action.run(() => post(`/opportunities/${o.id}/mark-posted`), onChange)}>
                Mark posted
              </button>
            </div>
          )}
        </div>
      )}
      {o.status === "failed" && <p className="error">{o.skip_reason}</p>}
      {action.error && <p className="error">{action.error}</p>}
    </article>
  );
}

function SuggestionEditor({ n, o, s, onChange }: { n: number; o: Opportunity; s: Suggestion; onChange: () => void }) {
  const [text, setText] = useState(s.text);
  const action = useAction();
  const id = `s-${s.id}`;
  const approve = () => action.run(async () => {
    if (text !== s.text) await post(`/opportunities/${o.id}/edit`, { suggestion_id: s.id, text });
    await post(`/opportunities/${o.id}/approve`, { suggestion_id: s.id });
  }, onChange);

  return (
    <div className="suggestion">
      <label htmlFor={id} className="row">
        Suggestion {n} <span className="chip">{s.strategy}</span>
        {s.edited && <span className="chip">edited</span>}
        {s.quality_flags.map((f) => <span key={f} className="chip warn">{f}</span>)}
      </label>
      <textarea id={id} value={text} onChange={(e) => setText(e.target.value)} maxLength={2200} />
      <div className="row spread">
        <span className="muted">{text.length} chars</span>
        <div className="row">
          <button disabled={action.busy || text === s.text}
            onClick={() => action.run(() => post(`/opportunities/${o.id}/edit`, { suggestion_id: s.id, text }), onChange)}>
            Save edit
          </button>
          <button className="primary" disabled={action.busy} onClick={approve}>
            {o.source === "own_comment" ? "Approve & reply" : "Approve"}
          </button>
        </div>
      </div>
      {action.error && <p className="error">{action.error}</p>}
    </div>
  );
}
