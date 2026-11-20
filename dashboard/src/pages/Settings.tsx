import { useState, type FormEvent } from "react";
import { api, getToken, patch, post, setToken, type Account, type AppSettings, type AuditRow, type Mode } from "../api";
import { useAction, useLoad } from "../useLoad";
import { Badge, Button, CopyButton, ErrorNote, Field, Page, Panel, Segmented, Skeleton, fmtDate, useStatus, useToast } from "../ui";

export default function Settings() {
  const { status, refresh } = useStatus();
  const [token, setTok] = useState(getToken());
  const toast = useToast();
  const authed = !!status;

  return (
    <Page title="Settings" subtitle="Connections, automation, and the audit log of everything the system and you have done.">
      <form className="panel" onSubmit={(e) => { e.preventDefault(); setToken(token); refresh(); toast("Token saved"); location.reload(); }}>
        <Field label="Dashboard token" hint="From DASHBOARD_USERS in .env. Stored in this browser only.">
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input type="password" value={token} onChange={(e) => setTok(e.target.value)} autoComplete="current-password" />
            <Button variant="primary">Save</Button>
          </div>
        </Field>
        {authed && <p className="small" style={{ marginTop: 8 }}><span className="dot live" style={{ display: "inline-block", marginRight: 6 }} />Connected</p>}
      </form>
      {authed && <>
        <Integrations />
        <AccountSection />
        <Automation />
        <Audit />
      </>}
    </Page>
  );
}

function Integrations() {
  const { status: s } = useStatus();
  if (!s) return null;
  const webhook = `${location.origin}/api/webhooks/instagram`;
  const rows: [string, boolean, string][] = [
    ["AI (drafts, planning, research)", s.llm.configured, s.llm.configured ? `${s.llm.provider}, ${s.llm.model}` : "Set ANTHROPIC_API_KEY in .env and restart."],
    ["Instagram account", s.instagram.connected && s.instagram.active, s.instagram.connected ? (s.instagram.active ? `@${s.instagram.handle}` : `Paused: ${s.instagram.paused_reason}`) : "Connect below."],
    ["Webhooks (DMs, comments, keyword DMs)", s.webhooks.configured, s.webhooks.configured ? "Configured" : "Set IG_APP_SECRET and IG_WEBHOOK_VERIFY_TOKEN."],
    ["Publishing", s.publishing.configured, s.publishing.configured ? "PUBLIC_BASE_URL is set" : "Set PUBLIC_BASE_URL to this server's https address."],
  ];
  return (
    <>
      <div className="section-title"><h2>Connections</h2></div>
      <div className="panel divide">
        {rows.map(([l, ok, detail]) => (
          <div key={l} className="item row spread">
            <div><b className="small">{l}</b><p className="xs muted">{detail}</p></div>
            <Badge tone={ok ? "live" : "signal"}>{ok ? "ready" : "to do"}</Badge>
          </div>
        ))}
        <div className="item stack" style={{ gap: 6 }}>
          <b className="small">Meta app setup</b>
          <p className="xs muted">Permissions: instagram_business_basic, instagram_business_manage_comments, instagram_business_content_publish, instagram_business_manage_messages, instagram_business_manage_insights. Subscribe the webhook to <b>comments</b> and <b>messages</b>. The app must be Live with Advanced Access for comment webhooks.</p>
          <div className="row"><code className="small">{webhook}</code><CopyButton text={webhook} label="Copy webhook URL" /></div>
        </div>
      </div>
    </>
  );
}

function AccountSection() {
  const { refresh } = useStatus();
  const { data, reload } = useLoad(() => api<Account[]>("/accounts"));
  const action = useAction();
  const toast = useToast();
  const [tok, setTok] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    action.run(() => post<Account>("/accounts", { platform: "instagram", access_token: tok }), (a) => { setTok(""); reload(); refresh(); toast(`Connected @${a.handle}`, "live"); });
  };
  return (
    <>
      <div className="section-title"><h2>Instagram account</h2></div>
      <Panel>
        {data?.map((a) => (
          <div key={a.id} className="row spread" style={{ marginBottom: 12 }}>
            <div className="row"><b>@{a.handle}</b><Badge tone={a.status === "active" ? "live" : "bad"}>{a.status}</Badge>
              {a.token_expires_at && <span className="xs muted">token renews automatically, valid until {fmtDate(a.token_expires_at, { dateStyle: "medium" })}</span>}</div>
            {a.status !== "active" && <Button size="sm" busy={action.busy} onClick={() => action.run(() => post(`/accounts/${a.id}/resume`), () => { reload(); refresh(); })}>Resume</Button>}
          </div>
        ))}
        {data?.some((a) => a.paused_reason) && <p className="small bad-text" style={{ marginBottom: 10 }}>{data.find((a) => a.paused_reason)?.paused_reason}</p>}
        <form onSubmit={submit}>
          <Field label={data?.length ? "Replace token" : "Long-lived access token"} hint="Stored encrypted. It is never shown again or sent to this browser.">
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input type="password" required autoComplete="off" value={tok} onChange={(e) => setTok(e.target.value)} />
              <Button variant="primary" busy={action.busy}>Connect</Button>
            </div>
          </Field>
        </form>
        <ErrorNote error={action.error} />
      </Panel>
    </>
  );
}

