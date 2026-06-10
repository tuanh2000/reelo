"use client";

// ===== Screen 3: Skill & Provider Setup (ported from screen-setup.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card, TierBadge } from "@/components/ui";
import { SKILLS, PROVIDERS, SERIES, type Nav, type Route, type Skill, type SkillTemplate, type ProviderOptionData } from "@/lib/data";
import { getProviders, saveApiKey, uploadMusic, uploadVoiceSample, type ProvidersResponse } from "@/lib/api";

const DENSITY_OPTIONS: { id: "light" | "standard" | "dense"; label: string }[] = [
  { id: "light", label: "Thưa (ít ảnh)" },
  { id: "standard", label: "Tiêu chuẩn" },
  { id: "dense", label: "Dày (nhiều ảnh)" },
];
const ASPECT_OPTIONS: { id: "16:9" | "9:16"; label: string }[] = [
  { id: "16:9", label: "16:9 (ngang)" },
  { id: "9:16", label: "9:16 (dọc)" },
];
const LANGUAGE_OPTIONS = [
  { id: "vi", label: "Tiếng Việt" },
  { id: "en", label: "English" },
];

function SkillCard({ sk, active, onClick }: { sk: Skill; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="card card-hover"
      style={{
        padding: 16,
        textAlign: "left",
        cursor: "pointer",
        border: `2px solid ${active ? "var(--brand)" : "var(--border)"}`,
        background: active ? "var(--brand-tint)" : "var(--surface)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <span
          style={{
            width: 42,
            height: 42,
            borderRadius: 12,
            background: active ? "var(--brand)" : "var(--surface-2)",
            color: active ? "#fff" : sk.accent,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Icon name={sk.icon} size={21} />
        </span>
        <span
          style={{
            width: 22,
            height: 22,
            borderRadius: 999,
            border: `2px solid ${active ? "var(--brand)" : "var(--border-strong)"}`,
            background: active ? "var(--brand)" : "transparent",
            color: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {active && <Icon name="check" size={13} strokeWidth={3} />}
        </span>
      </div>
      <div style={{ fontWeight: 700, fontSize: 15.5, marginBottom: 5 }}>{sk.name}</div>
      <div className="muted" style={{ fontSize: 13, lineHeight: 1.5, marginBottom: 8 }}>
        {sk.desc}
      </div>
      <div className="subtle" style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 5 }}>
        <Icon name="layers" size={13} /> {sk.templates.length} skill
      </div>
    </button>
  );
}

function TemplateRow({ tpl, active, onClick }: { tpl: SkillTemplate; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="card"
      style={{
        padding: 13,
        textAlign: "left",
        cursor: "pointer",
        boxShadow: "none",
        display: "flex",
        alignItems: "center",
        gap: 12,
        border: `2px solid ${active ? "var(--brand)" : "var(--border)"}`,
        background: active ? "var(--brand-tint)" : "var(--surface)",
      }}
    >
      <span
        style={{
          width: 20,
          height: 20,
          borderRadius: 999,
          border: `2px solid ${active ? "var(--brand)" : "var(--border-strong)"}`,
          background: active ? "var(--brand)" : "transparent",
          flex: "none",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#fff",
        }}
      >
        {active && <Icon name="check" size={12} strokeWidth={3} />}
      </span>
      <span
        style={{
          width: 38,
          height: 38,
          borderRadius: 10,
          background: "var(--surface-2)",
          color: "var(--text-2)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flex: "none",
        }}
      >
        <Icon name="file-text" size={17} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 700, fontSize: 14.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {tpl.name}
        </div>
        <div className="subtle" style={{ fontSize: 12.5, display: "inline-flex", alignItems: "center", gap: 5, marginTop: 3 }}>
          <Icon name="user-round" size={12} /> bởi {tpl.author}
        </div>
      </div>
      {tpl.official && (
        <Badge tone="brand" icon="badge-check">
          Chính thức
        </Badge>
      )}
    </button>
  );
}

function ProviderOption({ opt, active, onClick }: { opt: ProviderOptionData; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="card"
      style={{
        padding: 13,
        textAlign: "left",
        cursor: "pointer",
        boxShadow: "none",
        border: `2px solid ${active ? "var(--brand)" : "var(--border)"}`,
        background: active ? "var(--brand-tint)" : "var(--surface)",
        display: "flex",
        flexDirection: "column",
        gap: 9,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            width: 18,
            height: 18,
            borderRadius: 999,
            border: `2px solid ${active ? "var(--brand)" : "var(--border-strong)"}`,
            background: active ? "var(--brand)" : "transparent",
            flex: "none",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fff",
          }}
        >
          {active && <Icon name="check" size={11} strokeWidth={3} />}
        </span>
        <span style={{ fontWeight: 700, fontSize: 14 }}>{opt.name}</span>
        <span className="subtle" style={{ fontSize: 12, marginLeft: "auto" }}>
          {opt.note}
        </span>
      </div>
      <TierBadge cost_tier={opt.cost_tier} requires_key={opt.requires_key} />
    </button>
  );
}

export function SetupScreen({ nav, route }: { nav: Nav; route: Route }) {
  const series = route.series || SERIES[0];
  const [skill, setSkill] = React.useState(series.skill);
  const [tmpl, setTmpl] = React.useState(SKILLS.find((s) => s.id === series.skill)!.templates[0].id);
  const [prov, setProv] = React.useState<{ [k: string]: string }>({ ...series.providers });
  const [keys, setKeys] = React.useState<{ [k: string]: string }>({});
  const [savedKeys, setSavedKeys] = React.useState<{ [k: string]: boolean }>({});

  // New series config fields (Setup screen — integration §6 / risks #9).
  const [language, setLanguage] = React.useState("vi");
  const [targetMinutes, setTargetMinutes] = React.useState(10);
  const [density, setDensity] = React.useState<"light" | "standard" | "dense">("standard");
  const [aspect, setAspect] = React.useState<"16:9" | "9:16">("16:9");
  const [musicName, setMusicName] = React.useState<string>("");

  // Voice-clone (OmniVoice) sample upload state — shown when voice = omnivoice.
  const [sampleFile, setSampleFile] = React.useState<File | null>(null);
  const [sampleTranscript, setSampleTranscript] = React.useState("");
  const [sampleLang, setSampleLang] = React.useState("vi");
  const [sampleStatus, setSampleStatus] = React.useState<"idle" | "saving" | "saved" | "error">("idle");
  const [sampleError, setSampleError] = React.useState<string>("");

  // Live provider catalog from GET /providers (falls back to the static copy).
  const [catalog, setCatalog] = React.useState<ProvidersResponse | null>(null);
  React.useEffect(() => {
    getProviders()
      .then(setCatalog)
      .catch(() => setCatalog(null)); // offline: keep static PROVIDERS
  }, []);

  const groupOptions = (g: string): ProviderOptionData[] => {
    const live = catalog?.[g as keyof ProvidersResponse];
    if (live && live.length) {
      return live.map((o) => ({
        id: o.id,
        name: o.name,
        cost_tier: o.cost_tier,
        requires_key: o.requires_key,
        key_help_url: o.key_help_url || undefined,
        note: o.note || "",
      }));
    }
    return PROVIDERS[g].options;
  };

  const pickCategory = (id: string) => {
    setSkill(id);
    setTmpl(SKILLS.find((s) => s.id === id)!.templates[0].id);
  };
  const cat = SKILLS.find((s) => s.id === skill)!;
  const tplObj = cat.templates.find((t) => t.id === tmpl) || cat.templates[0];

  // Providers needing a key among the current selection. requires_key is honoured
  // for FREE providers too (e.g. Gemini free tier still needs a key); only the
  // keyless Edge-TTS is exempt (integration risk #1).
  const needKey = Object.keys(PROVIDERS)
    .map((g) => {
      const opt = groupOptions(g).find((o) => o.id === prov[g]);
      return opt && opt.requires_key ? { g, opt } : null;
    })
    .filter(Boolean) as { g: string; opt: ProviderOptionData }[];

  const onSaveKey = async (opt: ProviderOptionData) => {
    const value = keys[opt.id];
    if (!value) return;
    try {
      await saveApiKey(opt.id, value);
      setSavedKeys((s) => ({ ...s, [opt.id]: true }));
    } catch {
      setSavedKeys((s) => ({ ...s, [opt.id]: false }));
    }
  };

  const onMusic = async (file: File | undefined) => {
    if (!file) return;
    setMusicName(file.name);
    try {
      await uploadMusic(series.id, file);
    } catch {
      /* upload best-effort; surfaced elsewhere */
    }
  };

  const onSaveVoiceSample = async () => {
    if (!sampleFile || !sampleTranscript.trim()) return;
    setSampleStatus("saving");
    setSampleError("");
    try {
      await uploadVoiceSample(series.id, sampleFile, sampleTranscript.trim(), sampleLang);
      setSampleStatus("saved");
    } catch (e) {
      setSampleStatus("error");
      setSampleError(e instanceof Error ? e.message : "Tải mẫu thất bại");
    }
  };

  return (
    <div className="page" style={{ paddingBottom: 96 }}>
      <div style={{ marginBottom: 6 }}>
        <Badge tone="neutral" icon="folder">
          {series.name}
        </Badge>
      </div>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 22 }}>Skill &amp; Provider</h2>
        <p className="muted" style={{ fontSize: 14, marginTop: 4 }}>
          Chọn template chủ đề và nhà cung cấp AI cho từng khâu. Bạn có thể dùng các dịch vụ miễn phí hoặc gắn API key
          riêng (BYOK).
        </p>
      </div>

      {/* Skill */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 14 }}>
        <Icon name="shapes" size={18} style={{ color: "var(--brand)" }} />
        <h3 style={{ fontSize: 16 }}>1. Chọn thể loại</h3>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 14, marginBottom: 18 }}>
        {SKILLS.map((sk) => (
          <SkillCard key={sk.id} sk={sk} active={skill === sk.id} onClick={() => pickCategory(sk.id)} />
        ))}
      </div>

      {/* Skill templates within the chosen category */}
      <Card style={{ padding: 16, marginBottom: 34 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
          <span
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              background: "var(--brand-tint)",
              color: "var(--brand)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Icon name={cat.icon} size={18} />
          </span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Skill trong “{cat.name}”</div>
            <div className="subtle" style={{ fontSize: 12.5 }}>
              Mỗi thể loại có nhiều skill do các tác giả xây dựng — chọn 1
            </div>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
          {cat.templates.map((tpl) => (
            <TemplateRow key={tpl.id} tpl={tpl} active={tmpl === tpl.id} onClick={() => setTmpl(tpl.id)} />
          ))}
        </div>
      </Card>

      {/* Series config: language / length / density / aspect / music */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 14 }}>
        <Icon name="settings-2" size={18} style={{ color: "var(--brand)" }} />
        <h3 style={{ fontSize: 16 }}>2. Cấu hình series</h3>
      </div>
      <Card style={{ padding: 16, marginBottom: 34 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
          <div>
            <span className="label">Ngôn ngữ giọng đọc</span>
            <select
              className="field"
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            >
              {LANGUAGE_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <span className="label">Thời lượng mục tiêu (phút)</span>
            <input
              className="field"
              type="number"
              min={1}
              max={60}
              value={targetMinutes}
              onChange={(e) => setTargetMinutes(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
          <div>
            <span className="label">Mật độ ảnh</span>
            <select
              className="field"
              value={density}
              onChange={(e) => setDensity(e.target.value as "light" | "standard" | "dense")}
            >
              {DENSITY_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <span className="label">Tỉ lệ khung hình</span>
            <select
              className="field"
              value={aspect}
              onChange={(e) => setAspect(e.target.value as "16:9" | "9:16")}
            >
              {ASPECT_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <span className="label">Nhạc nền (tuỳ chọn)</span>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <label className="btn btn-secondary btn-md" style={{ cursor: "pointer" }}>
              <Icon name="music" size={15} /> Tải lên
              <input
                type="file"
                accept="audio/*"
                style={{ display: "none" }}
                onChange={(e) => onMusic(e.target.files?.[0])}
              />
            </label>
            {musicName && (
              <span className="subtle" style={{ fontSize: 13 }}>
                {musicName}
              </span>
            )}
          </div>
        </div>
      </Card>

      {/* Providers */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 14 }}>
        <Icon name="sliders-horizontal" size={18} style={{ color: "var(--brand)" }} />
        <h3 style={{ fontSize: 16 }}>3. Chọn Provider cho từng khâu</h3>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 16, marginBottom: 34 }}>
        {Object.keys(PROVIDERS).map((g) => {
          const group = PROVIDERS[g];
          return (
            <Card key={g} style={{ padding: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
                <span
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 10,
                    background: "var(--brand-tint)",
                    color: "var(--brand)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Icon name={group.icon} size={18} />
                </span>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 15 }}>{group.label}</div>
                  <div className="subtle" style={{ fontSize: 12.5 }}>
                    Chọn 1 nhà cung cấp
                  </div>
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(190px, 1fr))", gap: 10 }}>
                {groupOptions(g).map((opt) => (
                  <ProviderOption
                    key={opt.id}
                    opt={opt}
                    active={prov[g] === opt.id}
                    onClick={() => setProv((p) => ({ ...p, [g]: opt.id }))}
                  />
                ))}
              </div>
            </Card>
          );
        })}
      </div>

      {/* Voice clone (OmniVoice) — only when the chosen voice provider is omnivoice */}
      {prov.voice === "omnivoice" && (
        <Card style={{ padding: 16, marginBottom: 34, border: "2px solid var(--brand)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <span
              style={{
                width: 36,
                height: 36,
                borderRadius: 10,
                background: "var(--brand-tint)",
                color: "var(--brand)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Icon name="mic" size={18} />
            </span>
            <div>
              <div style={{ fontWeight: 700, fontSize: 15 }}>Clone giọng (OmniVoice)</div>
              <div className="subtle" style={{ fontSize: 12.5 }}>
                Tải lên một đoạn âm thanh mẫu (3–30 giây) + transcript của chính đoạn đó, rồi chọn ngôn ngữ.
              </div>
            </div>
          </div>
          <div style={{ display: "grid", gap: 14, marginTop: 8 }}>
            <div>
              <span className="label">Âm thanh mẫu</span>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <label className="btn btn-secondary btn-md" style={{ cursor: "pointer" }}>
                  <Icon name="upload" size={15} /> Chọn file
                  <input
                    type="file"
                    accept="audio/*"
                    style={{ display: "none" }}
                    onChange={(e) => {
                      setSampleFile(e.target.files?.[0] || null);
                      setSampleStatus("idle");
                    }}
                  />
                </label>
                {sampleFile && (
                  <span className="subtle" style={{ fontSize: 13 }}>
                    {sampleFile.name}
                  </span>
                )}
              </div>
            </div>
            <div>
              <span className="label">Transcript của đoạn mẫu</span>
              <textarea
                className="field"
                rows={3}
                placeholder="Nhập đúng lời thoại trong đoạn âm thanh mẫu…"
                value={sampleTranscript}
                onChange={(e) => {
                  setSampleTranscript(e.target.value);
                  setSampleStatus("idle");
                }}
                style={{ resize: "vertical", fontSize: 13.5 }}
              />
            </div>
            <div style={{ maxWidth: 240 }}>
              <span className="label">Ngôn ngữ giọng nói</span>
              <select
                className="field"
                value={sampleLang}
                onChange={(e) => setSampleLang(e.target.value)}
              >
                {LANGUAGE_OPTIONS.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <Button
                variant="primary"
                size="md"
                icon="save"
                onClick={onSaveVoiceSample}
                disabled={!sampleFile || !sampleTranscript.trim() || sampleStatus === "saving"}
              >
                {sampleStatus === "saving" ? "Đang lưu…" : "Lưu giọng mẫu"}
              </Button>
              {sampleStatus === "saved" && (
                <Badge tone="green" icon="check">
                  Đã lưu giọng mẫu
                </Badge>
              )}
              {sampleStatus === "error" && (
                <span className="subtle" style={{ fontSize: 12.5, color: "var(--danger, #ef3e36)" }}>
                  {sampleError}
                </span>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* BYOK */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 14 }}>
        <Icon name="key-round" size={18} style={{ color: "var(--brand)" }} />
        <h3 style={{ fontSize: 16 }}>4. API Keys (BYOK)</h3>
      </div>
      <Card style={{ padding: 16 }}>
        {needKey.length === 0 ? (
          <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text-2)", fontSize: 14 }}>
            <Icon name="party-popper" size={18} style={{ color: "#16a34a" }} /> Lựa chọn hiện tại không cần API key
            (ví dụ Edge-TTS) — sẵn sàng tạo!
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {needKey.map(({ g, opt }) => (
              <div key={`${g}-${opt.id}`}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 7, flexWrap: "wrap" }}>
                  <span className="label" style={{ margin: 0 }}>
                    {opt.name}
                  </span>
                  <Badge tone="neutral">{PROVIDERS[g].label}</Badge>
                  {opt.cost_tier === "free" && (
                    <Badge tone="green" icon="gift">
                      Free tier (vẫn cần key)
                    </Badge>
                  )}
                  {savedKeys[opt.id] && (
                    <Badge tone="green" icon="check">
                      Đã lưu
                    </Badge>
                  )}
                  {opt.key_help_url && (
                    <a
                      href={opt.key_help_url}
                      target="_blank"
                      rel="noreferrer"
                      className="subtle"
                      style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 4, marginLeft: "auto" }}
                    >
                      <Icon name="external-link" size={12} /> Lấy key
                    </a>
                  )}
                </div>
                <div style={{ display: "flex", gap: 9 }}>
                  <input
                    className="field mono"
                    type="password"
                    placeholder="sk-•••••••••••••••••••••"
                    value={keys[opt.id] || ""}
                    onChange={(e) => setKeys((k) => ({ ...k, [opt.id]: e.target.value }))}
                    style={{ fontSize: 13 }}
                  />
                  <Button variant="secondary" size="md" icon="save" onClick={() => onSaveKey(opt)}>
                    Lưu
                  </Button>
                </div>
              </div>
            ))}
            <div className="subtle" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 7 }}>
              <Icon name="lock" size={13} /> Key được mã hóa (AES-256-GCM) và lưu theo tài khoản của bạn.
            </div>
          </div>
        )}
      </Card>

      {/* sticky footer */}
      <div
        style={{
          position: "sticky",
          bottom: 0,
          marginTop: 28,
          padding: "16px 0 0",
          background: "linear-gradient(transparent, var(--bg) 40%)",
          display: "flex",
          gap: 12,
          alignItems: "center",
        }}
      >
        <div
          className="muted"
          style={{ fontSize: 13.5, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          <b style={{ color: "var(--text)" }}>{tplObj.name}</b> · {cat.name} ·{" "}
          {needKey.length === 0 ? "Miễn phí" : `${needKey.length} key cần thiết`}
        </div>
        <Button variant="secondary" size="md" icon="palette" onClick={() => nav({ name: "style", series })}>
          Chọn style hình ảnh
        </Button>
        <Button
          variant="primary"
          size="md"
          icon="check"
          onClick={() => nav({ name: "project", series, toast: "Đã lưu cấu hình Skill & Provider" })}
        >
          Lưu cấu hình
        </Button>
      </div>
    </div>
  );
}
