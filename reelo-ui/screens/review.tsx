"use client";

// ===== Screen 6: Final Review & Publish (ported from screen-review.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Progress, Placeholder, Segmented, EmptyState } from "@/components/ui";
import { type Nav, type Route, type Series, type Episode } from "@/lib/data";
import { getEpisode, publishToYouTube, type EpisodeDetail, type ExportResult } from "@/lib/api";

// Real <video> player when a signed final.mp4 URL is available; otherwise a
// placeholder (asset not rendered yet / offline demo).
function VideoPlayer({ title, src }: { title: string; src: string | null }) {
  const [playing, setPlaying] = React.useState(false);
  const ref = React.useRef<HTMLVideoElement>(null);
  const toggle = () => {
    const v = ref.current;
    if (!v) return;
    if (v.paused) void v.play().catch(() => {});
    else v.pause();
  };
  if (src) {
    return (
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video
          ref={ref}
          src={src}
          controls
          style={{ width: "100%", aspectRatio: "16/9", background: "#0b0b0d", display: "block" }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
        />
      </div>
    );
  }
  return (
    <div className="card" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ position: "relative", aspectRatio: "16/9", background: "#0b0b0d", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div className="ph-stripes" style={{ position: "absolute", inset: 0, opacity: 0.25 }} />
        <span style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <span
            className="mono"
            style={{
              fontSize: 12,
              color: "rgba(255,255,255,.5)",
              background: "rgba(255,255,255,.06)",
              padding: "5px 11px",
              borderRadius: 7,
              border: "1px solid rgba(255,255,255,.12)",
            }}
          >
            preview · {title}
          </span>
        </span>
        <button
          onClick={toggle}
          style={{
            position: "relative",
            width: 72,
            height: 72,
            borderRadius: 999,
            background: "var(--brand)",
            border: "none",
            color: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 10px 30px -6px color-mix(in oklab,var(--brand) 70%,transparent)",
          }}
        >
          <Icon name={playing ? "pause" : "play"} size={28} />
        </button>
      </div>
    </div>
  );
}

function TagInput({ tags, setTags }: { tags: string[]; setTags: (t: string[]) => void }) {
  const [val, setVal] = React.useState("");
  return (
    <div className="field" style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", padding: "8px 10px" }}>
      {tags.map((t) => (
        <span key={t} className="badge badge-neutral" style={{ paddingRight: 5 }}>
          #{t}
          <button
            onClick={() => setTags(tags.filter((x) => x !== t))}
            style={{ border: "none", background: "none", color: "inherit", display: "inline-flex", cursor: "pointer" }}
          >
            <Icon name="x" size={12} />
          </button>
        </span>
      ))}
      <input
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === ",") && val.trim()) {
            e.preventDefault();
            setTags([...new Set([...tags, val.trim().replace(/,/g, "")])]);
            setVal("");
          }
        }}
        placeholder={tags.length ? "" : "Thêm tag, Enter để xác nhận"}
        style={{ border: "none", outline: "none", background: "transparent", color: "var(--text)", flex: 1, minWidth: 120, fontSize: 14 }}
      />
    </div>
  );
}

