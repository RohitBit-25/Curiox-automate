import { useEffect, useState, type DragEvent } from "react";
import { api, del, patch, post, type Lead, type LeadStage } from "../api";
import { useAction, useLoad } from "../useLoad";
import {
  Badge, Button, CopyButton, Drawer, ErrorNote, Field, Page, Panel, SetupPrompt, Skeleton, fmtDate, go, igProfile, useRoute, useStatus, useToast,
} from "../ui";

const STAGES: [LeadStage, string][] = [["new", "New"], ["researched", "Researched"], ["contacted", "Contacted"], ["replied", "Replied"],
  ["call", "Call booked"], ["won", "Won"], ["lost", "Lost"]];
const scoreTone = (n: number | null) => (n == null ? "" : n >= 70 ? "live" : n >= 45 ? "info" : "signal");

export default function Leads() {
  const route = useRoute();
  const leads = useLoad(() => api<Lead[]>("/leads"));
  const [over, setOver] = useState<LeadStage | null>(null);
  const action = useAction();
  const move = (id: number, stage: LeadStage) => action.run(() => post(`/leads/${id}/stage`, { stage }), leads.reload);
  const drop = (stage: LeadStage) => (e: DragEvent) => { e.preventDefault(); setOver(null); const id = Number(e.dataTransfer.getData("text/plain")); if (id) move(id, stage); };

  return (
    <Page title="Leads" subtitle="Small businesses you could help. The AI audits their Instagram from what you paste and drafts a personal first message. You send it yourself."
      actions={<Button variant="primary" onClick={() => go("leads?new=1")}>Add lead</Button>}>
      <ErrorNote error={leads.error || action.error} />
      {!leads.data ? <Skeleton h={260} /> : (
        <div className="kanban">
          {STAGES.map(([stage, label]) => {
            const xs = leads.data!.filter((l) => l.stage === stage);
            return (
              <section key={stage} className={`lane ${over === stage ? "over" : ""}`} aria-label={label}
                onDragOver={(e) => { e.preventDefault(); setOver(stage); }} onDragLeave={() => setOver(null)} onDrop={drop(stage)}>
                <div className="lane-head"><span>{label}</span><span className="tnum">{xs.length}</span></div>
                {xs.map((l) => (
                  <button key={l.id} type="button" className="lead-card" draggable onDragStart={(e) => e.dataTransfer.setData("text/plain", String(l.id))}
                    onClick={() => go(`leads/${l.id}`)}>
                    <div className="row spread"><b className="small">@{l.handle}</b>{l.rubric_total != null && <Badge tone={scoreTone(l.rubric_total)}>{l.rubric_total}</Badge>}</div>
                    {l.niche && <span className="xs muted">{l.niche}</span>}
                    {l.next_followup_at && <span className={`xs ${new Date(l.next_followup_at) < new Date() ? "bad-text" : "muted"}`}>Follow up {fmtDate(l.next_followup_at, { day: "numeric", month: "short" })}</span>}
                  </button>
                ))}
                {stage === "new" && xs.length === 0 && <p className="xs muted" style={{ padding: 4 }}>Add a business you'd like to help.</p>}
              </section>
            );
          })}
        </div>
      )}
      <NewLead open={route.query.has("new")} onClose={() => go("leads")} onCreated={(id) => { leads.reload(); go(`leads/${id}`); }} />
      <LeadDrawer id={route.id ? Number(route.id) : null} onClose={() => go("leads")} onChange={leads.reload} />
    </Page>
  );
}

const blankLead = { handle: "", name: "", niche: "", website: "", notes: "", profile_text: "" };

function NewLead({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: number) => void }) {
  const [f, setF] = useState(blankLead);
  const action = useAction();
  useEffect(() => { if (open) setF(blankLead); }, [open]);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <Drawer open={open} onClose={onClose} title="Add lead">
      <LeadFields f={f} set={set} />
      <div><Button variant="primary" busy={action.busy} disabled={!f.handle.trim()} onClick={() => action.run(
        () => post<Lead>("/leads", Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v || null]))), (l) => onCreated(l.id))}>Add lead</Button></div>
      <ErrorNote error={action.error} />
    </Drawer>
  );
}

