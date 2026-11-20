import { Fragment, useEffect, useMemo, useState, type DragEvent } from "react";
import {
  ApiError, api, del, patch, post, upload, type AppSettings, type CaptionLint, type ContentFormat, type ContentItem, type HookScore, type Tells,
} from "../api";
import { useAction, useDebounced, useLoad } from "../useLoad";
import {
  Badge, Button, CopyButton, Drawer, Empty, ErrorNote, Field, Page, Panel, SetupPrompt, Skeleton, Tabs, fmtDate, fmtNum, fmtPct,
  go, pillarColor, useRoute, useStatus, useToast, type Tone,
} from "../ui";

const FORMATS: ContentFormat[] = ["reel", "carousel", "image", "story"];
const NEEDS: Record<ContentFormat, string> = {
  reel: "One MP4 or MOV video", carousel: "2 to 10 JPEG images", image: "One JPEG image", story: "One JPEG image or video",
};
const STATUS_TONE: Record<string, Tone> = { idea: "", drafted: "info", approved: "live", processing: "signal", publishing: "signal", published: "live", failed: "bad" };

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function mondayOf(d = new Date()) { const m = new Date(d); m.setHours(0, 0, 0, 0); m.setDate(m.getDate() - ((m.getDay() + 6) % 7)); return m; }
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const toLocalInput = (s: string | null) => { if (!s) return ""; const d = new Date(s); return `${iso(d)}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };

export default function Content() {
  const route = useRoute();
  const [tab, setTab] = useState<"calendar" | "library">("calendar");
  const [week, setWeek] = useState(mondayOf);
  const settings = useLoad(() => api<AppSettings>("/settings"));
  const pillars = settings.data?.voice.pillars.map((p) => p.name) ?? [];
  const [planning, setPlanning] = useState(false);
  const items = useLoad(() => api<ContentItem[]>(tab === "calendar" ? `/content?week_start=${iso(week)}` : "/content?limit=300"),
    [tab, week], planning ? 4000 : undefined);
  useEffect(() => {  // stop polling once the plan's drafts are in (or after 3 minutes)
    if (!planning) return;
    const t = setTimeout(() => setPlanning(false), 180_000);
    if (items.data?.length && items.data.every((i) => i.status !== "idea")) setPlanning(false);
    return () => clearTimeout(t);
  }, [planning, items.data]);
  const close = () => go("content");
  const openId = route.id ? Number(route.id) : null;

  return (
    <Page title="Content" subtitle="Plan the week, review the drafts, add your media. Approved posts publish themselves at their scheduled time."
      actions={<>
        <Button onClick={() => go("content?new=1")}>New post</Button>
        <Button variant="primary" onClick={() => go("content?plan=1")}>Plan a week</Button>
      </>}>
      <div className="row spread" style={{ marginBottom: 14 }}>
        <Tabs label="View" value={tab} onChange={setTab} items={[["calendar", "Calendar"], ["library", "Library"]]} />
        {tab === "calendar" && (
          <div className="row">
            <Button size="sm" onClick={() => setWeek(addDays(week, -7))} aria-label="Previous week">Previous</Button>
            <Button size="sm" variant="ghost" onClick={() => setWeek(mondayOf())}>This week</Button>
            <Button size="sm" onClick={() => setWeek(addDays(week, 7))} aria-label="Next week">Next</Button>
            <span className="small dim tnum">{week.toLocaleDateString(undefined, { day: "numeric", month: "short" })} to {addDays(week, 6).toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
            <ApproveWeek week={iso(week)} onDone={items.reload} />
          </div>
        )}
      </div>
      {planning && <div className="callout info" style={{ marginBottom: 12 }}><span className="spin" />Planning and drafting your week. This takes a minute or two.</div>}
      <ErrorNote error={items.error} />
      {!items.data ? <Skeleton h={190} /> : tab === "calendar"
        ? <Week week={week} items={items.data} pillars={pillars} />
        : <Library items={items.data} pillars={pillars} />}

      <Editor id={openId} pillars={pillars} onClose={close} onChange={items.reload} />
      <PlanDrawer open={route.query.has("plan")} week={week} onClose={close} onQueued={() => { setPlanning(true); close(); }} />
      <NewDrawer open={route.query.has("new")} pillars={pillars} onClose={close} onCreated={(id) => { items.reload(); go(`content/${id}`); }} />
    </Page>
  );
}

function ApproveWeek({ week, onDone }: { week: string; onDone: () => void }) {
  const action = useAction();
  const toast = useToast();
  return <Button size="sm" busy={action.busy} onClick={() => action.run(
    () => post<{ approved: number[]; skipped: { id: number; reason: string }[] }>("/content/approve-week", { week_start: week }),
    (r) => { toast(`Approved ${r.approved.length}${r.skipped.length ? `, ${r.skipped.length} need fixes` : ""}`, r.skipped.length ? undefined : "live"); onDone(); })}>
    Approve week
  </Button>;
}

function Week({ week, items, pillars }: { week: Date; items: ContentItem[]; pillars: string[] }) {
  const today = iso(new Date());
  if (!items.length) return <Empty title="Nothing planned this week" body="Plan it in one go from your pillars and a short brief of what happened this week, or add a single post."
    action={<div className="row"><a className="btn primary" href="#content?plan=1">Plan this week</a><a className="btn" href="#content?new=1">New post</a></div>} />;
  return (
    <div className="week">
      {Array.from({ length: 7 }, (_, i) => {
        const d = addDays(week, i);
        const dayItems = items.filter((x) => x.scheduled_at && iso(new Date(x.scheduled_at)) === iso(d));
        return (
          <div key={i} className={`day ${iso(d) === today ? "today" : ""}`}>
            <div className="day-head"><span>{d.toLocaleDateString(undefined, { weekday: "short" })}</span><b>{d.getDate()}</b></div>
            {dayItems.map((x) => <PostChip key={x.id} x={x} pillars={pillars} />)}
          </div>
        );
      })}
    </div>
  );
}

function PostChip({ x, pillars }: { x: ContentItem; pillars: string[] }) {
  return (
    <button type="button" className="post-chip" style={{ ["--pc" as string]: pillarColor(pillars, x.pillar) }} onClick={() => go(`content/${x.id}`)}>
      <div className="row spread"><b>{x.format}</b><span className="xs muted tnum">{x.scheduled_at ? new Date(x.scheduled_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : ""}</span></div>
      <span className="angle">{x.angle || x.caption || "Untitled"}</span>
      <div className="row"><Badge tone={STATUS_TONE[x.status]}>{x.status}</Badge>{!x.media.length && x.status !== "published" && <Badge tone="signal">no media</Badge>}</div>
    </button>
  );
}

function Library({ items, pillars }: { items: ContentItem[]; pillars: string[] }) {
  const [status, setStatus] = useState("all");
  const rows = items.filter((i) => status === "all" || i.status === status);
  return (
    <div className="stack">
      <Tabs label="Status" value={status} onChange={setStatus} items={[["all", "All"], ["drafted", "Drafts"], ["approved", "Approved"], ["published", "Published"], ["failed", "Failed"]]} />
      <div className="panel scroll-x">
        <table>
          <thead><tr><th>When</th><th>Post</th><th>Status</th><th className="num">Reach</th><th className="num">Saves</th><th className="num">Shares</th><th className="num">Eng. rate</th></tr></thead>
          <tbody>{rows.map((i) => {
            const m = i.insights ?? {};
            const inter = ["likes", "comments", "saved", "shares"].reduce((n, k) => n + (m[k] ?? 0), 0);
            return (
              <tr key={i.id} onClick={() => go(`content/${i.id}`)} style={{ cursor: "pointer" }}>
                <td className="small tnum">{fmtDate(i.scheduled_at, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</td>
                <td><div className="row"><span className="pill-dot" style={{ background: pillarColor(pillars, i.pillar) }} /><b className="small">{i.format}</b></div><div className="small dim">{(i.angle || i.caption).slice(0, 90)}</div></td>
                <td><Badge tone={STATUS_TONE[i.status]}>{i.status}</Badge></td>
                <td className="num">{fmtNum(m.reach)}</td><td className="num">{fmtNum(m.saved)}</td><td className="num">{fmtNum(m.shares)}</td>
                <td className="num">{m.reach ? fmtPct(inter / m.reach) : "–"}</td>
              </tr>
            );
          })}</tbody>
        </table>
        {!rows.length && <p className="muted small" style={{ padding: 12 }}>No posts here.</p>}
      </div>
    </div>
  );
}

function PlanDrawer({ open, week, onClose, onQueued }: { open: boolean; week: Date; onClose: () => void; onQueued: () => void }) {
  const { status } = useStatus();
  const action = useAction();
  const toast = useToast();
  const [brief, setBrief] = useState("");
  const [count, setCount] = useState(5);
  const [start, setStart] = useState(iso(week));
  useEffect(() => setStart(iso(week)), [week]);
  return (
    <Drawer open={open} onClose={onClose} title="Plan a week">
      {!status?.llm.configured && <SetupPrompt need="llm" />}
      <p className="dim">The AI plans a mix of reels, carousels and images across your pillars, then drafts each one with scored hooks, a caption and hashtags. Nothing publishes until you approve it and add media.</p>
      <Field label="Week starting (Monday)"><input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
      <Field label="What happened this week?" hint="Things you built, numbers, client calls, mistakes, opinions. Real details make posts people save.">
        <textarea rows={7} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="Shipped a WhatsApp order bot for a bakery: 40 orders/day now handled without DMs. Debugged a Celery retry storm. Strong opinion: most 'AI agents' are cron jobs with a prompt." />
      </Field>
      <Field label={`Posts: ${count}`}><input type="range" min={3} max={7} value={count} onChange={(e) => setCount(Number(e.target.value))} /></Field>
      <div className="row">
        <Button variant="primary" busy={action.busy} disabled={!status?.llm.configured} onClick={() => action.run(
          () => post("/content/plan", { week_start: start, brief, count }), () => { toast("Planning your week", "live"); onQueued(); })}>Plan and draft</Button>
      </div>
      <ErrorNote error={action.error} />
    </Drawer>
  );
}

function NewDrawer({ open, pillars, onClose, onCreated }: { open: boolean; pillars: string[]; onClose: () => void; onCreated: (id: number) => void }) {
  const action = useAction();
  const [f, setF] = useState({ format: "reel" as ContentFormat, pillar: "", angle: "", when: "" });
  return (
    <Drawer open={open} onClose={onClose} title="New post">
      <div className="form-grid">
        <Field label="Format"><select value={f.format} onChange={(e) => setF({ ...f, format: e.target.value as ContentFormat })}>{FORMATS.map((x) => <option key={x}>{x}</option>)}</select></Field>
        <Field label="Pillar"><select value={f.pillar || pillars[0]} onChange={(e) => setF({ ...f, pillar: e.target.value })}>{pillars.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Publish at"><input type="datetime-local" value={f.when} onChange={(e) => setF({ ...f, when: e.target.value })} /></Field>
      </div>
      <Field label="Angle" hint="One specific idea, not a topic."><textarea rows={3} value={f.angle} onChange={(e) => setF({ ...f, angle: e.target.value })} /></Field>
      <div><Button variant="primary" busy={action.busy} onClick={() => action.run(() => post<ContentItem>("/content", {
        format: f.format, pillar: f.pillar || pillars[0] || "General", angle: f.angle, scheduled_at: f.when ? new Date(f.when).toISOString() : null,
      }), (i) => onCreated(i.id))}>Create</Button></div>
      <ErrorNote error={action.error} />
    </Drawer>
  );
}

// ---------------------------------------------------------------- editor

type Score = { hook: HookScore | null; caption: CaptionLint | null; tells: Tells | null; hashtags: string[] };

function Editor({ id, pillars, onClose, onChange }: { id: number | null; pillars: string[]; onClose: () => void; onChange: () => void }) {
  const item = useLoad(() => (id ? api<ContentItem>(`/content/${id}`) : Promise.resolve(null)), [id]);
  const x = item.data;
  const { status } = useStatus();
  const toast = useToast();
  const action = useAction();
  const [caption, setCaption] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [meta, setMeta] = useState({ format: "reel" as ContentFormat, pillar: "", angle: "", when: "" });
  const [score, setScore] = useState<Score | null>(null);
  const [ownHook, setOwnHook] = useState("");
  const [ownScore, setOwnScore] = useState<HookScore | null>(null);
  useEffect(() => {
    if (!x) return;
    setCaption(x.caption); setTags(x.hashtags);
    setMeta({ format: x.format, pillar: x.pillar, angle: x.angle, when: toLocalInput(x.scheduled_at) });
  }, [x]);
  const dCaption = useDebounced(caption);
  const dTags = useDebounced(tags.join(","));
  useEffect(() => {
    if (!x || !dCaption) { setScore(null); return; }
    post<Score>("/tools/score", { caption: dCaption, hashtags: dTags ? dTags.split(",").map((t) => ({ tag: t, tier: "niche" })) : [] })
      .then(setScore, () => setScore(null));
  }, [x, dCaption, dTags]);
  const dHook = useDebounced(ownHook);
  useEffect(() => { if (dHook) post<Score>("/tools/score", { hook: dHook }).then((s) => setOwnScore(s.hook), () => {}); else setOwnScore(null); }, [dHook]);

  const editable = x && ["idea", "drafted", "approved", "failed"].includes(x.status);
  const dirty = x && (caption !== x.caption || tags.join() !== x.hashtags.join() || meta.angle !== x.angle || meta.pillar !== x.pillar
    || meta.format !== x.format || meta.when !== toLocalInput(x.scheduled_at));
  const save = () => action.run(() => patch<ContentItem>(`/content/${id}`, {
    caption, hashtags: tags, angle: meta.angle, pillar: meta.pillar, format: meta.format,
    scheduled_at: meta.when ? new Date(meta.when).toISOString() : null,
  }), (r) => { item.setData(r); onChange(); toast("Saved"); });
  const act = (path: string, msg: string) => action.run(async () => { if (dirty) await save(); return post<ContentItem>(`/content/${id}/${path}`); },
    (r) => { item.setData(r); onChange(); toast(msg, "live"); });

  const d = x?.draft;
  const lint = score?.caption ?? d?.caption_lint;
  return (
    <Drawer open={id != null} onClose={onClose} title={x ? `${x.format[0].toUpperCase()}${x.format.slice(1)}, ${x.pillar}` : "Post"}
      actions={x && <Badge tone={STATUS_TONE[x.status]}>{x.status}</Badge>}>
      {!x ? <Skeleton lines={6} /> : <>
        {x.error && <div className="callout bad"><span>{x.status === "failed" ? "Publishing failed" : "Waiting"}: {x.error.replace(/_/g, " ")}</span></div>}
        {x.status === "published" && x.permalink && <div className="callout info"><span>Published {fmtDate(x.published_at)}. <a className="link" href={x.permalink} target="_blank" rel="noopener noreferrer">View on Instagram</a></span></div>}
        {d?.plan_why && <p className="small dim">Why this post: {d.plan_why}</p>}
        {d?.notes && <div className="callout signal"><span><b>Fill in before approving:</b> {d.notes}</span></div>}

        <div className="form-grid">
          <Field label="Format"><select disabled={!editable} value={meta.format} onChange={(e) => setMeta({ ...meta, format: e.target.value as ContentFormat })}>{FORMATS.map((f) => <option key={f}>{f}</option>)}</select></Field>
          <Field label="Pillar"><select disabled={!editable} value={meta.pillar} onChange={(e) => setMeta({ ...meta, pillar: e.target.value })}>{[...new Set([meta.pillar, ...pillars])].map((p) => <option key={p}>{p}</option>)}</select></Field>
          <Field label="Publish at"><input disabled={!editable} type="datetime-local" value={meta.when} onChange={(e) => setMeta({ ...meta, when: e.target.value })} /></Field>
        </div>
        <Field label="Angle"><textarea disabled={!editable} rows={2} value={meta.angle} onChange={(e) => setMeta({ ...meta, angle: e.target.value })} /></Field>

        {(d?.hook_scores?.length ?? 0) > 0 && (
          <Panel title="Hooks, ranked">
            <div className="divide">{d!.hook_scores!.map((h, i) => (
              <div key={i} className="item stack" style={{ gap: 4 }}>
                <div className="row spread"><b>{h.spoken}</b><span className="row"><span className="tnum score">{Math.round(h.score)}</span><Badge tone={h.verdict === "STRONG" ? "live" : h.verdict === "OK" ? "info" : "signal"}>{h.verdict.toLowerCase()}</Badge></span></div>
                <div className="row xs muted"><span>On screen: {h.on_screen}</span>{h.classified && <span>{h.classified.name}</span>}{h.verdict !== "STRONG" && <span>Weakest: {h.weakest.toLowerCase()}</span>}</div>
                {h.flags.map((f) => <p key={f} className="xs bad-text">{f}</p>)}
              </div>
            ))}</div>
            <Field label="Score your own hook"><input value={ownHook} onChange={(e) => setOwnHook(e.target.value)} placeholder="Type a first line…" /></Field>
            {ownScore && <p className="small" style={{ marginTop: 6 }}><b className="tnum">{Math.round(ownScore.score)}</b> {ownScore.verdict.toLowerCase()}, weakest: {ownScore.weakest.toLowerCase()}. {ownScore.flags[0] ?? ""}</p>}
          </Panel>
        )}

        {d?.beats && d.beats.length > 0 && (
          <Panel title="Reel script" actions={<CopyButton text={d.beats.map((b) => `${b.t}  ${b.say}\n  [${b.show}] ${b.on_screen}`).join("\n")} label="Copy script" />}>
            <div className="scroll-x"><table><thead><tr><th>Time</th><th>Say</th><th>Show</th><th>On screen</th></tr></thead>
              <tbody>{d.beats.map((b, i) => <tr key={i}><td className="tnum small">{b.t}</td><td>{b.say}</td><td className="dim small">{b.show}</td><td className="small">{b.on_screen}</td></tr>)}</tbody></table></div>
          </Panel>
        )}
        {d?.slides && d.slides.length > 0 && (
          <Panel title={x.format === "image" ? "Image" : `Slides (${d.slides.length})`} actions={<CopyButton text={d.slides.map((s, i) => `${i + 1}. ${s.headline}\n${s.body}`).join("\n\n")} label="Copy slides" />}>
            <ol className="stack" style={{ paddingLeft: 20, margin: 0 }}>{d.slides.map((s, i) => <li key={i}><b>{s.headline}</b>{s.body && <p className="small dim">{s.body}</p>}</li>)}</ol>
          </Panel>
        )}
        {d?.frames && d.frames.length > 0 && <Panel title="Story frame">{d.frames.map((f, i) => <p key={i}>{f.text}</p>)}</Panel>}

        {x.format !== "story" && (
          <Panel title="Caption">
            <textarea disabled={!editable} rows={7} value={caption} onChange={(e) => setCaption(e.target.value)} />
            {lint && (
              <div className="grid g2" style={{ marginTop: 12 }}>
                <div>
                  <p className="xs muted" style={{ marginBottom: 6 }}>What the feed shows</p>
                  <div className="feed-preview">{lint.visible}{lint.truncated && <em>… more</em>}</div>
                </div>
                <div className="lint">{lint.checks.map((c) => <Fragment key={c.check}><i className={c.status} title={c.status} /><span><b className="small">{c.check.toLowerCase()}</b> <span className="muted">{c.detail}</span></span></Fragment>)}</div>
              </div>
            )}
            {(score?.tells ?? d?.tells) && <p className="xs muted" style={{ marginTop: 8 }}>Reads as human: {Math.round((score?.tells ?? d?.tells)!.human_score)}/100 ({(score?.tells ?? d?.tells)!.verdict.toLowerCase()})</p>}
            <Field label={`Hashtags (${tags.length}/5)`} hint="3 to 5 specific tags: 2 or 3 niche, 1 or 2 mid, at most one broad.">
              <TagInput tags={tags} onChange={setTags} disabled={!editable} />
            </Field>
            {(score?.hashtags ?? d?.hashtag_issues ?? []).length > 0 && <p className="xs muted">{(score?.hashtags ?? d?.hashtag_issues ?? []).map((s) => s.replace(/_/g, " ")).join(", ")}</p>}
          </Panel>
        )}

        <Media x={x} onChange={(r) => { item.setData(r); onChange(); }} />

        <div className="row" style={{ position: "sticky", bottom: 0, padding: "12px 0", background: "var(--bg)", borderTop: "1px solid var(--line)" }}>
          {editable && <Button busy={action.busy} disabled={!dirty} onClick={save}>Save</Button>}
          {x && ["idea", "drafted", "failed"].includes(x.status) && <Button busy={action.busy} disabled={!status?.llm.configured}
            title={status?.llm.configured ? undefined : "Add an AI key in Settings"} onClick={() => act("draft", "Drafted")}>{x.draft?.hook_scores ? "Redraft" : "Draft with AI"}</Button>}
          {x && ["idea", "drafted"].includes(x.status) && <Button variant="primary" busy={action.busy} onClick={() => act("approve", "Approved. It will publish at its scheduled time.")}>Approve</Button>}
          {x?.status === "failed" && x.approved_by && <Button variant="primary" busy={action.busy} onClick={() => act("retry", "Queued to publish again")}>Retry publishing</Button>}
          <span style={{ flex: 1 }} />
          {x && !["processing", "publishing", "published"].includes(x.status) && <Button variant="danger" busy={action.busy}
            onClick={() => { if (confirm("Delete this post?")) action.run(() => del(`/content/${id}`), () => { onChange(); onClose(); toast("Deleted"); }); }}>Delete</Button>}
        </div>
        <ErrorNote error={action.error} />
      </>}
    </Drawer>
  );
}

function TagInput({ tags, onChange, disabled }: { tags: string[]; onChange: (t: string[]) => void; disabled?: boolean }) {
  const [v, setV] = useState("");
  const add = () => { const t = v.trim().replace(/^#/, ""); if (t && !tags.includes(t)) onChange([...tags, t]); setV(""); };
  return (
    <div className="row">
      {tags.map((t) => <span key={t} className="badge">#{t}{!disabled && <button type="button" className="btn ghost sm" style={{ padding: "0 2px" }} aria-label={`Remove ${t}`} onClick={() => onChange(tags.filter((x) => x !== t))}>×</button>}</span>)}
      {!disabled && <input style={{ width: 160 }} value={v} placeholder="add tag" onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }} onBlur={add} />}
    </div>
  );
}

function Media({ x, onChange }: { x: ContentItem; onChange: (r: ContentItem) => void }) {
  const { status } = useStatus();
  const action = useAction();
  const [over, setOver] = useState(false);
  const editable = ["idea", "drafted", "approved", "failed"].includes(x.status);
  const send = (files: FileList | null) => files && action.run(async () => {
    let last = x;
    for (const f of Array.from(files)) {
      try { last = await upload<ContentItem>(`/content/${x.id}/media?kind=${f.type.startsWith("video") ? "video" : "image"}`, f); }
      catch (e) { throw e instanceof ApiError ? new Error(`${f.name}: ${e.message}`) : e; }
    }
    return last;
  }, onChange);
  const drop = (e: DragEvent) => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); };
  const need = useMemo(() => NEEDS[x.format], [x.format]);
  return (
    <Panel title="Media" actions={<span className="xs muted">{need}</span>}>
      {!status?.publishing.configured && <p className="xs muted" style={{ marginBottom: 8 }}>Uploads work now. Publishing needs PUBLIC_BASE_URL set to this server's https address.</p>}
      {x.media.length > 0 && (
        <div className="thumbs" style={{ marginBottom: 10 }}>
          {x.media.map((m) => (
            <div key={m.file} className="thumb">
              {m.kind === "video" ? <video src={m.url} muted playsInline /> : <img src={m.url} alt="" />}
              {editable && <Button size="sm" onClick={() => action.run(() => del<ContentItem>(`/content/${x.id}/media/${m.file}`), onChange)}>Remove</Button>}
            </div>
          ))}
        </div>
      )}
      {editable && (
        <label className={`drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop} style={{ display: "block", cursor: "pointer" }}>
          {action.busy ? <span className="row"><span className="spin" /> Uploading…</span> : "Drop files here or choose files. JPEG for images, MP4 or MOV for video."}
          <input className="sr" type="file" multiple accept="image/jpeg,video/mp4,video/quicktime" onChange={(e) => send(e.target.files)} />
        </label>
      )}
      <ErrorNote error={action.error} />
    </Panel>
  );
}