const MODES: [Mode, string][] = [["off", "Off"], ["draft", "Draft for me"], ["auto", "Auto-reply"]];

function Automation() {
  const { status, refresh } = useStatus();
  const s = useLoad(() => api<AppSettings>("/settings"));
  const action = useAction();
  const toast = useToast();
  if (!s.data) return <Skeleton h={160} />;
  const save = (body: Partial<AppSettings>) => action.run(() => patch<AppSettings>("/settings", body), (r) => { s.setData(r); refresh(); toast("Saved"); });
  const setMode = (key: "mode_comments" | "mode_dms", m: Mode) => {
    if (m === "auto" && !confirm(
      "Auto-reply sends a reply without you reading it, but only when every check passes: a simple question, thanks or a useful point; " +
      `AI confidence of ${Math.round(s.data!.auto_min_confidence * 100)}% or more; no quality flags or facts to check; under the hourly cap. ` +
      "Leads, complaints and anything sensitive always wait for you. Turn it on?")) return;
    save({ [key]: m });
  };
  const num = (key: keyof AppSettings, label: string, min: number, max: number) => (
    <Field label={label}><input type="number" min={min} max={max} defaultValue={s.data![key] as number}
      onBlur={(e) => { const v = Number(e.target.value); if (v !== s.data![key]) save({ [key]: v } as Partial<AppSettings>); }} /></Field>
  );

  return (
    <>
      <div className="section-title"><h2>Automation</h2>{status?.automation_paused && <Badge tone="bad">paused by AUTOMATION_PAUSED</Badge>}</div>
      <Panel>
        <div className="stack">
          <div className="row spread">
            <div><b className="small">Replies to comments on your posts</b><p className="xs muted">Keyword DMs always run, whatever this is set to.</p></div>
            <Segmented label="Comment replies" value={s.data.mode_comments} options={MODES} onChange={(m) => setMode("mode_comments", m)} />
          </div>
          <div className="row spread">
            <div><b className="small">Replies to DMs people send you</b><p className="xs muted">Only within 24 hours of their last message.</p></div>
            <Segmented label="DM replies" value={s.data.mode_dms} options={MODES} onChange={(m) => setMode("mode_dms", m)} />
          </div>
          <Field label={`Minimum AI confidence for auto-reply: ${Math.round(s.data.auto_min_confidence * 100)}%`}>
            <input type="range" min={0.6} max={1} step={0.01} defaultValue={s.data.auto_min_confidence}
              onMouseUp={(e) => save({ auto_min_confidence: Number((e.target as HTMLInputElement).value) })}
              onKeyUp={(e) => save({ auto_min_confidence: Number((e.target as HTMLInputElement).value) })} />
          </Field>
          <div className="form-grid">
            {num("cap_auto_comment_replies_per_hour", "Auto comment replies per hour", 1, 100)}
            {num("cap_auto_dms_per_hour", "Auto DM replies per hour", 1, 200)}
            {num("cap_funnel_dms_per_hour", "Keyword DMs per hour", 1, 500)}
            {num("engagement_daily_target", "Daily engagement round size", 3, 30)}
          </div>
          <div className="callout"><span className="xs dim">Never automated, by design: comments on other people's posts, follows, and DMs to anyone who hasn't messaged or commented first.
            To stop everything instantly, set AUTOMATION_PAUSED=true in .env and restart.</span></div>
        </div>
        <ErrorNote error={action.error} />
      </Panel>
    </>
  );
}

function Audit() {
  const { data, reload } = useLoad(() => api<AuditRow[]>("/audit?limit=100"));
  const [q, setQ] = useState("");
  const rows = (data ?? []).filter((a) => !q || `${a.action} ${a.actor} ${a.entity}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <div className="section-title"><h2>Audit log</h2><div className="row"><input style={{ width: 200 }} placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter audit log" /><Button size="sm" variant="ghost" onClick={reload}>Refresh</Button></div></div>
      <Panel className="scroll-x">
        <table>
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead>
          <tbody>{rows.map((a) => (
            <tr key={a.id}>
              <td className="small muted tnum" style={{ whiteSpace: "nowrap" }}>{fmtDate(a.created_at)}</td>
              <td className="small">{a.actor}</td>
              <td className="small">{a.action.replace(/_/g, " ")} <span className="muted">{a.entity} {a.entity_id ?? ""}</span></td>
              <td className="xs muted pre" style={{ maxWidth: 420 }}>{Object.keys(a.data).length ? JSON.stringify(a.data) : ""}</td>
            </tr>
          ))}</tbody>
        </table>
      </Panel>
    </>
  );
}
