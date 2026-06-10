"use client";

// ===== App shell: Sidebar + Topbar (ported from shell.jsx) =====

import React from "react";
import { Icon, Badge, Button, Avatar, Progress } from "./ui";
import { Wordmark } from "./logo";
import { type Nav, type Route } from "@/lib/data";

function NavItem({
  icon,
  label,
  active,
  badge,
  disabled,
  title,
  onClick,
}: {
  icon: string;
  label: string;
  active?: boolean;
  badge?: string | null;
  disabled?: boolean;
  title?: string;
  onClick?: () => void;
}) {
  return (
    <button
      className={`nav-item ${active ? "is-active" : ""}`}
      disabled={disabled}
      title={disabled ? title : undefined}
      style={disabled ? { opacity: 0.5, cursor: "default" } : undefined}
      onClick={disabled ? undefined : onClick}
    >
      <Icon name={icon} size={19} className="nav-ico" />
      <span>{label}</span>
      {badge && (
        <Badge tone={active ? "brand" : "neutral"} className="nav-badge">
          {badge}
        </Badge>
      )}
    </button>
  );
}

export function Sidebar({ route, nav }: { route: Route; nav: Nav }) {
  const n = route.name;
  // Screens that operate on a specific series. Without an active series there is
  // nothing real to open, so these stay disabled (we never fall back to a mock
  // series). They light up once the user opens a series from the dashboard.
  const series = route.series;
  const noSeries = !series;
  const needSeriesTitle = "Hãy mở một series từ Bảng điều khiển trước";
  return (
    <aside className="sidebar">
      <div className="side-brand">
        <Wordmark size={30} />
      </div>

      <Button
        variant="primary"
        size="md"
        icon="sparkles"
        className="w-full"
        style={{ width: "100%", marginBottom: 4 }}
        onClick={() => nav({ name: "wizard" })}
      >
        Tạo series mới
      </Button>

      <div className="side-section">Không gian làm việc</div>
      <NavItem icon="layout-dashboard" label="Bảng điều khiển" active={n === "dashboard"} onClick={() => nav({ name: "dashboard" })} />
      <NavItem icon="message-square-plus" label="Trợ lý tạo series" active={n === "wizard"} onClick={() => nav({ name: "wizard" })} />
      <NavItem
        icon="folder-open"
        label="Chi tiết series"
        active={n === "project"}
        badge={series ? "đang mở" : null}
        disabled={noSeries}
        title={needSeriesTitle}
        onClick={() => series && nav({ name: "project", series })}
      />
      <NavItem icon="pen-line" label="Xưởng kịch bản" active={n === "workspace"} disabled={noSeries} title={needSeriesTitle} onClick={() => series && nav({ name: "workspace", series })} />
      <NavItem icon="youtube" label="Duyệt & Xuất bản" active={n === "review"} disabled={noSeries} title={needSeriesTitle} onClick={() => series && nav({ name: "review", series })} />

      <div className="side-section">Cấu hình</div>
      <NavItem icon="bot" label="Cấu hình AI" active={n === "settings"} onClick={() => nav({ name: "settings" })} />
      <NavItem icon="sliders-horizontal" label="Cấu hình series" active={n === "setup"} disabled={noSeries} title={needSeriesTitle} onClick={() => series && nav({ name: "setup", series })} />
      <NavItem icon="palette" label="Style Studio" active={n === "style"} disabled={noSeries} title={needSeriesTitle} onClick={() => series && nav({ name: "style", series })} />
      <NavItem icon="library" label="Video đã xuất bản" disabled badge="Sắp có" />

      <div style={{ flex: 1 }} />

      <div
        className="card"
        style={{
          padding: 13,
          background: "var(--brand-tint)",
          border: "1px solid color-mix(in oklab, var(--brand) 22%, transparent)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 700, fontSize: 13.5, color: "var(--brand-700)" }}>
          <Icon name="gift" size={16} /> Gói miễn phí
        </div>
        <div className="muted" style={{ fontSize: 12.5, margin: "6px 0 10px", lineHeight: 1.5 }}>
          Đã dùng 3/5 video tháng này. Tự host provider để không giới hạn.
        </div>
        <Progress value={60} height={6} />
      </div>

      <NavItem icon="settings" label="Cấu hình AI" active={n === "settings"} onClick={() => nav({ name: "settings" })} />
    </aside>
  );
}

const CRUMBS: Record<string, { t: string; b?: boolean }[]> = {
  dashboard: [{ t: "Bảng điều khiển", b: true }],
  wizard: [{ t: "Trợ lý tạo series", b: true }],
  setup: [{ t: "Cấu hình series", b: true }],
  style: [{ t: "Style Studio", b: true }],
  workspace: [{ t: "Xưởng kịch bản", b: true }],
  review: [{ t: "Duyệt & Xuất bản", b: true }],
  project: [{ t: "Chi tiết series", b: true }],
  settings: [{ t: "Cấu hình AI", b: true }],
};

export function Topbar({
  route,
  nav,
  theme,
  onToggleTheme,
}: {
  route: Route;
  nav: Nav;
  theme: "light" | "dark";
  onToggleTheme: () => void;
}) {
  const crumbs: { t: string; b?: boolean; onClick?: () => void }[] = [];
  if (route.name !== "dashboard") crumbs.push({ t: "Bảng điều khiển", onClick: () => nav({ name: "dashboard" }) });
  if (route.series && route.name !== "project")
    crumbs.push({ t: route.series.name, onClick: () => nav({ name: "project", series: route.series }) });
  crumbs.push({ ...(CRUMBS[route.name]?.[0] || { t: "" }), b: true });

  return (
    <header className="topbar">
      <nav className="crumb">
        {crumbs.map((c, i) => (
          <React.Fragment key={i}>
            {i > 0 && <Icon name="chevron-right" size={15} />}
            {c.onClick ? (
              <button
                className="btn-ghost"
                style={{ border: "none", background: "none", color: "inherit", font: "inherit", padding: 0 }}
                onClick={c.onClick}
              >
                {c.t}
              </button>
            ) : (
              <b
                style={{
                  color: c.b ? "var(--text)" : undefined,
                  maxWidth: 320,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {c.t}
              </b>
            )}
          </React.Fragment>
        ))}
      </nav>

      <div className="topbar-spacer" />

      {route.name === "dashboard" && (
        <div className="searchbar">
          <Icon name="search" size={17} />
          <input placeholder="Tìm series, chủ đề…" />
        </div>
      )}

      <button className="icon-btn" title="Đổi giao diện sáng/tối" onClick={onToggleTheme}>
        <Icon name={theme === "dark" ? "sun" : "moon"} size={19} />
      </button>
      <button className="icon-btn" title="Thông báo" style={{ position: "relative" }}>
        <Icon name="bell" size={19} />
        <span
          style={{
            position: "absolute",
            top: 9,
            right: 9,
            width: 7,
            height: 7,
            background: "var(--brand)",
            borderRadius: 999,
            boxShadow: "0 0 0 2px var(--surface)",
          }}
        />
      </button>
      <Avatar name="Minh Khôi" size={36} />
    </header>
  );
}
