"use client";

// ===== Shared UI kit (ported from the prototype's ui.jsx) =====

import React from "react";
import { icons as lucideIcons } from "lucide-react";
import { EP_STATUS, type EpisodeStatus } from "@/lib/data";
import { MiniMark } from "./logo";

// ---- Icon (lucide-react) ----
export function toPascal(name: string): string {
  return String(name)
    .split(/[-_ ]/)
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join("");
}

const iconMap = lucideIcons as Record<string, React.ComponentType<any>>;

export interface IconProps {
  name: string;
  size?: number;
  strokeWidth?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function Icon({ name, size = 20, strokeWidth = 2, className = "", style }: IconProps) {
  const Cmp = iconMap[toPascal(name)] || iconMap[name];
  if (!Cmp) {
    // Fallback: a rounded square, mirrors the prototype's missing-icon behavior.
    return (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        style={style}
        aria-hidden="true"
      >
        <rect x="3" y="3" width="18" height="18" rx="3" />
      </svg>
    );
  }
  return <Cmp size={size} strokeWidth={strokeWidth} className={className} style={style} aria-hidden="true" />;
}

// ---- Button ----
type ButtonVariant = "primary" | "secondary" | "ghost" | "outline" | "soft";
type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: string;
  iconRight?: string;
}

export function Button({
  variant = "primary",
  size = "md",
  icon,
  iconRight,
  children,
  className = "",
  ...rest
}: ButtonProps) {
  return (
    <button className={`btn btn-${variant} btn-${size} ${className}`} {...rest}>
      {icon && <Icon name={icon} size={size === "sm" ? 16 : 18} />}
      {children && <span className="btn-label">{children}</span>}
      {iconRight && <Icon name={iconRight} size={size === "sm" ? 16 : 18} />}
    </button>
  );
}

// ---- Badge ----
type BadgeTone = "neutral" | "green" | "amber" | "brand" | "blue" | "violet";

export interface BadgeProps {
  children: React.ReactNode;
  tone?: BadgeTone;
  icon?: string;
  className?: string;
  style?: React.CSSProperties;
}

export function Badge({ children, tone = "neutral", icon, className = "", style }: BadgeProps) {
  return (
    <span className={`badge badge-${tone} ${className}`} style={style}>
      {icon && <Icon name={icon} size={13} />}
      {children}
    </span>
  );
}

// Provider cost / key badges. `cost_tier` = does it cost money; `requires_key`
// = must the user supply a BYOK key (all providers except keyless Edge-TTS).
export function TierBadge({
  cost_tier,
  requires_key,
}: {
  cost_tier: "free" | "paid";
  requires_key: boolean;
}) {
  return (
    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
      {cost_tier === "free" ? (
        <Badge tone="green" icon="gift">
          Free tier
        </Badge>
      ) : (
        <Badge tone="amber" icon="credit-card">
          Trả phí
        </Badge>
      )}
      {requires_key ? (
        <Badge tone="neutral" icon="key-round">
          Cần API key
        </Badge>
      ) : (
        <Badge tone="green" icon="check">
          Không cần key
        </Badge>
      )}
    </span>
  );
}

// ---- Card ----
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  hover?: boolean;
}

export function Card({ children, className = "", hover = false, ...rest }: CardProps) {
  return (
    <div className={`card ${hover ? "card-hover" : ""} ${className}`} {...rest}>
      {children}
    </div>
  );
}

// ---- Progress bar ----
export function Progress({
  value = 0,
  tone,
  className = "",
  height = 8,
}: {
  value?: number;
  tone?: string;
  className?: string;
  height?: number;
}) {
  return (
    <div className={`progress ${className}`} style={{ height }}>
      <div
        className="progress-fill"
        style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: tone || "var(--brand)" }}
      />
    </div>
  );
}

// ---- Striped image placeholder ----
export function Placeholder({
  label,
  className = "",
  style,
  rounded = "rounded-xl",
}: {
  label: string;
  className?: string;
  style?: React.CSSProperties;
  rounded?: string;
}) {
  return (
    <div className={`ph ${rounded} ${className}`} style={style}>
      <div className="ph-stripes" />
      <span className="ph-label">{label}</span>
    </div>
  );
}

// ---- Avatar ----
export function Avatar({ name = "U", className = "", size = 36 }: { name?: string; className?: string; size?: number }) {
  const initials = name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <div className={`avatar ${className}`} style={{ width: size, height: size, fontSize: size * 0.4 }}>
      {initials}
    </div>
  );
}

