"use client";

// ===== Screen 4: Style Studio (ported from screen-style.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Placeholder } from "@/components/ui";
import { SERIES, type Nav, type Route } from "@/lib/data";

interface StylePreset {
  id: string;
  name: string;
  desc: string;
  palette: string[];
}

const STYLE_PRESETS: StylePreset[] = [
  { id: "cinematic", name: "Điện ảnh", desc: "Tông màu tương phản, ánh sáng kịch tính, chiều sâu trường ảnh nông. Cảm giác như một bộ phim bom tấn.", palette: ["#0e1726", "#1f3a5f", "#c2703d", "#e8a87c", "#f2e8d5"] },
  { id: "documentary", name: "Tài liệu", desc: "Màu trung thực, tông đất trầm ấm, ánh sáng tự nhiên. Phù hợp nội dung lịch sử, khoa học nghiêm túc.", palette: ["#2b2620", "#5c4a36", "#9c7a4d", "#c9b48a", "#ece3d0"] },
  { id: "animated", name: "Hoạt hình", desc: "Màu phẳng tươi sáng, đường nét rõ, độ bão hòa cao. Vui mắt, thân thiện, dễ tiếp cận.", palette: ["#ff5d5d", "#ffb443", "#3ec6ff", "#7b61ff", "#1dd3a7"] },
  { id: "minimal", name: "Tối giản", desc: "Bảng màu đơn sắc với một điểm nhấn, nhiều khoảng trống. Sạch sẽ, hiện đại, cao cấp.", palette: ["#111114", "#3a3a40", "#8a8a92", "#d9d9de", "#ef3e36"] },
  { id: "vintage", name: "Cổ điển", desc: "Sắc nâu sepia, grain nhẹ, ám vàng hoài niệm. Gợi không khí xưa cũ, trầm mặc.", palette: ["#3a2c1e", "#6b4f33", "#a8824f", "#d8b77a", "#efe4c8"] },
  { id: "noir", name: "Tương phản cao", desc: "Đen trắng mạnh mẽ với điểm nhấn đỏ. Bí ẩn, căng thẳng, gây ấn tượng tức thì.", palette: ["#0a0a0c", "#26262b", "#73737a", "#e6e6ea", "#e8332b"] },
];

interface Upload {
  id: string;
  label: string;
}

export function StyleScreen({ nav, route }: { nav: Nav; route: Route }) {
  const series = route.series || SERIES[0];
  const [preset, setPreset] = React.useState("cinematic");
  const [uploads, setUploads] = React.useState<Upload[]>([]);
  const [drag, setDrag] = React.useState(false);
  const cur = STYLE_PRESETS.find((p) => p.id === preset)!;

  // TODO(backend): send uploaded reference images to api.inferStyle() to get a
  // real palette + description instead of using the static preset data.
  const addUpload = () => setUploads((u) => [...u, { id: "u" + Date.now(), label: `Ảnh mẫu ${u.length + 1}` }]);

  return (
    <div className="page page-wide" style={{ paddingBottom: 40 }}>
      <div style={{ marginBottom: 6 }}>
        <Badge tone="neutral" icon="folder">
          {series.name}
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
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                addUpload();
              }}
              onClick={addUpload}
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
                      onClick={() => setUploads((us) => us.filter((x) => x.id !== u.id))}
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
            </div>

            <Placeholder label={`Preview · ${cur.name}`} style={{ aspectRatio: "16/9", marginBottom: 16 }} />

            <div className="label">Bảng màu</div>
            <div style={{ display: "flex", gap: 7, marginBottom: 16 }}>
              {cur.palette.map((c, i) => (
                <div key={i} style={{ flex: 1 }}>
                  <div style={{ height: 40, background: c, borderRadius: 9, border: "1px solid var(--border)" }} />
                  <div className="mono subtle" style={{ fontSize: 9.5, textAlign: "center", marginTop: 4 }}>
                    {c.replace("#", "")}
                  </div>
                </div>
              ))}
            </div>

            <div className="label">Mô tả phong cách</div>
            <p style={{ fontSize: 13.5, lineHeight: 1.6, color: "var(--text-2)", margin: "0 0 16px" }}>{cur.desc}</p>

            <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 18 }}>
              {[uploads.length ? `${uploads.length} ảnh mẫu` : "Từ preset", "16:9", "Nhất quán cả series"].map((t) => (
                <Badge key={t} tone="brand">
                  {t}
                </Badge>
              ))}
            </div>

            <Button
              variant="primary"
              size="md"
              icon="check"
              style={{ width: "100%" }}
              onClick={() => nav({ name: "project", series, toast: `Đã áp dụng style "${cur.name}"` })}
            >
              Áp dụng style này
            </Button>
          </Card>
        </div>
      </div>
    </div>
  );
}
