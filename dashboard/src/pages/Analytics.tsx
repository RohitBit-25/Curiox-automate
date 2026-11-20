import { useState } from "react";
import { api, type AppSettings, type ContentStats, type GrowthRow, type WeekRow } from "../api";
import { Bars, LineChart } from "../Chart";
import { useLoad } from "../useLoad";
import { Badge, Empty, Page, Panel, SetupPrompt, Skeleton, Stat, Tabs, fmtNum, fmtPct, pillarColor, useStatus } from "../ui";

type Funnel = { rule_id: number; name: string; active: boolean; sent_30d: number; failed_30d: number };

export default function Analytics() {
  const { status, today } = useStatus();
  const [days, setDays] = useState<"30" | "90">("30");
  const growth = useLoad(() => api<GrowthRow[]>(`/analytics/growth?days=${days}`), [days]);
  const content = useLoad(() => api<ContentStats>("/analytics/content"));
  const funnels = useLoad(() => api<Funnel[]>("/analytics/funnels"));
  const leads = useLoad(() => api<Record<string, number>>("/analytics/leads"));
  const weekly = useLoad(() => api<WeekRow[]>("/analytics/weekly?weeks=8"));
  const settings = useLoad(() => api<AppSettings>("/settings"));
  const pillars = settings.data?.voice.pillars.map((p) => p.name) ?? [];
  const k = today?.kpis;
  const series = (key: keyof GrowthRow) => (growth.data ?? []).map((r): [string, number | null] =>
    [new Date(r.day).toLocaleDateString(undefined, { day: "numeric", month: "short" }), r[key] as number | null]);
  const dms30 = (funnels.data ?? []).reduce((n, f) => n + f.sent_30d, 0);

  return (
    <Page title="Analytics" subtitle="Is the work growing the account? Followers and reach come from Instagram Insights once a day; everything else is from Curiox OS itself."
      actions={<Tabs label="Range" value={days} onChange={setDays} items={[["30", "30 days"], ["90", "90 days"]]} />}>
      {!status?.instagram.connected && <div style={{ marginBottom: 14 }}><SetupPrompt need="instagram" /></div>}
      <div className="grid g4">
        <Stat label="Followers" value={fmtNum(k?.followers)} delta={k?.followers_delta_7d} />
        <Stat label="Reach, 7 days" value={fmtNum(k?.reach_7d)} />
        <Stat label="Keyword DMs sent, 30 days" value={fmtNum(dms30)} />
        <Stat label="Clients won" value={fmtNum(leads.data?.won ?? 0)} />
      </div>

      <div className="grid g2" style={{ marginTop: 14 }}>
        <Panel title="Followers">{growth.data ? <LineChart points={series("followers")} format={(n) => fmtNum(Math.round(n))} color="var(--live)" /> : <Skeleton h={200} />}</Panel>
        <Panel title="Daily reach">{growth.data ? <LineChart points={series("reach")} format={(n) => fmtNum(Math.round(n))} /> : <Skeleton h={200} />}</Panel>
      </div>

      <div className="section-title"><h2>What content works</h2></div>
      {!content.data ? <Skeleton h={160} /> : content.data.items.length === 0 ? (
        <Empty title="No published posts measured yet" body="Post insights arrive the morning after something publishes through Curiox OS." />
      ) : (
        <>
          <div className="grid g2">
            <Panel title="Average reach by pillar"><Bars data={content.data.by_pillar.map((p) => ({ label: p.key, value: p.avg_reach, color: pillarColor(pillars, p.key) }))} format={fmtNum} /></Panel>
            <Panel title="Average reach by format"><Bars data={content.data.by_format.map((p) => ({ label: p.key, value: p.avg_reach }))} format={fmtNum} /></Panel>
          </div>
          <Panel title="Top posts by reach" className="scroll-x mt">
            <table>
              <thead><tr><th>Post</th><th className="num">Reach</th><th className="num">Saves</th><th className="num">Shares</th><th className="num">Eng. rate</th></tr></thead>
              <tbody>{[...content.data.items].sort((a, b) => (b.reach ?? 0) - (a.reach ?? 0)).slice(0, 10).map((i) => (
                <tr key={i.id}>
                  <td><div className="row"><Badge>{i.format}</Badge><span className="small dim">{i.pillar}</span></div>
                    {i.permalink ? <a className="link small" href={i.permalink} target="_blank" rel="noopener noreferrer">{i.angle.slice(0, 80) || "View post"}</a> : <span className="small">{i.angle.slice(0, 80)}</span>}</td>
                  <td className="num">{fmtNum(i.reach)}</td><td className="num">{fmtNum(i.saved)}</td><td className="num">{fmtNum(i.shares)}</td><td className="num">{fmtPct(i.er)}</td>
                </tr>
              ))}</tbody>
            </table>
          </Panel>
        </>
      )}

      <div className="grid g2" style={{ marginTop: 14 }}>
        <Panel title="Keyword DMs, 30 days">
          {!funnels.data?.length ? <p className="muted small">No keyword DMs yet.</p> : <Bars data={funnels.data.map((f) => ({ label: f.name, value: f.sent_30d }))} />}
        </Panel>
        <Panel title="Leads by stage">
          {!leads.data || !Object.keys(leads.data).length ? <p className="muted small">No leads yet.</p>
            : <Bars data={["new", "researched", "contacted", "replied", "call", "won", "lost"].map((s) => ({ label: s, value: leads.data![s] ?? 0, color: s === "won" ? "var(--live)" : undefined }))} />}
        </Panel>
      </div>

      <div className="section-title"><h2>Engagement pipeline, weekly</h2></div>
      <Panel className="scroll-x">
        {!weekly.data?.length ? <p className="muted small">No activity yet.</p> : (
          <table>
            <thead><tr><th>Week</th><th className="num">Posts found</th><th className="num">Drafted</th><th className="num">Approved</th><th className="num">Rejected</th><th className="num">Posted</th></tr></thead>
            <tbody>{weekly.data.map((w) => <tr key={w.week}><td className="tnum">{w.week}</td><td className="num">{w.discovered}</td><td className="num">{w.queued}</td><td className="num">{w.approved}</td><td className="num">{w.rejected}</td><td className="num">{w.published}</td></tr>)}</tbody>
          </table>
        )}
      </Panel>
    </Page>
  );
}
