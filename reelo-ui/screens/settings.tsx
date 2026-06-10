"use client";

// ===== Settings: per-user API key management ("Cấu hình AI") =====
//
// Provider SELECTION is per-series now (picked in the create/Setup flow). This
// page only manages the user's BYOK API keys, which stay PER-USER: entered once
// per provider, encrypted in the DB, and reused across every series.
//
// For each provider that needs a key it shows a key input + a "đã lưu / đã xác
// thực" badge + a link to where the user gets the key. Keyless providers (Edge,
// web-commons, OmniVoice, the web aggregate) need nothing here. Voice-clone
// samples are uploaded per-series (project / Setup), not here.

import React from "react";
import { Icon, Badge, Button, Card, TierBadge } from "@/components/ui";
import { type Nav } from "@/lib/data";
import {
  getProviderKeys,
  saveApiKey,
  ApiError,
  type ProviderKeys,
  type ProviderKeyItem,
} from "@/lib/api";

type TaskKey = "script" | "image" | "voice";

const GROUPS: { key: TaskKey; label: string; icon: string; hint: string }[] = [
  {
    key: "script",
    label: "Viết kịch bản",
    icon: "pen-line",
    hint: "AI nghiên cứu và viết kịch bản cho từng tập.",
  },
  {
    key: "image",
    label: "Dựng ảnh",
    icon: "image",
    hint: "Nguồn ảnh / video minh hoạ cho mỗi đoạn.",
  },
  {
    key: "voice",
    label: "Giọng đọc",
    icon: "mic",
    hint: "Giọng đọc tổng hợp (TTS) cho lời thoại.",
  },
];

// Provider id → whether its key field is an OAuth token (claude-cli) vs API key.
function isOauthToken(id: string): boolean {
  return id === "claude-cli";
}

function KeyBadge({ item }: { item: ProviderKeyItem }) {
  if (!item.requires_key) {
    return (
      <Badge tone="neutral" icon="unlock">
        Không cần key
      </Badge>
    );
  }
  if (item.has_key) {
    return item.valid === false ? (
      <Badge tone="amber" icon="alert-triangle">
        Key không hợp lệ
      </Badge>
    ) : (
      <Badge tone="green" icon="check-circle-2">
        {item.valid === true ? "Đã xác thực" : "Đã lưu"}
      </Badge>
    );
  }
  return (
    <Badge tone="amber" icon="key-round">
      Chưa có key
    </Badge>
  );
}

