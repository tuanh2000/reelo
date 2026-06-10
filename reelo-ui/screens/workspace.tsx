"use client";

// ===== Screen 5: Script Workspace — pipeline + editor + chat (ported from screen-workspace.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Progress, Placeholder, StatusPill, Segmented, ChatBubble } from "@/components/ui";
import { SERIES, PIPELINE, SCRIPT_SEGMENTS, GEN_JOBS, type Nav, type Route, type GenJob, type ScriptSegment } from "@/lib/data";

// Web-photo image providers (web-commons; the web-* family) offer a human image
// curation step before produce; AI providers (kie/gemini/openai/sd) auto-pick.
function isWebPhotoProvider(providerId: string): boolean {
  return providerId.startsWith("web-");
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
          <button className="icon-btn" style={{ width: 30, height: 30 }} title="Tạo lại văn bản">
            <Icon name="refresh-cw" size={15} />
          </button>
          <button className="icon-btn" style={{ width: 30, height: 30 }} title="Nghe thử giọng đọc">
            <Icon name="volume-2" size={15} />
          </button>
          <button className="icon-btn" style={{ width: 30, height: 30 }} title="Thêm tùy chọn">
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
          <button className="btn btn-secondary btn-sm" style={{ width: "100%", marginTop: 8, fontSize: 12.5 }}>
            <Icon name="wand-sparkles" size={14} /> Tạo lại ảnh
          </button>
        </div>
      </div>
    </div>
  );
}

function JobRow({ job }: { job: GenJob }) {
  const stateMap = {
    done: { c: "#16a34a", t: "Xong", ic: "check-circle-2" },
    running: { c: "var(--brand)", t: "Đang chạy", ic: "loader" },
    queued: { c: "var(--text-3)", t: "Trong hàng đợi", ic: "clock" },
    error: { c: "#dc2626", t: "Lỗi", ic: "alert-triangle" },
  } as const;
  const st = stateMap[job.state];
  return (
    <div className="card" style={{ padding: 14, boxShadow: "none", display: "flex", alignItems: "center", gap: 13 }}>
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
        <Progress value={job.progress} height={6} tone={job.state === "error" ? "#dc2626" : job.state === "done" ? "#16a34a" : "var(--brand)"} />
      </div>
      {job.state === "error" && (
        <Button variant="soft" size="sm" icon="refresh-cw">
          Thử lại
        </Button>
      )}
    </div>
  );
}

function ProducingView({ jobs, onDone }: { jobs: GenJob[]; onDone: () => void }) {
  const allDone = jobs.every((j) => j.state === "done");
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
          <Icon name={allDone ? "party-popper" : "loader"} size={24} className={allDone ? "" : "spin"} />
        </span>
        <div style={{ flex: 1 }}>
          <h3 style={{ fontSize: 17 }}>{allDone ? "Đã dựng xong video!" : "Đang sản xuất tập này…"}</h3>
          <p className="muted" style={{ fontSize: 13.5, marginTop: 3 }}>
            {allDone
              ? "Tất cả asset đã sẵn sàng. Sang bước duyệt để xuất bản."
              : "Bạn có thể đóng tab — tiến độ được lưu tự động và tiếp tục sau."}
          </p>
        </div>
        {allDone && (
          <Button variant="primary" size="md" icon="arrow-right" onClick={onDone}>
            Tiếp tục duyệt
          </Button>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {jobs.map((j) => (
          <JobRow key={j.id} job={j} />
        ))}
      </div>

      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <Icon name="music-2" size={17} style={{ color: "var(--brand)" }} />
          <span style={{ fontWeight: 700, fontSize: 14 }}>Nghe thử giọng đọc</span>
          <Badge tone="green" className="ml-auto" style={{ marginLeft: "auto" }}>
            ElevenLabs
          </Badge>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button className="btn btn-primary" style={{ width: 42, height: 42, borderRadius: 999, padding: 0 }}>
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
  const [tone, setTone] = React.useState("formal");
  const [len, setLen] = React.useState(2);
  const [msgs, setMsgs] = React.useState<{ role: "ai" | "user"; text: string }[]>([
    { role: "ai", text: "Mình có thể chỉnh giọng văn, độ dài và tone cho tập này. Bạn muốn thay đổi gì?" },
  ]);
  const [input, setInput] = React.useState("");
  const send = (t?: string) => {
    const text = (t ?? input).trim();
    if (!text) return;
    // TODO(backend): wire to the real LLM to actually rewrite the script.
    setMsgs((m) => [
      ...m,
      { role: "user", text },
      { role: "ai", text: "Đã áp dụng! Mình viết lại đoạn mở đầu theo hướng đó, bạn xem trong editor nhé." },
    ]);
    setInput("");
  };
  return (
    <Card style={{ display: "flex", flexDirection: "column", overflow: "hidden", height: "100%" }}>
      <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 9 }}>
        <Icon name="sliders-horizontal" size={17} style={{ color: "var(--brand)" }} />
        <h3 style={{ fontSize: 15 }}>Tinh chỉnh văn phong</h3>
      </div>
      <div style={{ padding: 16, borderBottom: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 14 }}>
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
        {msgs.map((m, i) => (
          <ChatBubble key={i} role={m.role}>
            {m.text}
          </ChatBubble>
        ))}
      </div>
      <div style={{ padding: 12, borderTop: "1px solid var(--border)", display: "flex", gap: 8 }}>
        <input
          className="field"
          placeholder="Vd: viết hài hước hơn…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <Button variant="primary" icon="send" className="btn-icon" onClick={() => send()} />
      </div>
    </Card>
  );
}

