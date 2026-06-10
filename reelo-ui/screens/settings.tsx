"use client";

// ===== Settings: account-level "Cấu hình AI" =====
//
// The user configures the AI providers for the three generation tasks ONCE,
// here, before creating any series. A single provider set is shared across every
// series (account-level decision). Series creation gates on script + image being
// ready (see dashboard / wizard gate), routing the user here when they are not.
//
// Each task group: choose a provider (GET /settings/providers options), enter a
// key/token when the provider requires one (saveApiKey), and persist the choice
// (saveProviderSettings). Readiness comes back from the server.

import React from "react";
import { Icon, Badge, Button, Card, TierBadge } from "@/components/ui";
import { type Nav } from "@/lib/data";
import {
  getProviderSettings,
  saveProviderSettings,
  saveApiKey,
  ApiError,
  type ProviderSettings,
  type ProviderSettingsItem,
  type ProviderOption,
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
function isOauthToken(id: string | null): boolean {
  return id === "claude-cli";
}

function ReadyBadge({ item }: { item: ProviderSettingsItem }) {
  if (!item.provider) {
    return (
      <Badge tone="amber" icon="alert-triangle">
        Chưa chọn
      </Badge>
    );
  }
  if (item.ready) {
    return (
      <Badge tone="green" icon="check-circle-2">
        {item.requires_key ? "Đã xác thực" : "Sẵn sàng"}
      </Badge>
    );
  }
  return (
    <Badge tone="amber" icon="key-round">
      Cần nhập key
    </Badge>
  );
}

function ProviderGroupCard({
  group,
  options,
  item,
  onSaved,
}: {
  group: { key: TaskKey; label: string; icon: string; hint: string };
  options: ProviderOption[];
  item: ProviderSettingsItem;
  onSaved: (next: ProviderSettings) => void;
}) {
  const [provider, setProvider] = React.useState<string>(item.provider || "");
  const [keyValue, setKeyValue] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    setProvider(item.provider || "");
  }, [item.provider]);

  const chosen = options.find((o) => o.id === provider);
  // The provider currently saved on the server still has its key recorded; a
  // freshly-picked (unsaved) provider needs a key if it requires one.
  const isCurrentSaved = provider === item.provider;
  const needsKey = !!chosen?.requires_key && !(isCurrentSaved && item.has_key);
  const oauth = isOauthToken(provider);

  const onSave = async () => {
    if (!provider) return;
    setSaving(true);
    setError(null);
    try {
      // Save the key first (if the user typed one) so readiness reflects it.
      if (chosen?.requires_key && keyValue.trim()) {
        await saveApiKey(provider, keyValue.trim());
      }
      const next = await saveProviderSettings({ [group.key]: provider });
      setKeyValue("");
      onSaved(next);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Lưu cấu hình thất bại.");
    } finally {
      setSaving(false);
    }
  };

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
        <ReadyBadge item={item} />
      </div>

      <div style={{ display: "grid", gap: 14 }}>
        <div>
          <span className="label">Nhà cung cấp</span>
          <select
            className="field"
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              setError(null);
            }}
          >
            <option value="">— Chọn nhà cung cấp —</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
                {o.cost_tier === "free" ? "  · Miễn phí" : "  · Trả phí"}
                {o.requires_key ? "  · cần key" : ""}
              </option>
            ))}
          </select>
        </div>

        {chosen && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <TierBadge cost_tier={chosen.cost_tier} requires_key={chosen.requires_key} />
            {chosen.note && (
              <span className="subtle" style={{ fontSize: 12.5 }}>
                {chosen.note}
              </span>
            )}
            {chosen.key_help_url && (
              <a
                href={chosen.key_help_url}
                target="_blank"
                rel="noreferrer"
                className="subtle"
                style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4 }}
              >
                <Icon name="external-link" size={12} />{" "}
                {oauth ? "Hướng dẫn lấy token" : "Lấy key"}
              </a>
            )}
          </div>
        )}

        {needsKey && (
          <div>
            <span className="label">{oauth ? "OAuth token" : "API key"}</span>
            {oauth && (
              <div className="subtle" style={{ fontSize: 12, marginBottom: 7, lineHeight: 1.5 }}>
                Đăng nhập tài khoản Claude của bạn rồi chạy{" "}
                <code style={{ fontSize: 12 }}>claude setup-token</code> để tạo OAuth token và dán
                vào đây. Reelo gọi <code style={{ fontSize: 12 }}>claude</code> CLI bằng subscription
                của bạn — không phải API key trả theo token.
              </div>
            )}
            <input
              className="field mono"
              type="password"
              placeholder={oauth ? "sk-ant-oat01-•••••••••••" : "sk-•••••••••••••••••••"}
              value={keyValue}
              onChange={(e) => setKeyValue(e.target.value)}
              style={{ fontSize: 13 }}
            />
          </div>
        )}

        {item.provider === provider && item.requires_key && item.has_key && (
          <div className="subtle" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
            <Icon name="lock" size={13} /> Key đã lưu (mã hoá). Dán key mới để thay thế.
          </div>
        )}

        {error && (
          <div
            className="subtle"
            style={{ fontSize: 12.5, color: "var(--danger, #ef3e36)", display: "flex", alignItems: "center", gap: 6 }}
          >
            <Icon name="alert-triangle" size={13} /> {error}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Button
            variant="primary"
            size="md"
            icon={saving ? "loader" : "save"}
            disabled={!provider || saving || (needsKey && !keyValue.trim())}
            onClick={onSave}
          >
            {saving ? "Đang lưu…" : "Lưu"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

export function SettingsScreen({ nav }: { nav: Nav }) {
  const [data, setData] = React.useState<ProviderSettings | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(() => {
    setLoading(true);
    setError(null);
    getProviderSettings()
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Không tải được cấu hình."))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const allReady = !!data && data.script_ready && data.image_ready;

  return (
    <div className="page" style={{ paddingBottom: 60 }}>
      <div style={{ marginBottom: 6 }}>
        <Badge tone="brand" icon="settings">
          Cấu hình tài khoản
        </Badge>
      </div>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 22 }}>Cấu hình AI</h2>
        <p className="muted" style={{ fontSize: 14, marginTop: 4, maxWidth: 640 }}>
          Chọn nhà cung cấp AI cho viết kịch bản, dựng ảnh và giọng đọc — cấu hình một lần và dùng
          chung cho mọi series. Cần cấu hình xong kịch bản và ảnh trước khi tạo series.
        </p>
      </div>

      {!loading && data && (
        <Card
          style={{
            padding: 14,
            marginBottom: 20,
            border: `1px solid ${allReady ? "color-mix(in oklab, #16a34a 35%, transparent)" : "color-mix(in oklab, var(--brand) 30%, transparent)"}`,
            background: allReady ? "color-mix(in oklab, #16a34a 6%, transparent)" : "var(--brand-tint)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14 }}>
            <Icon
              name={allReady ? "check-circle-2" : "info"}
              size={18}
              style={{ color: allReady ? "#16a34a" : "var(--brand)" }}
            />
            <span style={{ flex: 1 }}>
              {allReady
                ? "Đã đủ cấu hình — bạn có thể tạo series mới."
                : "Hãy chọn provider cho Viết kịch bản và Dựng ảnh để bắt đầu tạo series."}
            </span>
            {allReady && (
              <Button variant="primary" size="sm" icon="sparkles" onClick={() => nav({ name: "wizard" })}>
                Tạo series mới
              </Button>
            )}
          </div>
        </Card>
      )}

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
            <ProviderGroupCard
              key={g.key}
              group={g}
              options={data.options[g.key]}
              item={data[g.key]}
              onSaved={setData}
            />
          ))}
          <div className="subtle" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 7 }}>
            <Icon name="lock" size={13} /> Mọi key được mã hoá (AES-256-GCM) và lưu theo tài khoản của bạn.
          </div>
        </div>
      )}
    </div>
  );
}
