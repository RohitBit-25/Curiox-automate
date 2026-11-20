import { post, type Today as T } from "../api";
import { Sparkline } from "../Chart";
import { useAction } from "../useLoad";
import {
  Badge, Button, CopyButton, Empty, Meter, Panel, SetupPrompt, Skeleton, Stat, fmtDate, fmtNum, fmtPct, useStatus, useToast,
} from "../ui";

const HOURS = 48;

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

/** The next 48 hours as a tape: one tick per scheduled post, a live "now" marker at the left. */
function Tape({ items }: { items: T["scheduled"] }) {
  const start = Date.now();
  const pos = (t: number) => `${Math.min(100, Math.max(0, ((t - start) / (HOURS * 3600e3)) * 100))}%`;
  const marks = [];
  const first = new Date(start);
  first.setMinutes(0, 0, 0);
  for (let i = 1; i <= HOURS; i++) {
    const t = first.getTime() + i * 3600e3;
    const d = new Date(t);
    if (d.getHours() % 6) continue;
    marks.push(
      <div key={t} className={`hour ${d.getHours() === 0 ? "midnight" : ""}`} style={{ left: pos(t) }}>
        <span>{d.getHours() === 0 ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" }) : `${String(d.getHours()).padStart(2, "0")}:00`}</span>
      </div>,
    );
  }
  return (
    <div className="tape-scroll"><div className="tape" role="img" aria-label={`${items.length} posts scheduled in the next 48 hours`}>
      {marks}
      <div className="now" style={{ left: 0 }} />
      {items.map((i) => {
        const t = new Date(i.scheduled_at).getTime();
        const tone = i.status === "approved" || i.status === "processing" ? (i.has_media ? "" : "warn") : "draft";
        return (
          <a key={i.id} href={`#content/${i.id}`} className={`tick ${tone}`} style={{ left: pos(Math.max(t, start)) }}
            title={`${i.format}: ${i.angle}`}>
            <i /><span>{new Date(t).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })} {i.format}</span>
          </a>
        );
      })}
    </div></div>
  );
}

