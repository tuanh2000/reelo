"use client";

// ===== Screen 7: Project Detail / Series Progress (ported from screen-project.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Progress, Placeholder, StatusPill, EmptyState, ConfirmDialog } from "@/components/ui";
import {
  PIPELINE,
  EP_STATUS,
  PROVIDERS,
  DEMO_FALLBACK,
  skillOf,
  provName,
  pubCount,
  type Nav,
  type Route,
  type Series,
  type Episode,
  type EpisodeStatus,
} from "@/lib/data";
import { DEMO_SERIES } from "@/lib/demo-fixtures";
import {
  listSeries,
  getEpisode,
  renameSeries,
  resetEpisode,
  resumeProduction,
  ApiError,
} from "@/lib/api";

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

// Action button + (optional) in-progress badge, driven by the episode's REAL
// state (status + lazy script_status). In-progress episodes route to the
// workspace, which rebuilds the live progress view from the backend.
//   • script_status running (draft) → badge "✍️ Đang viết kịch bản…" + "Xem tiến độ".
//   • status assets (đang sản xuất)  → badge "🎬 Đang sản xuất…" + "Xem tiến độ".
//   • assembled                      → "Duyệt & xuất".
//   • published                      → "Xem".
//   • draft (chưa có gì)             → "Bắt đầu".  scripted → "Tiếp tục".
function epAction(
  ep: Episode,
): {
  label: string;
  icon: string;
  variant: "secondary" | "soft" | "primary";
  to: Route["name"];
  badge?: { label: string };
} {
  if (ep.status === "published") return { label: "Xem", icon: "play", variant: "secondary", to: "review" };
  if (ep.status === "assembled")
    return { label: "Duyệt & xuất", icon: "youtube", variant: "primary", to: "review" };
  if (ep.status === "assets")
    return {
      label: "Xem tiến độ",
      icon: "loader",
      variant: "primary",
      to: "workspace",
      badge: { label: "Đang sản xuất…" },
    };
  if (ep.status === "draft") {
    if (ep.scriptStatus === "running")
      return {
        label: "Xem tiến độ",
        icon: "loader",
        variant: "primary",
        to: "workspace",
        badge: { label: "Đang viết kịch bản…" },
      };
    return { label: "Bắt đầu", icon: "sparkles", variant: "soft", to: "workspace" };
  }
  return { label: "Tiếp tục", icon: "arrow-right", variant: "primary", to: "workspace" };
}

function EpisodeRow({
  ep,
  idx,
  series,
  nav,
  onReset,
  resetting,
  onResumeProduction,
  resumingProd,
}: {
  ep: Episode;
  idx: number;
  series: Series;
  nav: Nav;
  onReset?: (ep: Episode) => void;
  resetting?: boolean;
  onResumeProduction?: (ep: Episode) => void;
  resumingProd?: boolean;
}) {
  const st = EP_STATUS[ep.status];
  const act = epAction(ep);
  // Reset is only meaningful once an episode has produced something — i.e. it is
  // past a clean draft (any non-draft status, or a draft mid/post script gen).
  const canReset =
    !!onReset && (ep.status !== "draft" || (ep.scriptStatus != null && ep.scriptStatus !== "running"));
  // "Chạy lại bước chưa xong": only while producing (status "assets"). Recovers a
  // run frozen by a worker restart (deploy) — re-queues the unfinished steps.
  const canResume = !!onResumeProduction && ep.status === "assets";
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
          {act.badge ? (
            <Badge tone="brand">
              <Icon name="loader" size={12} className="spin" />
              {act.badge.label}
            </Badge>
          ) : (
            <StatusPill status={ep.status} />
          )}
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
      {canResume && (
        <button
          className="btn btn-ghost btn-sm"
          title="Chạy lại các bước chưa xong (dùng khi sản xuất bị treo, ví dụ sau khi deploy)"
          aria-label="Chạy lại bước chưa xong"
          disabled={resumingProd}
          onClick={() => onResumeProduction?.(ep)}
          style={{ flex: "none", padding: "6px 8px", color: "var(--brand)" }}
        >
          <Icon name={resumingProd ? "loader" : "refresh-cw"} size={15} className={resumingProd ? "spin" : ""} />
        </button>
      )}
      {canReset && (
        <button
          className="btn btn-ghost btn-sm"
          title="Làm lại từ đầu (xóa kịch bản + ảnh/voice)"
          aria-label="Làm lại từ đầu"
          disabled={resetting}
          onClick={() => onReset?.(ep)}
          style={{ flex: "none", padding: "6px 8px", color: "#dc2626" }}
        >
          <Icon name={resetting ? "loader" : "rotate-ccw"} size={15} className={resetting ? "spin" : ""} />
        </button>
      )}
      <Button variant={act.variant} size="sm" icon={act.icon} onClick={() => nav({ name: act.to, series, episode: ep })}>
        {act.label}
      </Button>
    </div>
  );
}

