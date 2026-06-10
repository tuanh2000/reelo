"use client";

// ===== Screen 7: Project Detail / Series Progress (ported from screen-project.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Progress, Placeholder, StatusPill } from "@/components/ui";
import {
  SERIES,
  PIPELINE,
  EP_STATUS,
  PROVIDERS,
  skillOf,
  provName,
  pubCount,
  type Nav,
  type Route,
  type Series,
  type Episode,
  type EpisodeStatus,
} from "@/lib/data";
import { listSeries } from "@/lib/api";

function MiniSteps({ step }: { step: number }) {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
      {PIPELINE.map((p, i) => (
        <div
          key={p.id}
          title={p.name}
          style={{ width: 7, height: 7, borderRadius: 999, background: i <= step ? "var(--brand)" : "var(--surface-3)" }}
        />
      ))}
    </div>
  );
}

function epAction(status: EpisodeStatus): { label: string; icon: string; variant: "secondary" | "soft" | "primary"; to: Route["name"] } {
  if (status === "published") return { label: "Xem", icon: "play", variant: "secondary", to: "review" };
  if (status === "draft") return { label: "Bắt đầu", icon: "sparkles", variant: "soft", to: "workspace" };
  return { label: "Tiếp tục", icon: "arrow-right", variant: "primary", to: "workspace" };
}

function EpisodeRow({ ep, idx, series, nav }: { ep: Episode; idx: number; series: Series; nav: Nav }) {
  const st = EP_STATUS[ep.status];
  const act = epAction(ep.status);
  return (
    <div className="card" style={{ boxShadow: "none", display: "flex", alignItems: "center", gap: 14, padding: 13 }}>
      <span
        style={{
          width: 30,
          height: 30,
          borderRadius: 9,
          background: "var(--surface-2)",
          color: "var(--text-2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontWeight: 800,
          fontSize: 13,
          flex: "none",
        }}
      >
        {String(idx + 1).padStart(2, "0")}
      </span>
      <Placeholder label="ảnh tập" style={{ width: 84, height: 48, flex: "none" }} rounded="rounded-lg" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 14.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {ep.title}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 6 }}>
          <StatusPill status={ep.status} />
          <MiniSteps step={st.step} />
          {ep.dur && (
            <span className="subtle" style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4 }}>
              <Icon name="clock" size={12} /> {ep.dur}
            </span>
          )}
          {ep.views && (
            <span className="subtle" style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4 }}>
              <Icon name="eye" size={12} /> {ep.views}
            </span>
          )}
        </div>
      </div>
      <Button variant={act.variant} size="sm" icon={act.icon} onClick={() => nav({ name: act.to, series, episode: ep })}>
        {act.label}
      </Button>
    </div>
  );
}