// ---- Episode status pill ----
export function StatusPill({ status }: { status: EpisodeStatus }) {
  const s = EP_STATUS[status] || EP_STATUS.draft;
  return (
    <span className="status-pill" style={{ ["--s" as any]: s.color } as React.CSSProperties}>
      <span className="status-dot" />
      {s.label}
    </span>
  );
}

// ---- Segmented control ----
export interface SegmentedOption {
  value: string;
  label: string;
  icon?: string;
}

export function Segmented({
  options,
  value,
  onChange,
}: {
  options: SegmentedOption[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="segmented">
      {options.map((o) => (
        <button
          key={o.value}
          className={`seg-item ${value === o.value ? "is-active" : ""}`}
          onClick={() => onChange(o.value)}
        >
          {o.icon && <Icon name={o.icon} size={15} />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---- Chat bubble ----
export function ChatBubble({ role, children }: { role: "ai" | "user"; children: React.ReactNode }) {
  const isAI = role === "ai";
  return (
    <div className={`chat-row ${isAI ? "chat-ai" : "chat-user"}`}>
      {isAI && (
        <div className="chat-ava">
          <MiniMark size={18} />
        </div>
      )}
      <div className={`chat-bubble ${isAI ? "bubble-ai" : "bubble-user"}`}>{children}</div>
      {!isAI && <Avatar name="Bạn" size={30} />}
    </div>
  );
}

// ---- Copyable error box (script-gen + produce failures) ----
// A red-toned block with a title, an optional hint, the full error in a
// scrollable <pre> the user can read in full, and a "Sao chép" button so they
// can paste the message back to us. `actions` renders extra buttons (e.g. retry).
export function ErrorBox({
  title,
  detail,
  hint,
  actions,
  style,
}: {
  title: string;
  detail: string;
  hint?: React.ReactNode;
  actions?: React.ReactNode;
  style?: React.CSSProperties;
}) {
  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(detail);
      } else {
        // Fallback for non-secure contexts / older browsers.
        const ta = document.createElement("textarea");
        ta.value = detail;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked — the <pre> is still selectable manually */
    }
  };
  return (
    <div
      className="card"
      style={{
        padding: 16,
        boxShadow: "none",
        border: "1.5px solid color-mix(in oklab,#dc2626 35%,var(--border))",
        background: "color-mix(in oklab,#dc2626 5%,var(--surface))",
        display: "flex",
        flexDirection: "column",
        gap: 12,
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <Icon name="alert-triangle" size={18} style={{ color: "#dc2626", flex: "none" }} />
        <span style={{ fontWeight: 800, fontSize: 14.5, color: "#dc2626" }}>{title}</span>
        <button
          className="btn btn-soft btn-sm"
          style={{ marginLeft: "auto" }}
          onClick={copy}
          title="Sao chép nội dung lỗi"
        >
          <Icon name={copied ? "check" : "copy"} size={15} />
          <span className="btn-label">{copied ? "Đã chép" : "Sao chép lỗi"}</span>
        </button>
      </div>
      {hint && (
        <div className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
          {hint}
        </div>
      )}
      <pre
        className="mono"
        style={{
          margin: 0,
          padding: "10px 12px",
          background: "var(--surface-2)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          fontSize: 12.5,
          lineHeight: 1.5,
          color: "var(--text)",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          maxHeight: 220,
          overflow: "auto",
          userSelect: "text",
        }}
      >
        {detail}
      </pre>
      {actions && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{actions}</div>}
    </div>
  );
}

// ---- Centered empty state (e.g. a screen reached with no active series) ----
export function EmptyState({
  icon = "folder-open",
  title,
  desc,
  actionLabel,
  onAction,
}: {
  icon?: string;
  title: string;
  desc?: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="page" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: 360 }}>
      <Card style={{ padding: 32, maxWidth: 420, textAlign: "center" }}>
        <span
          style={{
            width: 56,
            height: 56,
            borderRadius: 16,
            background: "var(--brand-tint)",
            color: "var(--brand)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            marginBottom: 14,
          }}
        >
          <Icon name={icon} size={26} />
        </span>
        <h2 style={{ fontSize: 19, marginBottom: 6 }}>{title}</h2>
        {desc && (
          <p className="muted" style={{ fontSize: 14, lineHeight: 1.5, marginBottom: actionLabel ? 18 : 0 }}>
            {desc}
          </p>
        )}
        {actionLabel && onAction && (
          <Button variant="primary" size="md" icon="layout-dashboard" onClick={onAction}>
            {actionLabel}
          </Button>
        )}
      </Card>
    </div>
  );
}
