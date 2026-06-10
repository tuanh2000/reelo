"use client";

// ===== Screen 1: Dashboard (ported from screen-dashboard.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, Progress, Placeholder, Segmented } from "@/components/ui";
import { Logo3D } from "@/components/logo";
import { EP_STATUS, DEMO_FALLBACK, skillOf, provName, pubCount, type Nav, type Series } from "@/lib/data";
import { DEMO_SERIES } from "@/lib/demo-fixtures";
import { listSeries } from "@/lib/api";

function FeatureChip({ icon, children }: { icon: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 9,
        padding: "9px 13px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: 999,
        boxShadow: "var(--shadow-sm)",
        fontSize: 13.5,
        fontWeight: 600,
      }}
    >
      <span style={{ display: "inline-flex", color: "var(--brand)" }}>
        <Icon name={icon} size={16} />
      </span>
      {children}
    </div>
  );
}

function Hero({ nav, demo }: { nav: Nav; demo?: Series }) {
  return (
    <section
      className="card fade-up"
      style={{
        padding: 0,
        overflow: "hidden",
        marginBottom: 30,
        border: "1px solid var(--border)",
        position: "relative",
        background: "linear-gradient(135deg, var(--surface) 0%, color-mix(in oklab, var(--brand) 5%, var(--surface)) 100%)",
      }}
    >
      <div style={{ display: "grid", gridTemplateColumns: "1.1fr .9fr", alignItems: "center", gap: 20 }}>
        <div style={{ padding: "44px 0 44px 46px" }}>
          <Badge tone="brand" icon="sparkles" style={{ marginBottom: 16, fontSize: 12.5 }}>
            Sản xuất video bằng AI · từ A đến Z
          </Badge>
          <h1 style={{ fontSize: 40, lineHeight: 1.08, marginBottom: 14, fontWeight: 800 }}>
            Một ý tưởng.
            <br />
            Cả một <span style={{ color: "var(--brand)" }}>series</span> YouTube.
          </h1>
          <p className="muted" style={{ fontSize: 16, lineHeight: 1.6, maxWidth: 440, marginBottom: 24 }}>
            Đưa chủ đề, để Reelo lo phần còn lại — lên ý tưởng, viết kịch bản, lồng tiếng, tạo hình ảnh, dựng video và
            xuất bản chỉ với một cú nhấp.
          </p>
          <div style={{ display: "flex", gap: 12, marginBottom: 26 }}>
            <Button variant="primary" size="lg" icon="sparkles" onClick={() => nav({ name: "wizard" })}>
              Tạo series mới
            </Button>
            {demo && (
              <Button variant="secondary" size="lg" icon="play" onClick={() => nav({ name: "workspace", series: demo })}>
                Xem demo
              </Button>
            )}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            <FeatureChip icon="message-square">Tạo series bằng chat</FeatureChip>
            <FeatureChip icon="save">Lưu &amp; tiếp tục mọi lúc</FeatureChip>
            <FeatureChip icon="sliders-horizontal">Tự chọn AI provider</FeatureChip>
            <FeatureChip icon="upload-cloud">1-click xuất bản</FeatureChip>
          </div>
        </div>
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: 360 }}>
          <Logo3D size={232} />
        </div>
      </div>
    </section>
  );
}

function ProviderDots({ s }: { s: Series }) {
  const items = [
    { g: "script", id: s.providers.script, icon: "pen-line" },
    { g: "image", id: s.providers.image, icon: "image" },
    { g: "voice", id: s.providers.voice, icon: "mic" },
  ];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      {items.map((it, i) => (
        <div
          key={i}
          title={provName(it.g, it.id)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 5,
            padding: "4px 9px",
            background: "var(--surface-2)",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 600,
            color: "var(--text-2)",
          }}
        >
          <Icon name={it.icon} size={13} /> {provName(it.g, it.id)}
        </div>
      ))}
    </div>
  );
}

