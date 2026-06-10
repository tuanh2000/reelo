"use client";

// ===== Screen: Media Curation (M2-12 / M2-13) — pick real photos OR video clips =====
//
// The Reelo differentiator: for web-* media providers the system does NOT
// auto-pick one media per segment. It shows ~9 license-clean candidates per
// segment and lets a human curate the on-topic media. Since M2-13 the grid MIXES
// real photos (web-commons, keyless) with real video clips (web-pexels, needs a
// Pexels key) so each segment can be a still image OR a video clip. Clips show a
// poster with a ▶ badge and hover-play the short preview if one is available. The
// first candidate is pre-selected, so a user who does nothing still gets a
// sensible default and the pipeline runs. This screen sits BETWEEN "scripted" and
// "produce": save the selection, then start generation and continue to the
// workspace producing view.

import React from "react";
import { Icon, Button, Card, Badge } from "@/components/ui";
import { SERIES, type Nav, type Route } from "@/lib/data";
import {
  getImageCandidates,
  saveImageSelection,
  startGeneration,
  ApiError,
  type ImageCandidate,
  type SegmentCandidates,
} from "@/lib/api";

function CandidateTile({
  candidate,
  selected,
  onClick,
  caption,
}: {
  candidate: ImageCandidate;
  selected: boolean;
  onClick: () => void;
  caption: string;
}) {
  const isVideo = candidate.media_type === "video";
  const posterUrl = isVideo ? candidate.poster_url || candidate.thumb_url : candidate.thumb_url;
  const videoRef = React.useRef<HTMLVideoElement>(null);

  // Optional hover-play of the short preview clip (M2-13). No-op for photos / when
  // no preview_url is offered; play() failures (autoplay policy) are ignored.
  const onEnter = () => {
    if (isVideo && candidate.preview_url && videoRef.current) {
      videoRef.current.currentTime = 0;
      void videoRef.current.play().catch(() => {});
    }
  };
  const onLeave = () => {
    if (videoRef.current) {
      videoRef.current.pause();
    }
  };

  return (
    <button
      onClick={onClick}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      title={caption}
      style={{
        position: "relative",
        padding: 0,
        border: `2.5px solid ${selected ? "var(--brand)" : "transparent"}`,
        borderRadius: 12,
        overflow: "hidden",
        cursor: "pointer",
        background: "var(--surface-2)",
        aspectRatio: "16 / 10",
        outline: selected ? "2px solid var(--brand-tint)" : "none",
        transition: ".15s",
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={posterUrl}
        alt={caption}
        loading="lazy"
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
      {isVideo && candidate.preview_url && (
        <video
          ref={videoRef}
          src={candidate.preview_url}
          muted
          loop
          playsInline
          preload="none"
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            objectFit: "cover",
            opacity: 0,
            transition: "opacity .15s",
          }}
          onPlaying={(e) => {
            (e.currentTarget as HTMLVideoElement).style.opacity = "1";
          }}
          onPause={(e) => {
            (e.currentTarget as HTMLVideoElement).style.opacity = "0";
          }}
        />
      )}
      {isVideo && (
        <span
          style={{
            position: "absolute",
            left: 6,
            bottom: 6,
            display: "flex",
            alignItems: "center",
            gap: 4,
            padding: "2px 7px 2px 6px",
            borderRadius: 999,
            background: "rgba(0,0,0,.62)",
            color: "#fff",
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          <Icon name="play" size={11} strokeWidth={3} />
          {candidate.duration ? `${Math.round(candidate.duration)}s` : "Clip"}
        </span>
      )}
      {selected && (
        <span
          style={{
            position: "absolute",
            top: 6,
            right: 6,
            width: 24,
            height: 24,
            borderRadius: 999,
            background: "var(--brand)",
            color: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Icon name="check" size={15} strokeWidth={3} />
        </span>
      )}
    </button>
  );
}

function SegmentBlock({
  seg,
  chosenId,
  onChoose,
}: {
  seg: SegmentCandidates;
  chosenId: string | null;
  onChoose: (candidateId: string) => void;
}) {
  return (
    <Card style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
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
            flex: "none",
          }}
        >
          {seg.index}
        </span>
        <span style={{ fontWeight: 700, fontSize: 14 }}>Đoạn {seg.index}</span>
        <Badge tone="neutral" icon="search" className="ml-auto" style={{ marginLeft: "auto" }}>
          {seg.query}
        </Badge>
      </div>
      {seg.text && (
        <p className="muted" style={{ fontSize: 13.5, lineHeight: 1.5 }}>
          {seg.text}
        </p>
      )}
      {seg.candidates.length === 0 ? (
        <div className="card" style={{ padding: 14, background: "var(--surface-2)", boxShadow: "none" }}>
          <span className="subtle" style={{ fontSize: 13 }}>
            <Icon name="image-off" size={15} /> Không tìm thấy ảnh/clip hợp lệ cho đoạn này — hệ
            thống sẽ tự chọn khi sản xuất.
          </span>
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 10,
          }}
        >
          {seg.candidates.map((c) => {
            const kind = c.media_type === "video" ? "Clip" : "Ảnh";
            const caption = `${kind} · ${c.author} · ${c.license}`;
            return (
              <div key={c.id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <CandidateTile
                  candidate={c}
                  selected={chosenId === c.id}
                  onClick={() => onChoose(c.id)}
                  caption={caption}
                />
                <a
                  className="subtle"
                  href={c.source_url || undefined}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    fontSize: 11,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    color: "inherit",
                    textDecoration: "none",
                  }}
                  title={caption}
                >
                  {kind} · {c.author} · {c.license}
                </a>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

export function ImageSelectScreen({ nav, route }: { nav: Nav; route: Route }) {
  const series = route.series || SERIES[0];
  const episode =
    route.episode || series.episodes.find((e) => e.status !== "published") || series.episodes[0];

  const [segments, setSegments] = React.useState<SegmentCandidates[] | null>(null);
  // chosen[segmentIndex] = candidateId
  const [chosen, setChosen] = React.useState<Record<number, string>>({});
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    getImageCandidates(episode.id)
      .then((res) => {
        if (!alive) return;
        setSegments(res.segments);
        const init: Record<number, string> = {};
        for (const s of res.segments) {
          if (s.chosen_id) init[s.index] = s.chosen_id;
          else if (s.candidates[0]) init[s.index] = s.candidates[0].id;
        }
        setChosen(init);
      })
      .catch((e) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 409) {
          // Generative provider — no selection step; go straight to producing.
          nav({ name: "workspace", series, episode });
          return;
        }
        setError(e instanceof Error ? e.message : "Không tải được danh sách ảnh.");
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [episode.id]);

  const choose = (index: number, candidateId: string) =>
    setChosen((prev) => ({ ...prev, [index]: candidateId }));

  const onSaveAndProduce = async () => {
    setSaving(true);
    setError(null);
    try {
      await saveImageSelection(episode.id, chosen);
      const { jobId } = await startGeneration(series.id, episode.id);
      // Hand the live job to the workspace so it opens straight into the
      // producing view and polls real progress.
      nav({
        name: "workspace",
        series,
        episode,
        jobId,
        producing: true,
        toast: "Đã lưu lựa chọn — bắt đầu sản xuất!",
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lưu lựa chọn thất bại.");
      setSaving(false);
    }
  };

  return (
    <div className="page page-wide" style={{ paddingBottom: 24 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 18 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 4 }}>
            <Badge tone="neutral" icon="folder">
              {series.name}
            </Badge>
            <Badge tone="green" icon="image">
              Ảnh + Clip · Web
            </Badge>
          </div>
          <h2 style={{ fontSize: 22 }}>Chọn ảnh hoặc clip cho từng đoạn</h2>
          <p className="muted" style={{ fontSize: 13.5, marginTop: 4 }}>
            Mỗi đoạn có ảnh thật (Commons, PD/CC) và clip thật (Pexels, CC0) trộn lẫn. Mục đầu
            được chọn sẵn — nhấp để đổi (clip có nhãn ▶), rồi bấm sản xuất.
          </p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <Button variant="secondary" size="md" icon="arrow-left" onClick={() => nav({ name: "workspace", series, episode })}>
            Quay lại
          </Button>
          <Button
            variant="primary"
            size="md"
            icon="clapperboard"
            disabled={loading || saving || !segments}
            onClick={onSaveAndProduce}
          >
            {saving ? "Đang lưu…" : "Lưu lựa chọn & Sản xuất"}
          </Button>
        </div>
      </div>

      {error && (
        <div
          className="card"
          style={{ padding: 14, marginBottom: 16, color: "#dc2626", display: "flex", gap: 8 }}
        >
          <Icon name="alert-triangle" size={16} /> {error}
        </div>
      )}

      {loading ? (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: 60 }}>
          <Icon name="loader" size={26} className="spin" style={{ color: "var(--brand)" }} />
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {(segments || []).map((seg) => (
            <SegmentBlock
              key={seg.index}
              seg={seg}
              chosenId={chosen[seg.index] ?? null}
              onChoose={(id) => choose(seg.index, id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
