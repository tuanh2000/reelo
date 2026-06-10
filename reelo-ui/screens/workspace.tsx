"use client";

// ===== Screen 5: Script Workspace — pipeline + editor + chat (ported from screen-workspace.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Progress, Placeholder, StatusPill, Segmented, ChatBubble, EmptyState, ErrorBox } from "@/components/ui";
import { PIPELINE, type Nav, type Route, type Series, type Episode, type GenJob, type ScriptSegment } from "@/lib/data";
import {
  generateEpisodeScript,
  getEpisode,
  startGeneration,
  pollGeneration,
  retryChild,
  type CostEstimate,
  type SegmentSpec,
} from "@/lib/api";

// After this long with no segments and no error, warn that the worker may be
// down/busy (we keep polling regardless). ~90s per the spec.
const SCRIPT_STALL_MS = 90_000;
const SCRIPT_POLL_MS = 2000;

// Web media providers offer a human curation step before produce: the aggregate
// `web` plus the `web-*` family (web-commons photos / web-pexels clips). AI
// providers (kie/gemini/openai/sd) auto-pick — no selection step.
function isWebPhotoProvider(providerId: string): boolean {
  return providerId === "web" || providerId.startsWith("web-");
}

// Poll cadence + a stop predicate shared by the producing view.
const POLL_MS = 1500;
function allTerminal(jobs: GenJob[]): boolean {
  return jobs.length > 0 && jobs.every((j) => j.state === "done" || j.state === "error");
}

