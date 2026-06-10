"use client";

// ===== Screen 6: Final Review & Publish (ported from screen-review.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Progress, Placeholder, Segmented } from "@/components/ui";
import { SERIES, type Nav, type Route } from "@/lib/data";

function VideoPlayer({ title }: { title: string }) {
  const [playing, setPlaying] = React.useState(false);
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
          onClick={() => setPlaying((p) => !p)}
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
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", background: "var(--surface)" }}>
        <button className="icon-btn" style={{ width: 34, height: 34 }} onClick={() => setPlaying((p) => !p)}>
          <Icon name={playing ? "pause" : "play"} size={17} />
        </button>
        <span className="mono subtle" style={{ fontSize: 12 }}>
          0:00
        </span>
        <div className="progress" style={{ flex: 1, height: 6 }}>
          <div className="progress-fill" style={{ width: playing ? "34%" : "0%", background: "var(--brand)", transition: "width 1s linear" }} />
        </div>
        <span className="mono subtle" style={{ fontSize: 12 }}>
          9:42
        </span>
        <button className="icon-btn" style={{ width: 34, height: 34 }}>
          <Icon name="maximize" size={16} />
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

export function ReviewScreen({ nav, route }: { nav: Nav; route: Route }) {
  const series = route.series || SERIES[0];
  const episode = route.episode || series.episodes[2];
  const [stage, setStage] = React.useState<"idle" | "uploading" | "done">("idle");
  const [progress, setProgress] = React.useState(0);
  const [thumb, setThumb] = React.useState(0);
  const [tags, setTags] = React.useState<string[]>(["tôngiáo", "lịchsử", "vănminhcổđại", "sumer"]);
  const [vis, setVis] = React.useState("public");

  // TODO(backend): replace this simulated upload with api.publishToYouTube().
  React.useEffect(() => {
    if (stage !== "uploading") return;
    const iv = setInterval(
      () =>
        setProgress((p) => {
          const n = p + 6 + Math.random() * 8;
          if (n >= 100) {
            clearInterval(iv);
            setTimeout(() => setStage("done"), 400);
            return 100;
          }
          return n;
        }),
      260,
    );
    return () => clearInterval(iv);
  }, [stage]);

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
          <h2 style={{ fontSize: 24, marginBottom: 8 }}>Đã xuất bản lên YouTube! 🎉</h2>
          <p className="muted" style={{ fontSize: 15, marginBottom: 22, lineHeight: 1.6 }}>
            “{episode.title}” đang được xử lý trên YouTube và sẽ public trong vài phút.
          </p>
          <div className="card" style={{ padding: 14, display: "flex", alignItems: "center", gap: 12, textAlign: "left", boxShadow: "none", marginBottom: 22 }}>
            <Placeholder label="thumbnail" style={{ width: 96, height: 54, flex: "none" }} rounded="rounded-lg" />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 700, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {episode.title}
              </div>
              <a href="#" onClick={(e) => e.preventDefault()} className="mono" style={{ fontSize: 12, color: "var(--brand)" }}>
                youtube.com/watch?v=r33lo…
              </a>
            </div>
            <Button variant="secondary" size="sm" icon="external-link">
              Mở
            </Button>
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

      <div style={{ display: "grid", gridTemplateColumns: "1.25fr 1fr", gap: 22, alignItems: "start" }}>
        {/* left: player + thumbs */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <VideoPlayer title={episode.title} />
          <Card style={{ padding: 16 }}>
            <div className="label" style={{ marginBottom: 10 }}>
              Chọn thumbnail
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10 }}>
              {[0, 1, 2].map((i) => (
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
                  <Placeholder label={`thumb ${i + 1}`} style={{ aspectRatio: "16/9" }} rounded="rounded-lg" />
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
            <button className="btn btn-secondary btn-sm" style={{ marginTop: 10, fontSize: 12.5 }}>
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
              <input className="field" defaultValue={`${episode.title} | ${series.name}`} />
            </div>
            <div>
              <span className="label">Mô tả</span>
              <textarea
                className="field"
                rows={5}
                defaultValue={`Khám phá ${episode.title.toLowerCase()} trong hành trình tìm hiểu ${series.topic.toLowerCase()}.\n\n📌 Mục lục\n00:00 Mở đầu\n01:20 Bối cảnh lịch sử\n04:45 Những vị thần chính\n08:10 Di sản còn lại\n\n#Reelo #${series.topic.replace(/\s/g, "")}`}
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
                onChange={setVis}
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
                  <Icon name="loader" size={16} className="spin" style={{ color: "var(--brand)" }} /> Đang tải lên YouTube…
                </span>
                <span className="mono" style={{ fontSize: 13, fontWeight: 700 }}>
                  {Math.round(progress)}%
                </span>
              </div>
              <Progress value={progress} height={9} />
            </div>
          ) : (
            <button
              className="btn btn-primary btn-lg"
              style={{ width: "100%", height: 54, fontSize: 16 }}
              onClick={() => {
                setProgress(0);
                setStage("uploading");
              }}
            >
              <Icon name="youtube" size={22} /> Upload lên YouTube
            </button>
          )}
          <p className="subtle" style={{ fontSize: 12, textAlign: "center", marginTop: 10 }}>
            Kết nối tới kênh “{series.name}” · Tài khoản đã xác thực
          </p>
        </Card>
      </div>
    </div>
  );
}