function asStr(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function asTags(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((t): t is string => typeof t === "string") : [];
}

export function ReviewScreen({ nav, route }: { nav: Nav; route: Route }) {
  // Publishing a real episode needs a real series; otherwise show an empty state
  // instead of mock data.
  if (!route.series) {
    return (
      <EmptyState
        icon="youtube"
        title="Chưa chọn series"
        desc="Hãy mở một series từ Bảng điều khiển để duyệt và xuất bản tập."
        actionLabel="Về Bảng điều khiển"
        onAction={() => nav({ name: "dashboard" })}
      />
    );
  }
  return <ReviewInner nav={nav} route={route} series={route.series} />;
}

function ReviewInner({ nav, route, series }: { nav: Nav; route: Route; series: Series }) {
  const episode: Episode =
    route.episode ||
    series.episodes.find((e) => e.status === "assembled" || e.status === "published") ||
    series.episodes[0];
  const [stage, setStage] = React.useState<"idle" | "uploading" | "done">("idle");
  const [thumb, setThumb] = React.useState(0);
  const [vis, setVis] = React.useState<"public" | "unlisted" | "private">("public");
  const [error, setError] = React.useState<string | null>(null);

  // Live asset URLs + AI-suggested metadata (fetched from the episode).
  const [detail, setDetail] = React.useState<EpisodeDetail | null>(null);
  const [result, setResult] = React.useState<ExportResult | null>(null);
  const [title, setTitle] = React.useState(`${episode.title} | ${series.name}`);
  const [desc, setDesc] = React.useState("");
  const [tags, setTags] = React.useState<string[]>([]);
  const seeded = React.useRef(false);

  React.useEffect(() => {
    if (!route.episode) return; // offline/demo seed → keep placeholders
    let alive = true;
    getEpisode(episode.id)
      .then((d) => {
        if (!alive) return;
        setDetail(d);
        // Seed editable metadata from the episode's youtube block once.
        if (!seeded.current) {
          const yt = d.episode.youtube || {};
          if (asStr(yt.title)) setTitle(asStr(yt.title));
          if (asStr(yt.description)) setDesc(asStr(yt.description));
          const t = asTags(yt.tags);
          if (t.length) setTags(t);
          seeded.current = true;
        }
      })
      .catch((e) => {
        if (!alive) return;
        setError(e instanceof Error ? e.message : "Không tải được dữ liệu tập.");
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode.id]);

  const thumbnails = detail?.assets.thumbnails ?? [];
  const videoUrl = result?.videoUrl ?? detail?.assets.videoUrl ?? null;
  const srtUrl = result?.srtUrl ?? detail?.assets.srtUrl ?? null;

  // Export (v1): backend returns signed URLs + metadata; the user uploads to YT.
  const onExport = async () => {
    setStage("uploading");
    setError(null);
    try {
      const res = await publishToYouTube(series.id, episode.id, {
        title,
        description: desc,
        tags,
        visibility: vis,
        thumbnailIndex: thumb,
      });
      setResult(res);
      setStage("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Xuất video thất bại.");
      setStage("idle");
    }
  };

  if (stage === "done") {
    return (
      <div className="page" style={{ maxWidth: 620, paddingTop: 60 }}>
        <Card className="fade-up" style={{ padding: 36, textAlign: "center" }}>
          <span
            style={{
              width: 72,
              height: 72,
              borderRadius: 999,
              background: "color-mix(in oklab,#16a34a 16%,transparent)",
              color: "#16a34a",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              marginBottom: 18,
            }}
          >
            <Icon name="check" size={38} strokeWidth={3} />
          </span>
          <h2 style={{ fontSize: 24, marginBottom: 8 }}>Video đã sẵn sàng! 🎉</h2>
          <p className="muted" style={{ fontSize: 15, marginBottom: 22, lineHeight: 1.6 }}>
            “{title}” đã được xuất. Tải video, phụ đề và thumbnail bên dưới rồi đăng lên YouTube.
          </p>
          <div className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10, textAlign: "left", boxShadow: "none", marginBottom: 22 }}>
            {videoUrl && (
              <a className="btn btn-secondary btn-sm" href={videoUrl} target="_blank" rel="noreferrer" download>
                <Icon name="download" size={15} /> Tải video (.mp4)
              </a>
            )}
            {srtUrl && (
              <a className="btn btn-secondary btn-sm" href={srtUrl} target="_blank" rel="noreferrer" download>
                <Icon name="captions" size={15} /> Tải phụ đề (.srt)
              </a>
            )}
            {result?.thumbnailUrl && (
              <a className="btn btn-secondary btn-sm" href={result.thumbnailUrl} target="_blank" rel="noreferrer" download>
                <Icon name="image" size={15} /> Tải thumbnail
              </a>
            )}
          </div>
          <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
            <Button variant="secondary" size="md" icon="layout-dashboard" onClick={() => nav({ name: "dashboard" })}>
              Về bảng điều khiển
            </Button>
            <Button
              variant="primary"
              size="md"
              icon="plus"
              onClick={() => nav({ name: "workspace", series, episode: series.episodes.find((e) => e.status === "draft") })}
            >
              Làm tập tiếp theo
            </Button>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="page page-wide">
      <div style={{ marginBottom: 18 }}>
        <h2 style={{ fontSize: 22 }}>Duyệt &amp; Xuất bản</h2>
        <p className="muted" style={{ fontSize: 14, marginTop: 4 }}>
          Xem lại video hoàn chỉnh và chỉnh metadata trước khi đăng. Bạn duyệt bước cuối — phần còn lại AI đã lo.
        </p>
      </div>

      {error && (
        <div className="card" style={{ padding: 14, marginBottom: 16, color: "#dc2626", display: "flex", gap: 8 }}>
          <Icon name="alert-triangle" size={16} /> {error}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1.25fr 1fr", gap: 22, alignItems: "start" }}>
        {/* left: player + thumbs */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <VideoPlayer title={episode.title} src={videoUrl} />
          <Card style={{ padding: 16 }}>
            <div className="label" style={{ marginBottom: 10 }}>
              Chọn thumbnail
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10 }}>
              {(thumbnails.length ? thumbnails : [null, null, null]).map((url, i) => (
                <button
                  key={i}
                  onClick={() => setThumb(i)}
                  style={{
                    padding: 0,
                    border: `2px solid ${thumb === i ? "var(--brand)" : "transparent"}`,
                    borderRadius: 12,
                    overflow: "hidden",
                    position: "relative",
                    background: "none",
                  }}
                >
                  {url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={url}
                      alt={`thumb ${i + 1}`}
                      style={{ width: "100%", aspectRatio: "16/9", objectFit: "cover", display: "block", borderRadius: 12 }}
                    />
                  ) : (
                    <Placeholder label={`thumb ${i + 1}`} style={{ aspectRatio: "16/9" }} rounded="rounded-lg" />
                  )}
                  {thumb === i && (
                    <span
                      style={{
                        position: "absolute",
                        top: 6,
                        right: 6,
                        width: 20,
                        height: 20,
                        borderRadius: 999,
                        background: "var(--brand)",
                        color: "#fff",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      <Icon name="check" size={13} strokeWidth={3} />
                    </span>
                  )}
                </button>
              ))}
            </div>
            {/* Generate more thumbnails — no backend endpoint yet. */}
            <button
              className="btn btn-secondary btn-sm"
              style={{ marginTop: 10, fontSize: 12.5, opacity: 0.5, cursor: "default" }}
              title="Sắp có"
              disabled
            >
              <Icon name="wand-sparkles" size={14} /> Tạo thêm bằng AI
            </button>
          </Card>
        </div>

        {/* right: metadata */}
        <Card style={{ padding: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 18 }}>
            <Icon name="youtube" size={20} style={{ color: "var(--brand)" }} />
            <h3 style={{ fontSize: 16 }}>Thông tin YouTube</h3>
            <Badge tone="brand" icon="sparkles" style={{ marginLeft: "auto" }}>
              AI gợi ý
            </Badge>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <span className="label">Tiêu đề</span>
              <input className="field" value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div>
              <span className="label">Mô tả</span>
              <textarea
                className="field"
                rows={5}
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
              />
            </div>
            <div>
              <span className="label">Tags</span>
              <TagInput tags={tags} setTags={setTags} />
            </div>
            <div>
              <span className="label">Chế độ hiển thị</span>
              <Segmented
                value={vis}
                onChange={(v) => setVis(v as "public" | "unlisted" | "private")}
                options={[
                  { value: "public", label: "Công khai", icon: "globe" },
                  { value: "unlisted", label: "Không liệt kê", icon: "link" },
                  { value: "private", label: "Riêng tư", icon: "lock" },
                ]}
              />
            </div>
          </div>

          <div className="divider" style={{ margin: "20px 0 16px" }} />

          {stage === "uploading" ? (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 14, display: "inline-flex", alignItems: "center", gap: 8 }}>
                  <Icon name="loader" size={16} className="spin" style={{ color: "var(--brand)" }} /> Đang xuất video…
                </span>
              </div>
              <Progress value={100} height={9} />
            </div>
          ) : (
            <button
              className="btn btn-primary btn-lg"
              style={{ width: "100%", height: 54, fontSize: 16 }}
              disabled={!videoUrl}
              onClick={onExport}
            >
              <Icon name="youtube" size={22} /> Xuất video
            </button>
          )}
          <p className="subtle" style={{ fontSize: 12, textAlign: "center", marginTop: 10 }}>
            {videoUrl
              ? "v1: tải video + metadata về để tự đăng lên YouTube."
              : "Video chưa dựng xong — quay lại bước sản xuất."}
          </p>
        </Card>
      </div>
    </div>
  );
}