export function ProjectScreen({ nav, route }: { nav: Nav; route: Route }) {
  // Start from the series handed in by the dashboard; refresh its episode list
  // (titles/statuses) from the backend on mount so the project view reflects the
  // latest produce/publish progress. Falls back to the static seed only when no
  // series was routed in (offline demo).
  const [series, setSeries] = React.useState<Series>(route.series || SERIES[0]);

  React.useEffect(() => {
    if (!route.series) return; // demo / no real series → keep seed
    let alive = true;
    listSeries()
      .then((all) => {
        if (!alive) return;
        const fresh = all.find((s) => s.id === route.series!.id);
        if (fresh) setSeries(fresh);
      })
      .catch(() => {
        /* keep the routed-in copy on network error */
      });
    return () => {
      alive = false;
    };
  }, [route.series]);

  const sk = skillOf(series.skill);
  const done = pubCount(series);
  const total = series.episodes.length;
  const resume = series.episodes.find((e) => !["draft", "published"].includes(e.status));
  const counts = series.episodes.reduce<Record<string, number>>((a, e) => {
    a[e.status] = (a[e.status] || 0) + 1;
    return a;
  }, {});

  return (
    <div className="page page-wide" style={{ paddingBottom: 48 }}>
      {/* header */}
      <Card style={{ padding: 0, overflow: "hidden", marginBottom: 20 }}>
        <div style={{ display: "flex", gap: 18, padding: 22 }}>
          <Placeholder label={series.cover} style={{ width: 200, height: 124, flex: "none" }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
              <Badge tone="neutral" icon="folder">
                {series.topic}
              </Badge>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 600, color: sk.accent, whiteSpace: "nowrap" }}>
                <Icon name={sk.icon} size={15} /> {sk.name}
              </span>
            </div>
            <h2 style={{ fontSize: 24, marginBottom: 10 }}>{series.name}</h2>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
              {Object.keys(series.providers).map((g) => (
                <Badge key={g} tone="neutral" icon={PROVIDERS[g].icon}>
                  {provName(g, (series.providers as Record<string, string>)[g])}
                </Badge>
              ))}
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 9, alignItems: "flex-end", flex: "none" }}>
            <Button variant="secondary" size="sm" icon="sliders-horizontal" onClick={() => nav({ name: "setup", series })}>
              Skill &amp; Provider
            </Button>
            <Button variant="secondary" size="sm" icon="palette" onClick={() => nav({ name: "style", series })}>
              Style Studio
            </Button>
          </div>
        </div>
        <div className="divider" />
        <div style={{ padding: "14px 22px", display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 7 }}>
              <span style={{ fontWeight: 700, fontSize: 13.5 }}>
                {done}/{total} tập đã xuất bản
              </span>
              <span className="subtle" style={{ fontSize: 12.5 }}>
                {Math.round((done / total) * 100)}%
              </span>
            </div>
            <Progress value={(done / total) * 100} height={8} />
          </div>
        </div>
      </Card>

      {/* resume banner */}
      {resume && (
        <Card
          style={{
            padding: 16,
            marginBottom: 20,
            display: "flex",
            alignItems: "center",
            gap: 16,
            border: "1px solid color-mix(in oklab, var(--brand) 30%, transparent)",
            background: "var(--brand-tint)",
          }}
        >
          <span
            style={{
              width: 44,
              height: 44,
              borderRadius: 12,
              background: "var(--surface)",
              color: "var(--brand)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flex: "none",
            }}
          >
            <Icon name="history" size={22} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Tiếp tục công việc còn dở</div>
            <div className="muted" style={{ fontSize: 13.5, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              “{resume.title}” · đang ở bước{" "}
              <b style={{ color: "var(--text)" }}>{PIPELINE[Math.min(EP_STATUS[resume.status].step + 1, PIPELINE.length - 1)].name}</b>
            </div>
          </div>
          <Button variant="primary" size="md" icon="arrow-right" onClick={() => nav({ name: "workspace", series, episode: resume })}>
            Tiếp tục ngay
          </Button>
        </Card>
      )}

      {/* episodes */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <h3 style={{ fontSize: 17 }}>Tất cả các tập</h3>
        <div style={{ display: "flex", gap: 7 }}>
          {(["published", "assembled", "assets", "scripted", "draft"] as EpisodeStatus[])
            .filter((s) => counts[s])
            .map((s) => (
              <span key={s} className="status-pill" style={{ ["--s" as any]: EP_STATUS[s].color, fontSize: 12 } as React.CSSProperties}>
                <span className="status-dot" />
                {counts[s]} {EP_STATUS[s].label}
              </span>
            ))}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {series.episodes.map((ep, i) => (
          <EpisodeRow key={ep.id} ep={ep} idx={i} series={series} nav={nav} />
        ))}
        <button
          className="btn btn-ghost btn-md"
          style={{ border: "1.5px dashed var(--border-strong)", color: "var(--text-2)", marginTop: 2 }}
          onClick={() => nav({ name: "wizard" })}
        >
          <Icon name="plus" size={17} /> Thêm tập mới vào series
        </button>
      </div>
    </div>
  );
}
