import { useEffect, useState, type FormEvent } from "react";
import { api, patch, post, type AppSettings, type Knowledge, type Voice } from "../api";
import { useAction, useLoad } from "../useLoad";
import { Badge, Button, ErrorNote, Field, Page, Panel, Skeleton, pillarColor, useToast } from "../ui";

const lines = (xs: string[]) => xs.join("\n");
const unlines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

export default function Brand() {
  const settings = useLoad(() => api<AppSettings>("/settings"));
  return (
    <Page title="Brand & voice" subtitle="Everything the AI writes starts here: who Curiox is, what it sells, how it sounds, and the facts it's allowed to claim.">
      {!settings.data ? <Skeleton lines={8} /> : <VoiceForm initial={settings.data.voice} onSaved={settings.setData} />}
      <KnowledgeSection />
    </Page>
  );
}

function VoiceForm({ initial, onSaved }: { initial: Voice; onSaved: (s: AppSettings) => void }) {
  const [v, setV] = useState(initial);
  const [txt, setTxt] = useState({ always: lines(initial.always), never: lines(initial.never), examples: initial.examples.join("\n---\n"),
    best_times: lines(initial.best_times), languages: initial.languages.join(", ") });
  useEffect(() => setV(initial), [initial]);
  const action = useAction();
  const toast = useToast();
  const total = v.pillars.reduce((n, p) => n + (Number(p.share) || 0), 0);
  const names = v.pillars.map((p) => p.name);
  const save = () => action.run(() => patch<AppSettings>("/settings", { voice: {
    ...v, always: unlines(txt.always), never: unlines(txt.never), best_times: unlines(txt.best_times),
    examples: txt.examples.split(/\n-{3,}\n/).map((x) => x.trim()).filter(Boolean),
    languages: txt.languages.split(",").map((x) => x.trim()).filter(Boolean),
  } }), (s) => { onSaved(s); toast("Voice saved. New drafts use it right away.", "live"); });
  const setPillar = (i: number, k: "name" | "description" | "share", val: string) =>
    setV({ ...v, pillars: v.pillars.map((p, j) => (j === i ? { ...p, [k]: k === "share" ? Number(val) : val } : p)) });

  return (
    <div className="stack">
      <Panel title="Who Curiox is">
        <div className="stack">
          <div className="form-grid">
            <Field label="Instagram handle"><input value={v.handle} onChange={(e) => setV({ ...v, handle: e.target.value })} /></Field>
            <Field label="Main link" hint="Used in DMs, never in captions."><input value={v.cta_link} onChange={(e) => setV({ ...v, cta_link: e.target.value })} placeholder="https://curiox.dev" /></Field>
            <Field label="Languages" hint="First one is the default: en, hinglish, hi"><input value={txt.languages} onChange={(e) => setTxt({ ...txt, languages: e.target.value })} /></Field>
          </div>
          <Field label="Who we are"><textarea rows={2} value={v.who} onChange={(e) => setV({ ...v, who: e.target.value })} placeholder="A developer building AI automations and trading tools in public, from India." /></Field>
          <Field label="Who it's for"><textarea rows={2} value={v.audience} onChange={(e) => setV({ ...v, audience: e.target.value })} placeholder="Developers learning AI, indie builders, and small-business owners drowning in DMs and spreadsheets." /></Field>
          <Field label="What Curiox sells" hint="The lead audit and offers use this."><textarea rows={2} value={v.offer} onChange={(e) => setV({ ...v, offer: e.target.value })} placeholder="Custom automations for small businesses: order bots, booking flows, CRM in Google Sheets, AI replies." /></Field>
        </div>
      </Panel>

      <Panel title="Content pillars" actions={<span className={`small tnum ${total === 100 ? "muted" : "bad-text"}`}>{total}% of posts</span>}>
        <div className="stack">
          {v.pillars.map((p, i) => (
            <div key={i} className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
              <span className="pill-dot" style={{ background: pillarColor(names, p.name), marginTop: 14 }} />
              <input aria-label="Pillar name" style={{ maxWidth: 240 }} value={p.name} onChange={(e) => setPillar(i, "name", e.target.value)} />
              <input aria-label="Description" value={p.description} onChange={(e) => setPillar(i, "description", e.target.value)} placeholder="What posts in this pillar cover" />
              <input aria-label="Share percent" type="number" min={0} max={100} style={{ width: 80 }} value={p.share} onChange={(e) => setPillar(i, "share", e.target.value)} />
              <Button size="sm" variant="ghost" aria-label={`Remove ${p.name}`} disabled={v.pillars.length < 2} onClick={() => setV({ ...v, pillars: v.pillars.filter((_, j) => j !== i) })}>Remove</Button>
            </div>
          ))}
          <div><Button size="sm" disabled={v.pillars.length >= 8} onClick={() => setV({ ...v, pillars: [...v.pillars, { name: "New pillar", description: "", share: 0 }] })}>Add pillar</Button></div>
        </div>
      </Panel>

      <div className="grid g2">
        <Panel title="Always"><textarea rows={5} value={txt.always} onChange={(e) => setTxt({ ...txt, always: e.target.value })} placeholder={"One rule per line\nShow the code or the screen, not just talk"} /></Panel>
        <Panel title="Never"><textarea rows={5} value={txt.never} onChange={(e) => setTxt({ ...txt, never: e.target.value })} placeholder={"One rule per line\nGive specific stock tips"} /></Panel>
      </div>
      <Panel title="Sounds like" actions={<span className="xs muted">Paste 2 or 3 of your best captions, separated by a line with ---</span>}>
        <textarea rows={6} value={txt.examples} onChange={(e) => setTxt({ ...txt, examples: e.target.value })} />
      </Panel>
      <Panel title="Posting windows" actions={<span className="xs muted">One per line, local time, e.g. Tue 19:30</span>}>
        <textarea rows={4} value={txt.best_times} onChange={(e) => setTxt({ ...txt, best_times: e.target.value })} />
      </Panel>
      <div className="row"><Button variant="primary" busy={action.busy} onClick={save}>Save voice</Button></div>
      <ErrorNote error={action.error} />
    </div>
  );
}