export default function Today() {
  const { today: t, status, refresh } = useStatus();
  const toast = useToast();
  const action = useAction();

  if (!t) return <><Skeleton h={44} w="50%" /><div style={{ height: 22 }} /><Skeleton h={112} /></>;

  const setup = [
    !status?.llm.configured && "llm", !status?.instagram.connected && "instagram",
    !status?.webhooks.configured && "webhooks", !status?.publishing.configured && "publishing",
  ].filter(Boolean) as ("llm" | "instagram" | "webhooks" | "publishing")[];
  const due = t.approvals.comments + t.approvals.content + t.approvals.dms + t.inbox.needs_reply;
  const k = t.kpis;
  const otherAlerts = t.alerts.filter((a) => !["llm_not_configured", "instagram_not_connected", "webhooks_not_configured", "publishing_not_configured"].includes(a.code));

  return (
    <>
      <h1 className="greeting">{greeting()}.</h1>
      <p>{due ? `${due} thing${due === 1 ? "" : "s"} need you. ` : "Nothing is waiting on you. "}
        {t.scheduled.length ? `${t.scheduled.length} post${t.scheduled.length === 1 ? "" : "s"} in the next 48 hours.` : "Nothing scheduled in the next 48 hours."}</p>
      <Tape items={t.scheduled} />

      {(setup.length > 0 || otherAlerts.length > 0) && (
        <div className="stack" style={{ marginTop: 18 }}>
          {otherAlerts.map((a) => (
            <a key={a.code} href={a.href} className={`callout ${a.level === "bad" ? "bad" : a.level === "warn" ? "signal" : "info"}`} style={{ textDecoration: "none" }}>
              <span className={`dot ${a.level === "bad" ? "bad" : "signal"}`} style={{ marginTop: 7 }} /><span>{a.text}</span>
            </a>
          ))}
          {setup.length > 0 && (
            <details className="panel tight">
              <summary>{setup.length} setup step{setup.length === 1 ? "" : "s"} left before everything runs on its own</summary>
              <div className="stack">{setup.map((s) => <SetupPrompt key={s} need={s} />)}</div>
            </details>
          )}
        </div>
      )}

      <div className="grid g4" style={{ marginTop: 22 }}>
        <Stat label="Followers" value={fmtNum(k.followers)} delta={k.followers_delta_7d}><Sparkline values={k.followers_spark} /></Stat>
        <Stat label="Reach, last 7 days" value={fmtNum(k.reach_7d)} />
        <Stat label="Engagement rate, 7 days" value={fmtPct(k.er_7d)} />
        <Stat label="Engagement streak" value={`${t.engagement.streak} day${t.engagement.streak === 1 ? "" : "s"}`} />
      </div>

      <div className="grid g3" style={{ marginTop: 14 }}>
        <Panel title="Waiting for you">
          <div className="divide">
            {([["Comment replies to review", t.approvals.comments, "#engagement?tab=queue"],
              ["DM conversations", t.inbox.needs_reply, "#inbox"],
              ["Posts to approve", t.approvals.content, "#content"]] as const).map(([l, n, href]) => (
              <a key={l} href={href} className="item row spread" style={{ textDecoration: "none" }}>
                <span className="dim">{l}</span>{n ? <Badge tone="signal">{n}</Badge> : <span className="muted small">none</span>}
              </a>
            ))}
          </div>
        </Panel>

        <Panel title="Engagement round" actions={<a className="btn sm primary" href="#engagement">Start</a>}>
          <p className="dim small" style={{ marginBottom: 10 }}>Comment thoughtfully on posts in your niche. You post them yourself; this list just keeps you consistent.</p>
          <div className="row spread small" style={{ marginBottom: 6 }}><span>{t.engagement.done} of {t.engagement.target} done</span><span className="muted">streak {t.engagement.streak}</span></div>
          <Meter value={t.engagement.done} max={t.engagement.target} tone="live" />
        </Panel>

        <Panel title="Follow-ups due">
          {t.followups_due.length === 0 ? <p className="muted small">No lead follow-ups due today.</p> : (
            <div className="divide">
              {t.followups_due.map((f) => (
                <div key={f.id} className="item stack" style={{ gap: 6 }}>
                  <div className="row spread"><a className="link" href={`#leads/${f.id}`}>@{f.handle}</a><Badge>{f.stage}</Badge></div>
                  {f.text && <p className="small dim pre">{f.text}</p>}
                  <div className="row">
                    {f.text && <CopyButton text={f.text} />}
                    <Button size="sm" busy={action.busy} onClick={() => action.run(() => post(`/leads/${f.id}/followup-sent`),
                      () => { toast("Follow-up marked as sent", "live"); refresh(); })}>Mark sent</Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <div className="section-title"><h2>Scheduled next</h2><a className="btn sm" href="#content">Open calendar</a></div>
      {t.scheduled.length === 0 ? (
        <Empty title="Nothing scheduled in the next 48 hours" body="Plan the week in one go: the AI drafts reels, carousels and captions from your pillars, and you approve them."
          action={<a className="btn primary" href="#content?plan=1">Plan this week</a>} />
      ) : (
        <div className="panel divide">
          {t.scheduled.map((i) => (
            <a key={i.id} href={`#content/${i.id}`} className="item row spread" style={{ textDecoration: "none" }}>
              <div style={{ minWidth: 0 }}>
                <div className="row"><b className="small">{fmtDate(i.scheduled_at, { weekday: "short", hour: "2-digit", minute: "2-digit" })}</b><Badge>{i.format}</Badge><span className="muted small">{i.pillar}</span></div>
                <p className="dim small" style={{ marginTop: 2 }}>{i.angle || "No angle yet"}</p>
              </div>
              {!i.has_media ? <Badge tone="signal">needs media</Badge> : <Badge tone={i.status === "approved" ? "live" : ""}>{i.status}</Badge>}
            </a>
          ))}
        </div>
      )}
    </>
  );
}