/** One provider row: shows status, and (when a key is needed) a key input + Save. */
function ProviderKeyRow({
  item,
  onSaved,
}: {
  item: ProviderKeyItem;
  onSaved: () => void;
}) {
  const [keyValue, setKeyValue] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const oauth = isOauthToken(item.id);

  const onSave = async () => {
    if (!keyValue.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await saveApiKey(item.id, keyValue.trim());
      setKeyValue("");
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Lưu key thất bại.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        display: "grid",
        gap: 10,
        padding: 14,
        borderRadius: 12,
        background: "var(--surface-2)",
        border: "1px solid var(--border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 650, fontSize: 14 }}>{item.name}</span>
        <TierBadge cost_tier={item.cost_tier} requires_key={item.requires_key} />
        <span style={{ marginLeft: "auto" }}>
          <KeyBadge item={item} />
        </span>
      </div>

      {item.note && (
        <div className="subtle" style={{ fontSize: 12.5, lineHeight: 1.5 }}>
          {item.note}
        </div>
      )}

      {item.requires_key && (
        <>
          {item.key_help_url && (
            <a
              href={item.key_help_url}
              target="_blank"
              rel="noreferrer"
              className="subtle"
              style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4 }}
            >
              <Icon name="external-link" size={12} />{" "}
              {oauth ? "Hướng dẫn lấy token" : "Lấy key"}
            </a>
          )}
          {oauth && (
            <div className="subtle" style={{ fontSize: 12, lineHeight: 1.5 }}>
              Đăng nhập tài khoản Claude của bạn rồi chạy{" "}
              <code style={{ fontSize: 12 }}>claude setup-token</code> để tạo OAuth token và dán
              vào đây.
            </div>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              className="field mono"
              type="password"
              placeholder={
                item.has_key
                  ? "Dán key mới để thay thế…"
                  : oauth
                    ? "sk-ant-oat01-•••••••••••"
                    : "sk-•••••••••••••••••••"
              }
              value={keyValue}
              onChange={(e) => {
                setKeyValue(e.target.value);
                setError(null);
              }}
              style={{ fontSize: 13, flex: 1 }}
            />
            <Button
              variant="primary"
              size="md"
              icon={saving ? "loader" : "save"}
              disabled={saving || !keyValue.trim()}
              onClick={onSave}
            >
              {saving ? "Đang lưu…" : item.has_key ? "Cập nhật" : "Lưu"}
            </Button>
          </div>
          {item.has_key && (
            <div className="subtle" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
              <Icon name="lock" size={13} /> Key đã lưu (mã hoá). Dán key mới để thay thế.
            </div>
          )}
        </>
      )}

      {error && (
        <div
          className="subtle"
          style={{ fontSize: 12.5, color: "var(--danger, #ef3e36)", display: "flex", alignItems: "center", gap: 6 }}
        >
          <Icon name="alert-triangle" size={13} /> {error}
        </div>
      )}
    </div>
  );
}

function ProviderGroupCard({
  group,
  items,
  onSaved,
}: {
  group: { key: TaskKey; label: string; icon: string; hint: string };
  items: ProviderKeyItem[];
  onSaved: () => void;
}) {
  return (
    <Card style={{ padding: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <span
          style={{
            width: 40,
            height: 40,
            borderRadius: 11,
            background: "var(--brand-tint)",
            color: "var(--brand)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flex: "none",
          }}
        >
          <Icon name={group.icon} size={19} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 15.5 }}>{group.label}</div>
          <div className="subtle" style={{ fontSize: 12.5 }}>
            {group.hint}
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        {items.map((item) => (
          <ProviderKeyRow key={item.id} item={item} onSaved={onSaved} />
        ))}
      </div>
    </Card>
  );
}

export function SettingsScreen({ nav }: { nav: Nav }) {
  const [data, setData] = React.useState<ProviderKeys | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(() => {
    setLoading(true);
    setError(null);
    getProviderKeys()
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Không tải được cấu hình."))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="page" style={{ paddingBottom: 60 }}>
      <div style={{ marginBottom: 6 }}>
        <Badge tone="brand" icon="key-round">
          Khóa API
        </Badge>
      </div>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 22 }}>Cấu hình AI</h2>
        <p className="muted" style={{ fontSize: 14, marginTop: 4, maxWidth: 660 }}>
          Nhập key API cho từng nhà cung cấp một lần — dùng chung cho mọi series. Việc chọn nhà
          cung cấp cho từng series (kịch bản / ảnh / giọng) được thực hiện khi tạo series.
        </p>
      </div>

      <Card
        style={{
          padding: 14,
          marginBottom: 20,
          background: "var(--brand-tint)",
          border: "1px solid color-mix(in oklab, var(--brand) 22%, transparent)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14 }}>
          <Icon name="info" size={18} style={{ color: "var(--brand)" }} />
          <span style={{ flex: 1 }}>
            Bạn chọn bộ công cụ AI riêng cho từng series khi tạo. Ở đây chỉ cần lưu key cho các
            provider cần key.
          </span>
          <Button variant="primary" size="sm" icon="sparkles" onClick={() => nav({ name: "wizard" })}>
            Tạo series mới
          </Button>
        </div>
      </Card>

      {error && (
        <Card style={{ padding: 16, marginBottom: 18, border: "1px solid color-mix(in oklab, var(--danger, #ef3e36) 40%, transparent)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, color: "var(--text-2)" }}>
            <Icon name="alert-triangle" size={18} style={{ color: "var(--danger, #ef3e36)" }} />
            {error}
            <Button variant="ghost" size="sm" icon="refresh-cw" onClick={load} style={{ marginLeft: "auto" }}>
              Thử lại
            </Button>
          </div>
        </Card>
      )}

      {loading && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: 48, color: "var(--text-3)" }}>
          <Icon name="loader" size={26} style={{ color: "var(--brand)" }} />
        </div>
      )}

      {!loading && data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {GROUPS.map((g) => (
            <ProviderGroupCard key={g.key} group={g} items={data[g.key]} onSaved={load} />
          ))}
          <div className="subtle" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 7 }}>
            <Icon name="lock" size={13} /> Mọi key được mã hoá (AES-256-GCM) và lưu theo tài khoản của bạn.
          </div>
        </div>
      )}
    </div>
  );
}