function SeriesCard({ s, nav, delay }: { s: Series; nav: Nav; delay: string }) {
  const sk = skillOf(s.skill);
  const done = pubCount(s);
  const total = s.episodes.length;
  const next = s.episodes.find((e) => e.status !== "published");
  return (
    <Card hover className="fade-up" style={{ padding: 0, overflow: "hidden", animationDelay: delay }}>
      <div style={{ display: "flex", gap: 16, padding: 18 }}>
        <Placeholder label={s.cover} style={{ width: 120, height: 90, flex: "none" }} rounded="rounded-xl" />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
            <Badge tone="neutral" icon="folder">
              {s.topic}
            </Badge>
          </div>
          <h3 style={{ fontSize: 18, marginBottom: 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {s.name}
          </h3>
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, fontWeight: 600, color: sk.accent }}>
              <Icon name={sk.icon} size={15} /> {sk.name}
            </span>
          </div>
        </div>
      </div>

      <div style={{ padding: "0 18px 14px" }}>
        <ProviderDots s={s} />
      </div>

      <div style={{ padding: "0 18px 16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 7 }}>
          <span style={{ fontSize: 13, fontWeight: 700 }}>
            {done}/{total} tập đã xuất bản
          </span>
          <span className="subtle" style={{ fontSize: 12.5 }}>
            {total - done} tập đang chờ
          </span>
        </div>
        <Progress value={(done / total) * 100} height={7} />
        <div style={{ display: "flex", gap: 4, marginTop: 12 }}>
          {s.episodes.map((e) => (
            <div
              key={e.id}
              title={`${e.title} · ${EP_STATUS[e.status].label}`}
              style={{
                flex: 1,
                height: 5,
                borderRadius: 999,
                background: e.status === "draft" ? "var(--surface-3)" : EP_STATUS[e.status].color,
                opacity: e.status === "draft" ? 1 : 0.9,
              }}
            />
          ))}
        </div>
      </div>

      <div className="divider" />
      <div style={{ display: "flex", gap: 10, padding: 14 }}>
        <Button
          variant="soft"
          size="sm"
          icon="plus"
          style={{ flex: 1 }}
          onClick={() => nav({ name: "workspace", series: s, episode: next })}
        >
          Tạo tập tiếp theo
        </Button>
        <Button variant="ghost" size="sm" icon="arrow-right" onClick={() => nav({ name: "project", series: s })}>
          Mở series
        </Button>
      </div>
    </Card>
  );
}

export function Dashboard({ nav }: { nav: Nav }) {
  // Real series from GET /series. Only in the offline demo
  // (DEMO_FALLBACK === true) do we seed with sample fixtures so the UI is usable
  // without a backend; prod starts empty and shows whatever the API returns.
  const [series, setSeries] = React.useState<Series[]>(DEMO_FALLBACK ? DEMO_SERIES : []);
  const [loading, setLoading] = React.useState(!DEMO_FALLBACK);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    listSeries()
      .then((list) => {
        if (!alive) return;
        setSeries(list);
      })
      .catch((e) => {
        if (!alive) return;
        // Offline demo: keep the sample fixtures; prod: surface the error.
        if (DEMO_FALLBACK) {
          setSeries(DEMO_SERIES);
        } else {
          setError(e instanceof Error ? e.message : "Không tải được danh sách series");
          setSeries([]);
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const totalEpisodes = series.reduce((a, s) => a + s.episodes.length, 0);

  return (
    <div className="page">
      <Hero nav={nav} demo={series[0]} />

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
        <div>
          <h2 style={{ fontSize: 22 }}>Series của bạn</h2>
          <p className="muted" style={{ fontSize: 14, marginTop: 4 }}>
            {loading
              ? "Đang tải danh sách series…"
              : `${series.length} series · ${totalEpisodes} tập tổng cộng`}
          </p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <Segmented
            options={[
              { value: "grid", label: "Lưới", icon: "layout-grid" },
              { value: "list", label: "Danh sách", icon: "list" },
            ]}
            value="grid"
            onChange={() => {}}
          />
        </div>
      </div>

      {error && (
        <Card style={{ padding: 16, marginBottom: 18, border: "1px solid color-mix(in oklab, var(--danger, #ef3e36) 40%, transparent)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, color: "var(--text-2)" }}>
            <Icon name="alert-triangle" size={18} style={{ color: "var(--danger, #ef3e36)" }} />
            {error}
          </div>
        </Card>
      )}

      {loading && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: 48, color: "var(--text-3)" }}>
          <Icon name="loader" size={26} style={{ color: "var(--brand)" }} />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))", gap: 18 }}>
        {!loading &&
          series.map((s, i) => (
            <SeriesCard key={s.id} s={s} nav={nav} delay={`${i * 60}ms`} />
          ))}

        <button
          className="card card-hover fade-up"
          onClick={() => nav({ name: "wizard" })}
          style={{
            animationDelay: `${series.length * 60}ms`,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            minHeight: 280,
            border: "2px dashed var(--border-strong)",
            background: "transparent",
            color: "var(--text-2)",
            cursor: "pointer",
          }}
        >
          <span
            style={{
              width: 56,
              height: 56,
              borderRadius: 16,
              background: "var(--brand-tint)",
              color: "var(--brand)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Icon name="plus" size={28} />
          </span>
          <span style={{ fontWeight: 700, fontSize: 16, color: "var(--text)" }}>Tạo series mới</span>
          <span style={{ fontSize: 13.5 }}>Bắt đầu từ một ý tưởng</span>
        </button>
      </div>
    </div>
  );
}