export function WorkspaceScreen({ nav, route }: { nav: Nav; route: Route }) {
  const series = route.series || SERIES[0];
  const episode = route.episode || series.episodes.find((e) => e.status !== "published") || series.episodes[0];
  const [stage, setStage] = React.useState<"edit" | "producing">("edit");
  const [jobs, setJobs] = React.useState<GenJob[]>(GEN_JOBS);

  // animate jobs when producing
  // TODO(backend): replace this timer-driven simulation with api.startGeneration()
  // + api.pollGeneration() to reflect real job progress.
  React.useEffect(() => {
    if (stage !== "producing") return;
    setJobs(GEN_JOBS.map((j) => ({ ...j })));
    const iv = setInterval(() => {
      setJobs((prev) => {
        const next = prev.map((j) => ({ ...j }));
        const running = next.find((j) => j.state === "running");
        if (running) {
          running.progress = Math.min(100, running.progress + 7 + Math.random() * 9);
          if (running.progress >= 100) {
            running.progress = 100;
            running.state = "done";
            const q = next.find((j) => j.state === "queued");
            if (q) q.state = "running";
          }
        } else {
          const q = next.find((j) => j.state === "queued");
          if (q) q.state = "running";
        }
        return next;
      });
    }, 420);
    return () => clearInterval(iv);
  }, [stage]);

  const doneIds =
    stage === "producing"
      ? [
          "script",
          ...(jobs.find((j) => j.id === "j-render")?.state === "done"
            ? ["voice", "images", "assemble"]
            : jobs.every((j) => j.id === "j-render" || j.state === "done")
              ? ["voice", "images"]
              : ["voice"]),
        ]
      : ["script"];
  const activeId = stage === "producing" ? (jobs.every((j) => j.state === "done") ? "review" : "images") : "script";

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
            onClick={() => {
              // Web-photo image providers (web-commons) curate real photos first:
              // route to the image-selection step. AI providers produce directly.
              if (isWebPhotoProvider(series.providers.image)) {
                nav({ name: "image-select", series, episode });
              } else {
                setStage("producing");
              }
            }}
          >
            {isWebPhotoProvider(series.providers.image) ? "Chọn ảnh & Sản xuất" : "Sản xuất tập này"}
          </Button>
        ) : (
          <Button variant="secondary" size="md" icon="pen-line" onClick={() => setStage("edit")}>
            Sửa kịch bản
          </Button>
        )}
      </div>

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
                <span style={{ fontWeight: 700, fontSize: 14 }}>Kịch bản · {SCRIPT_SEGMENTS.length} đoạn</span>
                <span className="subtle" style={{ fontSize: 12.5, marginLeft: "auto" }}>
                  Mỗi đoạn gắn 1 hình ảnh
                </span>
              </div>
              {SCRIPT_SEGMENTS.map((seg, i) => (
                <SegmentCard key={seg.id} seg={seg} idx={i} />
              ))}
              <button className="btn btn-ghost btn-md" style={{ border: "1.5px dashed var(--border-strong)", color: "var(--text-2)" }}>
                <Icon name="plus" size={17} /> Thêm đoạn
              </button>
            </div>
          ) : (
            <ProducingView jobs={jobs} onDone={() => nav({ name: "review", series, episode })} />
          )}
        </div>

        {/* right: tone chat */}
        <ToneChat />
      </div>
    </div>
  );
}