const blankK = { kind: "project", title: "", content: "", tags: "", verified: false };

function KnowledgeSection() {
  const { data, reload } = useLoad(() => api<Knowledge[]>("/knowledge"));
  const action = useAction();
  const [f, setF] = useState(blankK);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post("/knowledge", { ...f, tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean) }), () => { setF(blankK); reload(); });
  };
  return (
    <>
      <div className="section-title"><h2>Facts the AI can use</h2></div>
      <p className="dim small" style={{ marginBottom: 12 }}>Only entries marked verified can back a "we built this" line. Anything else the AI will refuse to claim.</p>
      <form className="panel stack" onSubmit={submit}>
        <div className="form-grid">
          <Field label="Kind"><select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            <option value="profile">About Curiox</option><option value="project">Project</option><option value="fact">Fact</option><option value="style">Style note</option>
          </select></Field>
          <Field label="Title"><input required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Bakery order bot" /></Field>
          <Field label="Tags"><input value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} placeholder="whatsapp, automation" /></Field>
        </div>
        <Field label="Details"><textarea required rows={3} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} placeholder="Built a WhatsApp ordering flow for a local bakery. Handles about 40 orders a day; cut DM replies by roughly 3 hours daily." /></Field>
        <label className="row small"><input type="checkbox" checked={f.verified} onChange={(e) => setF({ ...f, verified: e.target.checked })} /> Verified: true and I can back it up</label>
        <div><Button variant="primary" busy={action.busy}>Add</Button></div>
        <ErrorNote error={action.error} />
      </form>
      {data && data.length > 0 && (
        <div className="panel divide" style={{ marginTop: 12 }}>
          {data.map((k) => (
            <div key={k.id} className="item stack" style={{ gap: 4 }}>
              <div className="row"><b className="small">{k.title}</b><Badge>{k.kind}</Badge>{k.verified && <Badge tone="live">verified</Badge>}{k.tags.map((t) => <Badge key={t}>{t}</Badge>)}</div>
              <p className="small dim">{k.content}</p>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