export function ProjectScreen({ nav, route }: { nav: Nav; route: Route }) {
  // Project detail needs a concrete series. In prod it is always routed in from
  // the dashboard; with no series we show an empty state (never a mock series).
  // The offline demo (DEMO_FALLBACK) may seed a sample fixture so the screen is
  // browsable with no backend.
  if (!route.series && !DEMO_FALLBACK) {
    return (
      <EmptyState
        icon="folder-open"
        title="Chưa chọn series"
        desc="Hãy mở một series từ Bảng điều khiển để xem chi tiết."
        actionLabel="Về Bảng điều khiển"
        onAction={() => nav({ name: "dashboard" })}
      />
    );
  }
  return <ProjectInner nav={nav} route={route} />;
}

// Inline-editable series title: pencil → input with Save/Cancel (Enter saves,
// Esc cancels). Calls renameSeries and lifts the new name to the parent state.
// In the offline demo (no real series routed in) it updates locally only.
function SeriesTitle({
  series,
  isDemo,
  onRenamed,
}: {
  series: Series;
  isDemo: boolean;
  onRenamed: (name: string) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(series.name);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const begin = () => {
    setDraft(series.name);
    setError(null);
    setEditing(true);
  };
  const cancel = () => {
    setEditing(false);
    setError(null);
  };
  const save = async () => {
    const name = draft.trim();
    if (!name) {
      setError("Tên series không được để trống.");
      return;
    }
    if (name.length > 120) {
      setError("Tên series tối đa 120 ký tự.");
      return;
    }
    if (name === series.name) {
      setEditing(false);
      return;
    }
    // Offline demo: no backend — update locally only.
    if (isDemo) {
      onRenamed(name);
      setEditing(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await renameSeries(series.id, name);
      onRenamed(updated.name);
      setEditing(false);
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Đổi tên thất bại";
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <h2 style={{ fontSize: 24, margin: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
          {series.name}
        </h2>
        <button
          className="btn btn-ghost btn-sm"
          title="Đổi tên series"
          aria-label="Đổi tên series"
          onClick={begin}
          style={{ flex: "none", padding: "4px 8px" }}
        >
          <Icon name="pencil" size={15} />
        </button>
      </div>
    );
  }

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input
          ref={inputRef}
          className="input"
          value={draft}
          maxLength={120}
          disabled={saving}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void save();
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancel();
            }
          }}
          style={{ fontSize: 20, fontWeight: 700, flex: 1, minWidth: 0 }}
        />
        <Button variant="primary" size="sm" icon={saving ? "loader" : "check"} disabled={saving} onClick={() => void save()}>
          {saving ? "Đang lưu…" : "Lưu"}
        </Button>
        <Button variant="ghost" size="sm" icon="x" disabled={saving} onClick={cancel}>
          Hủy
        </Button>
      </div>
      {error && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6, fontSize: 12.5, color: "var(--danger, #ef3e36)" }}>
          <Icon name="alert-triangle" size={14} />
          {error}
        </div>
      )}
    </div>
  );
}

