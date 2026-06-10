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
