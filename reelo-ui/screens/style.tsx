"use client";

// ===== Screen 4: Style Studio (ported from screen-style.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Placeholder } from "@/components/ui";
import { type Nav, type Route } from "@/lib/data";
import {
  inferStyle,
  approveSeries,
  uploadVoiceSample,
  specToSeries,
  ApiError,
  type ImageStyleSpec,
  type VoiceConfigSpec,
  type SeriesConfig,
} from "@/lib/api";

interface StylePreset {
  id: string;
  name: string;
  desc: string;
  // A short English base prompt fed to the image provider (D4 base_prompt).
  base_prompt: string;
  palette: string[];
}

const STYLE_PRESETS: StylePreset[] = [
  { id: "cinematic", name: "Điện ảnh", desc: "Tông màu tương phản, ánh sáng kịch tính, chiều sâu trường ảnh nông. Cảm giác như một bộ phim bom tấn.", base_prompt: "cinematic film still, dramatic lighting, high contrast, shallow depth of field, anamorphic", palette: ["#0e1726", "#1f3a5f", "#c2703d", "#e8a87c", "#f2e8d5"] },
  { id: "documentary", name: "Tài liệu", desc: "Màu trung thực, tông đất trầm ấm, ánh sáng tự nhiên. Phù hợp nội dung lịch sử, khoa học nghiêm túc.", base_prompt: "documentary photography, natural lighting, earthy warm tones, realistic, archival", palette: ["#2b2620", "#5c4a36", "#9c7a4d", "#c9b48a", "#ece3d0"] },
  { id: "animated", name: "Hoạt hình", desc: "Màu phẳng tươi sáng, đường nét rõ, độ bão hòa cao. Vui mắt, thân thiện, dễ tiếp cận.", base_prompt: "flat illustration, vivid saturated colors, clean bold outlines, friendly cartoon style", palette: ["#ff5d5d", "#ffb443", "#3ec6ff", "#7b61ff", "#1dd3a7"] },
  { id: "minimal", name: "Tối giản", desc: "Bảng màu đơn sắc với một điểm nhấn, nhiều khoảng trống. Sạch sẽ, hiện đại, cao cấp.", base_prompt: "minimalist design, monochrome with single accent color, lots of negative space, modern, premium", palette: ["#111114", "#3a3a40", "#8a8a92", "#d9d9de", "#ef3e36"] },
  { id: "vintage", name: "Cổ điển", desc: "Sắc nâu sepia, grain nhẹ, ám vàng hoài niệm. Gợi không khí xưa cũ, trầm mặc.", base_prompt: "vintage photograph, sepia tones, subtle film grain, nostalgic warm cast, aged", palette: ["#3a2c1e", "#6b4f33", "#a8824f", "#d8b77a", "#efe4c8"] },
  { id: "noir", name: "Tương phản cao", desc: "Đen trắng mạnh mẽ với điểm nhấn đỏ. Bí ẩn, căng thẳng, gây ấn tượng tức thì.", base_prompt: "high-contrast black and white noir, single red accent, moody, dramatic shadows", palette: ["#0a0a0c", "#26262b", "#73737a", "#e6e6ea", "#e8332b"] },
];

interface Upload {
  id: string;
  label: string;
  file: File;
}