function LeadFields({ f, set, lockHandle }: { f: typeof blankLead; set: (k: keyof typeof blankLead) => (e: { target: { value: string } }) => void; lockHandle?: boolean }) {
  return <>
    <div className="form-grid">
      <Field label="Instagram handle"><input value={f.handle} onChange={set("handle")} placeholder="@sweetcrumbs.bakery" disabled={lockHandle} /></Field>
      <Field label="Owner or business name"><input value={f.name} onChange={set("name")} /></Field>
      <Field label="Niche"><input value={f.niche} onChange={set("niche")} placeholder="bakery, salon, gym…" /></Field>
      <Field label="Website"><input value={f.website} onChange={set("website")} /></Field>
    </div>
    <Field label="Their profile, pasted" hint="Bio, highlights, a few recent captions and comments. The audit only uses what you paste here.">
      <textarea rows={8} value={f.profile_text} onChange={set("profile_text")} placeholder={"Bio: Fresh sourdough daily 🍞 DM to order!\nPinned: Christmas orders open, DM us\nComment on last post: 'how do I order?' (no reply)"} />
    </Field>
    <Field label="Your notes"><textarea rows={2} value={f.notes} onChange={set("notes")} /></Field>
  </>;
}

function LeadDrawer({ id, onClose, onChange }: { id: number | null; onClose: () => void; onChange: () => void }) {
  const lead = useLoad(() => (id ? api<Lead>(`/leads/${id}`) : Promise.resolve(null)), [id]);
  const { status } = useStatus();
  const action = useAction();
  const toast = useToast();
  const l = lead.data;
  const [f, setF] = useState(blankLead);
  const [pitch, setPitch] = useState("");
  useEffect(() => {
    if (!l) return;
    setF({ handle: l.handle, name: l.name ?? "", niche: l.niche ?? "", website: l.website ?? "", notes: l.notes ?? "", profile_text: l.profile_text ?? "" });
    setPitch(l.pitch ?? "");
  }, [l]);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const done = (msg?: string) => (r: Lead) => { lead.setData(r); onChange(); if (msg) toast(msg, "live"); };
  const dirty = l && (f.name !== (l.name ?? "") || f.niche !== (l.niche ?? "") || f.website !== (l.website ?? "") || f.notes !== (l.notes ?? "")
    || f.profile_text !== (l.profile_text ?? "") || pitch !== (l.pitch ?? ""));
  const save = () => action.run(() => patch<Lead>(`/leads/${id}`, { name: f.name, niche: f.niche, website: f.website, notes: f.notes, profile_text: f.profile_text, pitch }), done("Saved"));
  const r = l?.research;

  return (
    <Drawer open={id != null} onClose={onClose} title={l ? `@${l.handle}` : "Lead"} actions={l && (
      <select aria-label="Stage" value={l.stage} style={{ width: 150 }} onChange={(e) => action.run(() => post<Lead>(`/leads/${l.id}/stage`, { stage: e.target.value }), done())}>
        {STAGES.map(([s, label]) => <option key={s} value={s}>{label}</option>)}
      </select>)}>
      {!l ? <Skeleton lines={6} /> : <>
        <div className="row">
          <a className="btn sm" href={igProfile(l.handle)} target="_blank" rel="noopener noreferrer">Open profile</a>
          {l.conversation_id && <a className="btn sm" href={`#inbox/${l.conversation_id}`}>Open DM thread</a>}
          {l.rubric_total != null && <Badge tone={scoreTone(l.rubric_total)}>Profile score {l.rubric_total}/100</Badge>}
        </div>

        {!status?.llm.configured && <SetupPrompt need="llm" />}
        {r ? (
          <>
            <Panel title="Audit" actions={<Badge>confidence {Math.round(r.confidence * 100)}%</Badge>}>
              <p className="dim">{r.summary}</p>
              {r.insufficient_info && <p className="small bad-text" style={{ marginTop: 6 }}>Not much to go on. Paste more of their profile and run it again.</p>}
              {r.problems.length > 0 && <div className="divide" style={{ marginTop: 10 }}>{r.problems.map((p, i) => (
                <div key={i} className="item stack" style={{ gap: 4 }}>
                  <div className="row"><b className="small">{p.problem}</b><Badge tone={p.impact === "high" ? "signal" : p.impact === "medium" ? "info" : ""}>{p.impact} impact</Badge></div>
                  <p className="xs muted">Evidence: {p.evidence}</p>
                  <p className="small">What we'd build: {p.curiox_solution}</p>
                </div>
              ))}</div>}
              <p className="small" style={{ marginTop: 10 }}><b>Open with:</b> {r.best_angle}</p>
            </Panel>
            <details className="panel tight"><summary className="small">Profile rubric, 12 checks</summary>
              <div className="bars" style={{ marginTop: 8 }}>{r.rubric.map((x) => (
                <div key={x.id} style={{ display: "contents" }}>
                  <span className="small dim">{x.id.replace(/_/g, " ")}</span>
                  <div className="track"><i style={{ width: `${Math.min(100, x.score * 8.33)}%` }} /></div>
                  <span className="xs muted" title={x.note}>{x.score}</span>
                </div>
              ))}</div>
            </details>
          </>
        ) : <p className="dim small">Paste their bio and a few captions below, then research. The AI scores their profile on 12 checks, finds a problem you could solve, and drafts the message.</p>}

        <Panel title="First message" actions={pitch && <CopyButton text={pitch} />}>
          <textarea rows={5} value={pitch} onChange={(e) => setPitch(e.target.value)} placeholder="Run the research to draft one, or write your own." />
          <div className="row" style={{ marginTop: 8 }}>
            {pitch && ["new", "researched"].includes(l.stage) && <Button size="sm" variant="primary" busy={action.busy}
              onClick={() => action.run(() => post<Lead>(`/leads/${l.id}/contacted`), done("Marked as contacted. Follow-up reminder in 3 days."))}>I sent it</Button>}
            {l.contacted_at && <span className="xs muted">Contacted {fmtDate(l.contacted_at, { dateStyle: "medium" })}</span>}
          </div>
        </Panel>

        {l.followups.length > 0 && (
          <Panel title="Follow-ups">
            <div className="divide">{l.followups.map((t, i) => (
              <div key={i} className="item stack" style={{ gap: 6 }}>
                <p className="small pre">{t}</p>
                <div className="row"><CopyButton text={t} />{i < l.followups_sent ? <Badge tone="live">sent</Badge>
                  : i === l.followups_sent && l.stage === "contacted" && <Button size="sm" busy={action.busy} onClick={() => action.run(() => post<Lead>(`/leads/${l.id}/followup-sent`), done("Follow-up logged"))}>Mark sent</Button>}</div>
              </div>
            ))}</div>
          </Panel>
        )}

        <LeadFields f={f} set={set} lockHandle />
        <div className="row">
          <Button busy={action.busy} disabled={!dirty} onClick={save}>Save</Button>
          <Button variant="primary" busy={action.busy} disabled={!status?.llm.configured} onClick={() => action.run(async () => {
            if (dirty) await patch(`/leads/${id}`, { name: f.name, niche: f.niche, website: f.website, notes: f.notes, profile_text: f.profile_text, pitch });
            return post<Lead>(`/leads/${id}/research`);
          }, done("Research done"))}>{r ? "Research again" : "Research with AI"}</Button>
          <span style={{ flex: 1 }} />
          <Button variant="danger" busy={action.busy} onClick={() => { if (confirm(`Delete @${l.handle}?`)) action.run(() => del(`/leads/${l.id}`), () => { onChange(); onClose(); }); }}>Delete</Button>
        </div>
        <ErrorNote error={action.error} />
      </>}
    </Drawer>
  );
}