function PipelineRail({ doneIds, activeId, onJump }: { doneIds: string[]; activeId: string; onJump?: (id: string) => void }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
      {PIPELINE.map((p, i) => {
        const done = doneIds.includes(p.id);
        const active = activeId === p.id;
        const color = done ? "#16a34a" : active ? "var(--brand)" : "var(--text-3)";
        return (
          <div key={p.id} style={{ display: "flex", gap: 12 }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              <button
                onClick={() => onJump && onJump(p.id)}
                style={{
                  width: 38,
                  height: 38,
                  borderRadius: 12,
                  border: `2px solid ${active ? "var(--brand)" : "transparent"}`,
                  background: done
                    ? "color-mix(in oklab,#16a34a 14%,transparent)"
                    : active
                      ? "var(--brand-tint)"
                      : "var(--surface-2)",
                  color,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flex: "none",
                  transition: ".2s",
                }}
              >
                <Icon name={done ? "check" : p.icon} size={18} strokeWidth={done ? 3 : 2} />
              </button>
              {i < PIPELINE.length - 1 && (
                <div style={{ width: 2, flex: 1, minHeight: 26, background: done ? "#16a34a" : "var(--border)", margin: "3px 0" }} />
              )}
            </div>
            <div style={{ paddingTop: 5, paddingBottom: 18, lineHeight: 1.3 }}>
              <div style={{ fontWeight: 700, fontSize: 14.5, whiteSpace: "nowrap", color: active ? "var(--text)" : done ? "var(--text)" : "var(--text-2)" }}>
                {p.name}
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, color, marginTop: 1 }}>{done ? "Hoàn tất" : active ? "Đang xử lý" : "Chờ"}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SegmentCard({ seg, idx }: { seg: ScriptSegment; idx: number }) {
  const [text, setText] = React.useState(seg.text);
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden", boxShadow: "none" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "10px 14px", borderBottom: "1px solid var(--border)", background: "var(--surface-2)" }}>
        <span
          style={{
            width: 24,
            height: 24,
            borderRadius: 7,
            background: "var(--brand-tint)",
            color: "var(--brand-700)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 12.5,
            fontWeight: 800,
          }}
        >
          {idx + 1}
        </span>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>Đoạn {idx + 1}</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          {/* Per-segment regenerate / preview / options — no backend endpoint yet. */}
          <button className="icon-btn" style={{ width: 30, height: 30, opacity: 0.5, cursor: "default" }} title="Tạo lại văn bản · Sắp có" disabled>
            <Icon name="refresh-cw" size={15} />
          </button>
          <button className="icon-btn" style={{ width: 30, height: 30, opacity: 0.5, cursor: "default" }} title="Nghe thử giọng đọc · Sắp có" disabled>
            <Icon name="volume-2" size={15} />
          </button>
          <button className="icon-btn" style={{ width: 30, height: 30, opacity: 0.5, cursor: "default" }} title="Thêm tùy chọn · Sắp có" disabled>
            <Icon name="more-horizontal" size={15} />
          </button>
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 168px", gap: 14, padding: 14 }}>
        <textarea
          className="field"
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={{ border: "none", boxShadow: "none", padding: 0, fontSize: 14.5, minHeight: 92, background: "transparent" }}
          rows={4}
        />
        <div>
          <Placeholder label={seg.img} style={{ width: "100%", height: 95 }} />
          {/* Per-segment image regen — no backend endpoint yet. */}
          <button
            className="btn btn-secondary btn-sm"
            style={{ width: "100%", marginTop: 8, fontSize: 12.5, opacity: 0.5, cursor: "default" }}
            title="Sắp có"
            disabled
          >
            <Icon name="wand-sparkles" size={14} /> Tạo lại ảnh
          </button>
        </div>
      </div>
    </div>
  );
}

function JobRow({ job, onRetry }: { job: GenJob; onRetry?: (childId: string) => void }) {
  const stateMap = {
    done: { c: "#16a34a", t: "Xong", ic: "check-circle-2" },
    running: { c: "var(--brand)", t: "Đang chạy", ic: "loader" },
    queued: { c: "var(--text-3)", t: "Trong hàng đợi", ic: "clock" },
    error: { c: "#dc2626", t: "Lỗi", ic: "alert-triangle" },
  } as const;
  const st = stateMap[job.state];
  const isError = job.state === "error";
  return (
    <div className="card" style={{ padding: 14, boxShadow: "none", display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 13 }}>
        <span
          style={{
            width: 38,
            height: 38,
            borderRadius: 11,
            background: "var(--surface-2)",
            color: st.c,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flex: "none",
          }}
        >
          <Icon name={job.icon} size={18} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 7 }}>
            <span style={{ fontWeight: 700, fontSize: 14 }}>{job.name}</span>
            <span style={{ fontSize: 12.5, fontWeight: 700, color: st.c, display: "inline-flex", alignItems: "center", gap: 5 }}>
              <Icon name={st.ic} size={14} className={job.state === "running" ? "spin" : ""} /> {st.t}
            </span>
          </div>
          <Progress value={job.progress} height={6} tone={isError ? "#dc2626" : job.state === "done" ? "#16a34a" : "var(--brand)"} />
        </div>
        {isError && (
          <Button variant="soft" size="sm" icon="refresh-cw" onClick={() => onRetry && onRetry(job.id)}>
            Thử lại
          </Button>
        )}
      </div>
      {isError && (
        <ErrorBox
          title={`Lỗi: ${job.name}`}
          detail={job.stderr || "Không có chi tiết lỗi từ worker (job báo lỗi nhưng không kèm thông điệp)."}
          hint="Sao chép nội dung dưới đây để gửi lại, hoặc nhấn “Thử lại” để chạy lại bước này."
        />
      )}
    </div>
  );
}

function ProducingView({
  jobs,
  onDone,
  onRetry,
}: {
  jobs: GenJob[];
  onDone: () => void;
  onRetry: (childId: string) => void;
}) {
  const allDone = jobs.length > 0 && jobs.every((j) => j.state === "done");
  const errorJobs = jobs.filter((j) => j.state === "error");
  const hasError = errorJobs.length > 0;
  // Combined, copyable summary of every failed job (for "gửi lại" in one paste).
  const combinedError = errorJobs
    .map((j) => `# ${j.name}\n${j.stderr || "(không có chi tiết lỗi)"}`)
    .join("\n\n");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div
        className="card"
        style={{
          padding: 18,
          display: "flex",
          alignItems: "center",
          gap: 16,
          background: allDone ? "color-mix(in oklab,#16a34a 8%,var(--surface))" : "var(--surface)",
        }}
      >
        <span
          style={{
            width: 48,
            height: 48,
            borderRadius: 14,
            background: allDone ? "color-mix(in oklab,#16a34a 16%,transparent)" : "var(--brand-tint)",
            color: allDone ? "#16a34a" : "var(--brand)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flex: "none",
          }}
        >
          <Icon
            name={allDone ? "party-popper" : hasError ? "alert-triangle" : "loader"}
            size={24}
            className={allDone || hasError ? "" : "spin"}
          />
        </span>
        <div style={{ flex: 1 }}>
          <h3 style={{ fontSize: 17 }}>
            {allDone
              ? "Đã dựng xong video!"
              : hasError
                ? "Một số bước gặp lỗi"
                : "Đang sản xuất tập này…"}
          </h3>
          <p className="muted" style={{ fontSize: 13.5, marginTop: 3 }}>
            {allDone
              ? "Tất cả asset đã sẵn sàng. Sang bước duyệt để xuất bản."
              : hasError
                ? "Nhấn “Thử lại” ở bước bị lỗi để chạy lại phần còn thiếu."
                : "Bạn có thể đóng tab — tiến độ được lưu tự động và tiếp tục sau."}
          </p>
        </div>
        {allDone && (
          <Button variant="primary" size="md" icon="arrow-right" onClick={onDone}>
            Tiếp tục duyệt
          </Button>
        )}
      </div>

      {hasError && errorJobs.length > 1 && (
        <ErrorBox
          title={`Tổng hợp lỗi sản xuất (${errorJobs.length} bước)`}
          detail={combinedError}
          hint="Có nhiều bước gặp lỗi. Sao chép toàn bộ để gửi lại, hoặc thử lại từng bước bên dưới."
        />
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {jobs.length === 0 ? (
          <div className="card" style={{ padding: 16, boxShadow: "none", display: "flex", alignItems: "center", gap: 10 }}>
            <Icon name="loader" size={18} className="spin" style={{ color: "var(--brand)" }} />
            <span className="muted" style={{ fontSize: 13.5 }}>Đang khởi tạo các bước sản xuất…</span>
          </div>
        ) : (
          jobs.map((j) => <JobRow key={j.id} job={j} onRetry={onRetry} />)
        )}
      </div>

      {/* Voice preview player — no streaming endpoint yet, so it is disabled. */}
      <div className="card" style={{ padding: 16, opacity: 0.6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <Icon name="music-2" size={17} style={{ color: "var(--brand)" }} />
          <span style={{ fontWeight: 700, fontSize: 14 }}>Nghe thử giọng đọc</span>
          <Badge tone="neutral" className="ml-auto" style={{ marginLeft: "auto" }}>
            Sắp có
          </Badge>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button className="btn btn-primary" style={{ width: 42, height: 42, borderRadius: 999, padding: 0, cursor: "default" }} title="Sắp có" disabled>
            <Icon name="play" size={18} />
          </button>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", gap: 2, alignItems: "flex-end", height: 30 }}>
              {Array.from({ length: 56 }).map((_, i) => (
                <div
                  key={i}
                  style={{
                    flex: 1,
                    height: `${20 + Math.abs(Math.sin(i * 0.7)) * 70}%`,
                    background: i < 18 ? "var(--brand)" : "var(--surface-3)",
                    borderRadius: 2,
                  }}
                />
              ))}
            </div>
          </div>
          <span className="mono subtle" style={{ fontSize: 12 }}>
            0:32 / 2:14
          </span>
        </div>
      </div>
    </div>
  );
}

function ToneChat() {
  // Style-refinement chat has no backend LLM endpoint yet, so the controls are
  // shown disabled ("Sắp có") instead of simulating a rewrite.
  const [tone, setTone] = React.useState("formal");
  const [len, setLen] = React.useState(2);
  return (
    <Card style={{ display: "flex", flexDirection: "column", overflow: "hidden", height: "100%" }}>
      <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 9 }}>
        <Icon name="sliders-horizontal" size={17} style={{ color: "var(--brand)" }} />
        <h3 style={{ fontSize: 15 }}>Tinh chỉnh văn phong</h3>
        <Badge tone="neutral" style={{ marginLeft: "auto" }}>
          Sắp có
        </Badge>
      </div>
      <div style={{ padding: 16, borderBottom: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 14, opacity: 0.6, pointerEvents: "none" }}>
        <div>
          <span className="label">Tông giọng</span>
          <Segmented
            value={tone}
            onChange={setTone}
            options={[
              { value: "formal", label: "Trang trọng" },
              { value: "friendly", label: "Thân thiện" },
              { value: "drama", label: "Kịch tính" },
            ]}
          />
        </div>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="label">Độ dài</span>
            <span className="subtle" style={{ fontSize: 12.5, fontWeight: 600 }}>
              {["Rất ngắn", "Ngắn", "Vừa", "Dài", "Rất dài"][len]}
            </span>
          </div>
          <input type="range" min="0" max="4" value={len} onChange={(e) => setLen(+e.target.value)} style={{ width: "100%", accentColor: "var(--brand)" }} />
        </div>
      </div>
      <div className="scroll-y" style={{ flex: 1, padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
        <ChatBubble role="ai">
          Trợ lý tinh chỉnh văn phong sẽ sớm có mặt — bạn sẽ chỉnh được giọng văn, độ dài và tông cho từng tập.
        </ChatBubble>
      </div>
      <div style={{ padding: 12, borderTop: "1px solid var(--border)", display: "flex", gap: 8 }}>
        <input className="field" placeholder="Sắp có…" disabled style={{ opacity: 0.6 }} />
        <Button variant="primary" icon="send" className="btn-icon" disabled title="Sắp có" />
      </div>
    </Card>
  );
}

/** Map an api SegmentSpec to the editor's ScriptSegment shape. */
function specToScript(seg: SegmentSpec): ScriptSegment {
  return { id: `seg${seg.index}`, text: seg.narration, img: seg.image_prompt };
}

// Derive which pipeline rail steps are done from the real child GenJob[].
function railFromJobs(jobs: GenJob[]): { doneIds: string[]; activeId: string } {
  const byKind = (pred: (id: string) => boolean) => jobs.filter((j) => pred(j.id));
  const done = (arr: GenJob[]) => arr.length > 0 && arr.every((j) => j.state === "done");
  const voiceJobs = byKind((id) => id.includes("voice"));
  const imageJobs = byKind((id) => id.includes("image") || id.includes("img"));
  const renderJobs = byKind((id) => id.includes("render"));
  const doneIds = ["script"];
  if (done(voiceJobs)) doneIds.push("voice");
  if (done(imageJobs)) doneIds.push("images");
  if (done(renderJobs)) doneIds.push("assemble");
  const allDone = jobs.length > 0 && jobs.every((j) => j.state === "done");
  let activeId = "voice";
  if (allDone) activeId = "review";
  else if (done(voiceJobs) && !done(imageJobs)) activeId = "images";
  else if (done(imageJobs) && !done(renderJobs)) activeId = "assemble";
  return { doneIds, activeId };
}

export function WorkspaceScreen({ nav, route }: { nav: Nav; route: Route }) {
  // No active series → nothing real to script. Show an empty state instead of
  // falling back to mock data. (The episode may still be implied from the series.)
  if (!route.series) {
    return (
      <EmptyState
        icon="pen-line"
        title="Chưa chọn series"
        desc="Hãy mở một series từ Bảng điều khiển để bắt đầu viết kịch bản."
        actionLabel="Về Bảng điều khiển"
        onAction={() => nav({ name: "dashboard" })}
      />
    );
  }
  return <WorkspaceInner nav={nav} route={route} series={route.series} />;
}

function WorkspaceInner({ nav, route, series }: { nav: Nav; route: Route; series: Series }) {
  const episode: Episode =
    route.episode || series.episodes.find((e) => e.status !== "published") || series.episodes[0];
  // If we navigated in with a live job (from image-select after startGeneration,
  // or a resumed produce), open straight into the producing view.
  const [stage, setStage] = React.useState<"edit" | "producing">(
    route.producing || route.jobId ? "producing" : "edit",
  );
  const [jobId, setJobId] = React.useState<string | null>(route.jobId ?? null);
  const [jobs, setJobs] = React.useState<GenJob[]>([]);

  // ---- Script segments (lazy gen) — explicit state machine over getEpisode ----
  // phase: "loading"  = first fetch / kicking off lazy gen (brief)
  //        "running"  = worker writing the script, no segments yet (show timer)
  //        "done"     = segments present (show the editor)
  //        "error"    = worker reported a failure (show copyable error + retry)
  type ScriptPhase = "loading" | "running" | "done" | "error";
  const [segments, setSegments] = React.useState<ScriptSegment[] | null>(null);
  const [scriptPhase, setScriptPhase] = React.useState<ScriptPhase>("loading");
  const [scriptError, setScriptError] = React.useState<string | null>(null);
  // Elapsed seconds while running, + a stall flag once we pass SCRIPT_STALL_MS.
  const [scriptElapsed, setScriptElapsed] = React.useState(0);
  const [scriptStalled, setScriptStalled] = React.useState(false);
  // Bump to force a fresh gen attempt (the "Thử lại" button).
  const [scriptAttempt, setScriptAttempt] = React.useState(0);

  // Load (and if needed lazily generate) the episode script, then poll the
  // episode until it is `done` or `error`. The poll is fully cleaned up on
  // unmount / done / error so it never leaks. Demo/offline: a missing backend
  // surfaces as an error (copyable) but the screen stays usable.
  React.useEffect(() => {
    if (!route.episode) {
      // Series opened without a specific episode — nothing to fetch yet.
      setScriptPhase("done");
      return;
    }
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let tick: ReturnType<typeof setInterval> | null = null;
    const startedAt = Date.now();
    setSegments(null);
    setScriptPhase("loading");
    setScriptError(null);
    setScriptElapsed(0);
    setScriptStalled(false);

    const stopTimers = () => {
      if (timer) clearTimeout(timer);
      if (tick) clearInterval(tick);
      timer = null;
      tick = null;
    };

    const apply = (segs: SegmentSpec[]) => {
      if (!alive) return;
      stopTimers();
      setSegments(segs.map(specToScript));
      setScriptPhase("done");
    };

    const fail = (msg: string) => {
      if (!alive) return;
      stopTimers();
      setScriptError(msg);
      setScriptPhase("error");
    };

    // Drive the elapsed-seconds counter + stall warning while we wait.
    const startRunningClock = () => {
      if (tick) return;
      setScriptPhase("running");
      tick = setInterval(() => {
        if (!alive) return;
        const secs = Math.floor((Date.now() - startedAt) / 1000);
        setScriptElapsed(secs);
        if (secs * 1000 >= SCRIPT_STALL_MS) setScriptStalled(true);
      }, 1000);
    };

    const pollUntilSettled = async () => {
      try {
        const detail = await getEpisode(episode.id);
        if (!alive) return;
        if (detail.episode.segments && detail.episode.segments.length > 0) {
          apply(detail.episode.segments);
          return;
        }
        if (detail.scriptStatus === "error") {
          fail(detail.scriptError || "Worker báo lỗi nhưng không kèm chi tiết.");
          return;
        }
        // still running (or status not yet written) → keep the clock + poll again
        startRunningClock();
        timer = setTimeout(pollUntilSettled, SCRIPT_POLL_MS);
      } catch (e) {
        fail(e instanceof Error ? e.message : "Không tải được kịch bản.");
      }
    };

    // Kick off lazy gen (idempotent server-side): returns segments if already
    // scripted, else an empty shell + (re)enqueues the worker; then we poll.
    generateEpisodeScript(episode.id)
      .then((ep) => {
        if (!alive) return;
        if (ep.segments && ep.segments.length > 0) apply(ep.segments);
        else {
          startRunningClock();
          pollUntilSettled();
        }
      })
      .catch((e) => {
        fail(e instanceof Error ? e.message : "Không tạo được kịch bản.");
      });

    return () => {
      alive = false;
      stopTimers();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode.id, scriptAttempt]);

  const retryScript = () => setScriptAttempt((n) => n + 1);

  // ---- Produce: poll the real GenJob[] while a job is in flight ----
  const [produceError, setProduceError] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (stage !== "producing" || !jobId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const next = await pollGeneration(jobId);
        if (!alive) return;
        setJobs(next);
        if (!allTerminal(next)) {
          timer = setTimeout(tick, POLL_MS);
        }
      } catch (e) {
        if (!alive) return;
        setProduceError(e instanceof Error ? e.message : "Mất kết nối khi theo dõi tiến độ.");
        // back off but keep trying so transient errors recover
        timer = setTimeout(tick, POLL_MS * 2);
      }
    };
    tick();

    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [stage, jobId]);

  // Start generation (AI providers go straight here; web-* curate first).
  const [starting, setStarting] = React.useState(false);
  const beginProduce = async () => {
    setStarting(true);
    setProduceError(null);
    try {
      const { jobId: id, costEstimate } = await startGeneration(series.id, episode.id);
      if (costEstimate && (costEstimate.images || costEstimate.voice_chars)) {
        const note = costEstimate.note ? `\n${costEstimate.note}` : "";
        const ok = window.confirm(
          `Sản xuất tập này sẽ tạo ${costEstimate.images} ảnh và ~${costEstimate.voice_chars} ký tự giọng đọc.${note}\n\nTiếp tục?`,
        );
        if (!ok) {
          setStarting(false);
          return;
        }
      }
      setJobId(id);
      setJobs([]);
      setStage("producing");
    } catch (e) {
      setProduceError(e instanceof Error ? e.message : "Không bắt đầu được quá trình sản xuất.");
    } finally {
      setStarting(false);
    }
  };

  const onProduceClick = () => {
    // Web media providers curate real photos/clips first.
    if (isWebPhotoProvider(series.providers.image)) {
      nav({ name: "image-select", series, episode });
    } else {
      void beginProduce();
    }
  };

  const onRetry = async (childId: string) => {
    if (!jobId) return;
    try {
      const next = await retryChild(jobId, childId);
      setJobs(next);
    } catch (e) {
      setProduceError(e instanceof Error ? e.message : "Thử lại thất bại.");
    }
  };

  const rail = stage === "producing" ? railFromJobs(jobs) : { doneIds: ["script"], activeId: "script" };
  const doneIds = rail.doneIds;
  const activeId = rail.activeId;
  const segCount = segments?.length ?? 0;

  return (
    <div className="page page-wide" style={{ height: "100%", display: "flex", flexDirection: "column", paddingBottom: 24 }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 18 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 4 }}>
            <Badge tone="neutral" icon="folder">
              {series.name}
            </Badge>
            <StatusPill status={episode.status} />
          </div>
          <h2 style={{ fontSize: 22, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{episode.title}</h2>
        </div>
        {stage === "edit" ? (
          <Button
            variant="primary"
            size="md"
            icon="clapperboard"
            disabled={starting || scriptPhase !== "done" || segCount === 0}
            onClick={onProduceClick}
          >
            {starting
              ? "Đang bắt đầu…"
              : isWebPhotoProvider(series.providers.image)
                ? "Chọn ảnh & Sản xuất"
                : "Sản xuất tập này"}
          </Button>
        ) : (
          <Button variant="secondary" size="md" icon="pen-line" onClick={() => setStage("edit")}>
            Sửa kịch bản
          </Button>
        )}
      </div>

      {produceError && (
        <div className="card" style={{ padding: 12, marginBottom: 14, color: "#dc2626", display: "flex", gap: 8 }}>
          <Icon name="alert-triangle" size={16} /> {produceError}
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "224px 1fr 332px", gap: 20 }}>
        {/* left: pipeline */}
        <Card style={{ padding: 18, height: "fit-content" }}>
          <div className="side-section" style={{ padding: "0 0 14px" }}>
            Quy trình sản xuất
          </div>
          <PipelineRail doneIds={doneIds} activeId={activeId} />
          <div className="divider" style={{ margin: "4px 0 14px" }} />
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--text-2)", fontWeight: 600 }}>
            <Icon name="save" size={15} style={{ color: "#16a34a" }} /> Tự động lưu · có thể tiếp tục sau
          </div>
        </Card>

        {/* center */}
        <div className="scroll-y" style={{ minWidth: 0 }}>
          {stage === "edit" ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="card" style={{ padding: "14px 16px", display: "flex", alignItems: "center", gap: 10, background: "var(--surface-2)", boxShadow: "none" }}>
                <Icon name="pen-line" size={17} style={{ color: "var(--brand)" }} />
                <span style={{ fontWeight: 700, fontSize: 14 }}>Kịch bản · {segCount} đoạn</span>
                <span className="subtle" style={{ fontSize: 12.5, marginLeft: "auto" }}>
                  Mỗi đoạn gắn 1 hình ảnh
                </span>
              </div>
              {scriptPhase === "error" ? (
                <ErrorBox
                  title="Lỗi khi viết kịch bản"
                  detail={scriptError || "Không có chi tiết lỗi."}
                  hint={
                    <>
                      Worker không tạo được kịch bản. Hãy kiểm tra{" "}
                      <strong>provider / API key ở Cấu hình AI</strong>, rồi nhấn “Thử lại”.
                      Sao chép nội dung lỗi dưới đây để gửi lại nếu cần hỗ trợ.
                    </>
                  }
                  actions={
                    <Button variant="primary" size="sm" icon="refresh-cw" onClick={retryScript}>
                      Thử lại
                    </Button>
                  }
                />
              ) : scriptPhase === "done" ? (
                <>
                  {(segments || []).map((seg, i) => (
                    <SegmentCard key={seg.id} seg={seg} idx={i} />
                  ))}
                  {/* Add-segment — no backend endpoint yet. */}
                  <button
                    className="btn btn-ghost btn-md"
                    style={{ border: "1.5px dashed var(--border-strong)", color: "var(--text-2)", opacity: 0.5, cursor: "default" }}
                    title="Sắp có"
                    disabled
                  >
                    <Icon name="plus" size={17} /> Thêm đoạn
                  </button>
                </>
              ) : (
                // "loading" / "running": spinner + elapsed seconds; warn on stall.
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div className="card" style={{ padding: 24, display: "flex", alignItems: "center", justifyContent: "center", gap: 10, boxShadow: "none" }}>
                    <Icon name="loader" size={20} className="spin" style={{ color: "var(--brand)" }} />
                    <span className="muted" style={{ fontSize: 14 }}>
                      ✍️ Đang viết kịch bản…
                      {scriptPhase === "running" && ` (đã ${scriptElapsed} giây)`}
                    </span>
                  </div>
                  {scriptStalled && (
                    <ErrorBox
                      title="Lâu hơn bình thường"
                      detail={`Đã chờ ${scriptElapsed} giây mà chưa có kịch bản. Worker có thể đang bận hoặc không chạy. Hệ thống vẫn đang tiếp tục thử — bạn có thể bấm “Thử lại” để gửi lại yêu cầu.`}
                      hint="Nếu chạy local, hãy kiểm tra tiến trình worker (Arq/Redis) còn sống không."
                      actions={
                        <Button variant="primary" size="sm" icon="refresh-cw" onClick={retryScript}>
                          Thử lại
                        </Button>
                      }
                    />
                  )}
                </div>
              )}
            </div>
          ) : (
            <ProducingView
              jobs={jobs}
              onRetry={onRetry}
              onDone={() => nav({ name: "review", series, episode, jobId: jobId ?? undefined })}
            />
          )}
        </div>

        {/* right: tone chat */}
        <ToneChat />
      </div>
    </div>
  );
}