function ProjectInner({ nav, route }: { nav: Nav; route: Route }) {
  // Start from the series handed in by the dashboard; refresh its episode list
  // (titles/statuses) from the backend on mount so the project view reflects the
  // latest produce/publish progress. The demo fixture is only used when there is
  // no routed-in series AND we are in the offline demo.
  const [series, setSeries] = React.useState<Series>(route.series || DEMO_SERIES[0]);
  const isDemo = !route.series;

  // Reset ("Làm lại từ đầu") per episode — destructive, confirmed via dialog.
  const [resetTarget, setResetTarget] = React.useState<Episode | null>(null);
  const [resetting, setResetting] = React.useState(false);
  const [resetError, setResetError] = React.useState<string | null>(null);
  const doReset = async () => {
    if (!resetTarget) return;
    const target = resetTarget;
    setResetting(true);
    setResetError(null);
    try {
      if (!isDemo) await resetEpisode(series.id, target.id);
      // Optimistically reflect the reset locally (back to clean draft).
      setSeries((prev) => ({
        ...prev,
        episodes: prev.episodes.map((e) =>
          e.id === target.id ? { ...e, status: "draft", scriptStatus: null } : e,
        ),
      }));
      setResetTarget(null);
    } catch (e) {
      setResetError(e instanceof Error ? e.message : "Không thể làm lại tập này.");
    } finally {
      setResetting(false);
    }
  };

  // "Chạy lại bước chưa xong" per episode — non-destructive: re-queue the
  // unfinished produce steps + re-enqueue produce, then open the workspace to
  // watch live progress (it rebuilds the producing view from the backend).
  const [resumingId, setResumingId] = React.useState<string | null>(null);
  const doResumeProduction = async (ep: Episode) => {
    setResumingId(ep.id);
    setResetError(null);
    try {
      if (!isDemo) await resumeProduction(ep.id);
      nav({ name: "workspace", series, episode: ep });
    } catch (e) {
      setResetError(e instanceof Error ? e.message : "Không chạy lại được sản xuất.");
    } finally {
      setResumingId(null);
    }
  };

  // Refresh the episode list (titles/statuses) from the backend on mount AND
  // whenever the tab regains focus, so the per-episode badge ("đang viết kịch
  // bản" / "đang sản xuất") + action buttons reflect the live worker progress
  // without a manual reload. Draft episodes are additionally probed for their
  // lazy script_status (the series list only carries `status`), so a draft whose
  // worker is mid-write shows the writing badge. Bounded: only `draft` episodes
  // are probed, and only their script_status is read.
  React.useEffect(() => {
    if (!route.series) return; // demo / no real series → keep seed
    let alive = true;

    const refresh = async () => {
      try {
        const all = await listSeries();
        if (!alive) return;
        const fresh = all.find((s) => s.id === route.series!.id);
        if (!fresh) return;
        // Probe lazy script_status for draft episodes (the only ambiguous state).
        const drafts = fresh.episodes.filter((e) => e.status === "draft");
        const statuses = await Promise.all(
          drafts.map((e) =>
            getEpisode(e.id)
              .then((d) => ({ id: e.id, scriptStatus: d.scriptStatus }))
              .catch(() => ({ id: e.id, scriptStatus: null as "running" | "done" | "error" | null })),
          ),
        );
        if (!alive) return;
        const byId = new Map(statuses.map((s) => [s.id, s.scriptStatus]));
        setSeries({
          ...fresh,
          episodes: fresh.episodes.map((e) =>
            e.status === "draft" ? { ...e, scriptStatus: byId.get(e.id) ?? null } : e,
          ),
        });
      } catch {
        /* keep the routed-in copy on network error */
      }
    };

    void refresh();
    const onVis = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVis);
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
            <SeriesTitle
              series={series}
              isDemo={isDemo}
              onRenamed={(name) => setSeries((prev) => ({ ...prev, name }))}
            />
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
          <EpisodeRow
            key={ep.id}
            ep={ep}
            idx={i}
            series={series}
            nav={nav}
            onReset={(e) => {
              setResetError(null);
              setResetTarget(e);
            }}
            resetting={resetting && resetTarget?.id === ep.id}
            onResumeProduction={(e) => void doResumeProduction(e)}
            resumingProd={resumingId === ep.id}
          />
        ))}
        <button
          className="btn btn-ghost btn-md"
          style={{ border: "1.5px dashed var(--border-strong)", color: "var(--text-2)", marginTop: 2 }}
          onClick={() => nav({ name: "wizard" })}
        >
          <Icon name="plus" size={17} /> Thêm tập mới vào series
        </button>
      </div>

      <ConfirmDialog
        open={resetTarget != null}
        busy={resetting}
        title="Làm lại tập này từ đầu?"
        confirmLabel="Xóa & làm lại"
        tone="danger"
        onCancel={() => setResetTarget(null)}
        onConfirm={() => void doReset()}
        body={
          <>
            Hành động này sẽ <b>xóa vĩnh viễn kịch bản</b> cùng <b>mọi ảnh, giọng đọc,
            video và thumbnail</b> đã tạo cho “{resetTarget?.title}”. Tập sẽ trở về bản
            nháp và sẵn sàng viết lại từ đầu. Không thể hoàn tác.
            {resetError && (
              <div style={{ marginTop: 10, color: "#dc2626", display: "flex", gap: 6, alignItems: "center" }}>
                <Icon name="alert-triangle" size={14} /> {resetError}
              </div>
            )}
          </>
        }
      />
    </div>
  );
}