export function StyleScreen({ nav, route }: { nav: Nav; route: Route }) {
  const draft = route.draft; // present in the create flow
  const series = route.series; // present when re-styling an existing series
  const headerName = series?.name || draft?.name || "Series mới";

  const [preset, setPreset] = React.useState("cinematic");
  const [uploads, setUploads] = React.useState<Upload[]>([]);
  const [drag, setDrag] = React.useState(false);
  const cur = STYLE_PRESETS.find((p) => p.id === preset)!;

  // Style inferred from uploaded reference images (POST /style/infer). Falls back
  // to the chosen preset's palette/description when no images were uploaded.
  const [inferred, setInferred] = React.useState<{ palette: string[]; description: string } | null>(null);
  const [inferring, setInferring] = React.useState(false);
  const [approving, setApproving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const aspect = draft?.aspect || "16:9";

  // Run inference whenever the set of uploaded reference images changes.
  const runInfer = React.useCallback((files: File[]) => {
    if (files.length === 0) {
      setInferred(null);
      return;
    }
    setInferring(true);
    setError(null);
    inferStyle(files)
      .then(setInferred)
      .catch((e) => {
        setError(e instanceof ApiError ? e.message : "Không suy ra được style từ ảnh mẫu");
      })
      .finally(() => setInferring(false));
  }, []);

  const addFiles = (files: FileList | null) => {
    if (!files || files.length === 0) {
      // Drag/drop without files or programmatic click: open the file picker.
      fileInputRef.current?.click();
      return;
    }
    const added: Upload[] = Array.from(files).map((file, i) => ({
      id: "u" + Date.now() + "_" + i,
      label: file.name,
      file,
    }));
    setUploads((u) => {
      const next = [...u, ...added];
      runInfer(next.map((x) => x.file));
      return next;
    });
  };

  const removeUpload = (id: string) => {
    setUploads((u) => {
      const next = u.filter((x) => x.id !== id);
      runInfer(next.map((x) => x.file));
      return next;
    });
  };

  // Effective palette/description shown + persisted: inferred (from images) when
  // available, else the preset's static values.
  const palette = inferred?.palette?.length ? inferred.palette : cur.palette;
  const description = inferred?.description || cur.desc;

  // FINAL APPROVE — persist the new series via approveSeries(outline + config),
  // then go to its real project page. Only runs in the create flow (draft set).
  const onApply = async () => {
    if (!draft) {
      // Editing an existing series' style: no create-flow persistence here yet.
      nav({ name: "project", ...(series ? { series } : {}), toast: `Đã áp dụng style "${cur.name}"` });
      return;
    }
    setApproving(true);
    setError(null);

    const image_style: ImageStyleSpec = {
      preset_id: preset,
      base_prompt: cur.base_prompt,
      palette,
      description,
      aspect,
    };
    // Per-series toolset chosen on the Setup screen (script/image/voice). The
    // backend aligns voice.provider with providers.voice; the voice sample (if a
    // clone provider) is uploaded right after the series is created (below).
    const providers = draft.providers;
    const voice: VoiceConfigSpec = {
      provider: providers?.voice || "edge",
      voice_id: "",
      mode: providers?.voice === "omnivoice" ? "clone" : "preset",
    };
    const config: SeriesConfig = {
      skill: draft.skill || "explain",
      language: draft.language || "vi",
      target_minutes: draft.target_minutes || 10,
      density: draft.density || "standard",
      aspect,
      ...(providers ? { providers } : {}),
      voice,
      image_style,
    };

    try {
      const spec = await approveSeries(draft.name, draft.topic || draft.name, draft.outline, config);
      const created = specToSeries(spec);
      // Upload the staged OmniVoice clone sample now that the series exists.
      if (draft.voiceSample) {
        try {
          await uploadVoiceSample(
            spec.series_id,
            draft.voiceSample.file,
            draft.voiceSample.transcript,
            draft.voiceSample.language,
          );
        } catch {
          /* best-effort: the series is created; the user can re-upload from the
             project screen if this fails. */
        }
      }
      nav({ name: "project", series: created, toast: `Đã lưu series "${created.name}"` });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Lưu series thất bại. Vui lòng thử lại.");
      setApproving(false);
    }
  };

  return (
    <div className="page page-wide" style={{ paddingBottom: 40 }}>
      <div style={{ marginBottom: 6 }}>
        <Badge tone="neutral" icon="folder">
          {headerName}
        </Badge>
      </div>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 22 }}>Style Studio</h2>
        <p className="muted" style={{ fontSize: 14, marginTop: 4 }}>
          Tải ảnh mẫu để AI suy ra phong cách hình ảnh, hoặc chọn nhanh một preset. Toàn bộ hình trong series sẽ theo
          style này.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 22, alignItems: "start" }}>
        {/* LEFT */}
        <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
          {/* upload */}
          <div>
            <div className="label" style={{ marginBottom: 10 }}>
              Ảnh tham chiếu
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg"
              multiple
              style={{ display: "none" }}
              onChange={(e) => {
                addFiles(e.target.files);
                e.target.value = ""; // allow re-selecting the same file
              }}
            />
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                addFiles(e.dataTransfer.files);
              }}
              onClick={() => fileInputRef.current?.click()}
              style={{
                border: `2px dashed ${drag ? "var(--brand)" : "var(--border-strong)"}`,
                background: drag ? "var(--brand-tint)" : "var(--surface-2)",
                borderRadius: 16,
                padding: "34px 20px",
                textAlign: "center",
                cursor: "pointer",
                transition: ".15s",
              }}
            >
              <span
                style={{
                  width: 52,
                  height: 52,
                  borderRadius: 14,
                  background: "var(--surface)",
                  color: "var(--brand)",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  marginBottom: 12,
                  boxShadow: "var(--shadow-sm)",
                }}
              >
                <Icon name="image-plus" size={24} />
              </span>
              <div style={{ fontWeight: 700, fontSize: 15 }}>Kéo &amp; thả ảnh vào đây</div>
              <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
                hoặc bấm để chọn từ máy · PNG, JPG tối đa 10MB
              </div>
            </div>

            {uploads.length > 0 && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 10, marginTop: 12 }}>
                {uploads.map((u) => (
                  <div key={u.id} style={{ position: "relative" }}>
                    <Placeholder label={u.label} style={{ aspectRatio: "1/1" }} rounded="rounded-lg" />
                    <button
                      onClick={() => removeUpload(u.id)}
                      style={{
                        position: "absolute",
                        top: 6,
                        right: 6,
                        width: 24,
                        height: 24,
                        borderRadius: 999,
                        border: "none",
                        background: "rgba(0,0,0,.6)",
                        color: "#fff",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        cursor: "pointer",
                      }}
                    >
                      <Icon name="x" size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* presets */}
          <div>
            <div className="label" style={{ marginBottom: 10 }}>
              Hoặc chọn nhanh preset
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
              {STYLE_PRESETS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setPreset(p.id)}
                  className="card card-hover"
                  style={{
                    padding: 0,
                    overflow: "hidden",
                    cursor: "pointer",
                    textAlign: "left",
                    border: `2px solid ${preset === p.id ? "var(--brand)" : "var(--border)"}`,
                  }}
                >
                  <Placeholder label="ảnh mẫu" rounded="" style={{ aspectRatio: "16/9", borderLeft: "none", borderRight: "none", borderTop: "none" }} />
                  <div style={{ padding: "10px 13px", display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 700, fontSize: 14 }}>{p.name}</span>
                    {preset === p.id && (
                      <span
                        style={{
                          marginLeft: "auto",
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
                        <Icon name="check" size={12} strokeWidth={3} />
                      </span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* RIGHT: inferred style */}
        <div style={{ position: "sticky", top: 22 }}>
          <Card style={{ padding: 18 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 16 }}>
              <Icon name="sparkles" size={18} style={{ color: "var(--brand)" }} />
              <h3 style={{ fontSize: 15.5 }}>Style được suy ra</h3>
              {inferring && <Icon name="loader" size={15} style={{ color: "var(--brand)", marginLeft: "auto" }} />}
            </div>

            <Placeholder label={`Preview · ${cur.name}`} style={{ aspectRatio: "16/9", marginBottom: 16 }} />

            <div className="label">Bảng màu</div>
            <div style={{ display: "flex", gap: 7, marginBottom: 16 }}>
              {palette.map((c, i) => (
                <div key={i} style={{ flex: 1 }}>
                  <div style={{ height: 40, background: c, borderRadius: 9, border: "1px solid var(--border)" }} />
                  <div className="mono subtle" style={{ fontSize: 9.5, textAlign: "center", marginTop: 4 }}>
                    {c.replace("#", "")}
                  </div>
                </div>
              ))}
            </div>

            <div className="label">Mô tả phong cách</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: "var(--text-2)", margin: "0 0 16px" }}>{description}</p>

            <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 18 }}>
              {[uploads.length ? `${uploads.length} ảnh mẫu` : "Từ preset", aspect, "Nhất quán cả series"].map((t) => (
                <Badge key={t} tone="brand">
                  {t}
                </Badge>
              ))}
            </div>

            {error && (
              <div
                className="subtle"
                style={{ fontSize: 12.5, color: "var(--danger, #ef3e36)", marginBottom: 12, display: "flex", alignItems: "center", gap: 6 }}
              >
                <Icon name="alert-triangle" size={13} /> {error}
              </div>
            )}

            <Button
              variant="primary"
              size="md"
              icon={approving ? "loader" : "check"}
              style={{ width: "100%" }}
              disabled={approving || inferring}
              onClick={onApply}
            >
              {approving ? "Đang lưu series…" : draft ? "Chốt & Lưu series" : "Áp dụng style này"}
            </Button>
          </Card>
        </div>
      </div>
    </div>
  );
}
