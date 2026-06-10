"use client";

// ===== Screen 3: Skill & Provider Setup (ported from screen-setup.jsx) =====

import React from "react";
import { Icon, Badge, Button, Card } from "@/components/ui";
import { SKILLS, type Nav, type Route, type Skill, type SkillTemplate, type SeriesDraft } from "@/lib/data";
import {
  uploadMusic,
  uploadVoiceSample,
  getProviderKeys,
  ApiError,
  type ProviderKeys,
  type ProviderKeyItem,
} from "@/lib/api";

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

// One per-series provider dropdown + a key warning when the picked provider
// needs a per-user key the user has not saved (links to Cấu hình AI).
function ProviderPicker({
  label,
  options,
  value,
  onChange,
  nav,
}: {
  label: string;
  options: ProviderKeyItem[];
  value: string;
  onChange: (id: string) => void;
  nav: Nav;
}) {
  const chosen = options.find((o) => o.id === value);
  const needsKey = !!chosen?.requires_key && !chosen?.has_key;
  return (
    <div>
      <span className="label">{label}</span>
      <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.length === 0 && <option value="">—</option>}
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
            {o.cost_tier === "free" ? "  · Miễn phí" : "  · Trả phí"}
            {o.requires_key ? (o.has_key ? "  · có key" : "  · cần key") : ""}
          </option>
        ))}
      </select>
      {needsKey && (
        <div
          className="subtle"
          style={{ fontSize: 12, marginTop: 6, color: "var(--danger, #ef3e36)", display: "flex", alignItems: "center", gap: 6, lineHeight: 1.5 }}
        >
          <Icon name="alert-triangle" size={13} /> Chưa có key cho “{chosen?.name}”.
          <button
            className="btn btn-ghost btn-sm"
            style={{ padding: "2px 6px", fontSize: 12 }}
            onClick={() => nav({ name: "settings" })}
          >
            Thêm key ở Cấu hình AI
          </button>
        </div>
      )}
    </div>
  );
}

const VOICE_LANGUAGE_OPTIONS = LANGUAGE_OPTIONS;

// Per-series voice-clone reference upload (OmniVoice). When a series already
// exists it uploads immediately (POST /series/{id}/voice-sample); in the create
// flow (no series id yet) it stages the file + transcript and reports them up so
// the parent can upload right after approve creates the series.
function VoiceSampleBlock({
  seriesId,
  language,
  staged,
  onStage,
}: {
  seriesId?: string;
  language: string;
  staged: { file: File; transcript: string; language: string } | null;
  onStage: (s: { file: File; transcript: string; language: string } | null) => void;
}) {
  const [file, setFile] = React.useState<File | null>(staged?.file ?? null);
  const [transcript, setTranscript] = React.useState(staged?.transcript ?? "");
  const [lang, setLang] = React.useState(staged?.language || language);
  const [saving, setSaving] = React.useState(false);
  const [done, setDone] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const onApply = async () => {
    if (!file || !transcript.trim()) return;
    if (!seriesId) {
      // Create flow: stage for upload right after approve.
      onStage({ file, transcript: transcript.trim(), language: lang });
      setDone(true);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await uploadVoiceSample(seriesId, file, transcript.trim(), lang);
      setDone(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Tải giọng mẫu thất bại.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        display: "grid",
        gap: 12,
        marginTop: 14,
        padding: 14,
        borderRadius: 12,
        background: "var(--surface-2)",
        border: "1px solid var(--border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span style={{ fontWeight: 650, fontSize: 13.5 }}>Giọng mẫu để clone (OmniVoice)</span>
        {done ? (
          <Badge tone="green" icon="check-circle-2">
            {seriesId ? "Đã lưu giọng mẫu" : "Sẽ tải lên sau khi tạo series"}
          </Badge>
        ) : (
          <Badge tone="amber" icon="alert-triangle">
            Cần giọng mẫu
          </Badge>
        )}
      </div>
      <div>
        <span className="label">Âm thanh mẫu</span>
        <input
          className="field"
          type="file"
          accept="audio/*"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setDone(false);
            setError(null);
          }}
          style={{ fontSize: 13 }}
        />
        <div className="subtle" style={{ fontSize: 11.5, marginTop: 5, lineHeight: 1.5 }}>
          3–30 giây, đọc rõ; transcript phải khớp audio.
        </div>
      </div>
      <div>
        <span className="label">Transcript (nội dung đúng của đoạn mẫu)</span>
        <textarea
          className="field"
          rows={3}
          value={transcript}
          placeholder="Nhập đúng những gì được nói trong đoạn âm thanh mẫu…"
          onChange={(e) => {
            setTranscript(e.target.value);
            setDone(false);
          }}
          style={{ fontSize: 13, resize: "vertical" }}
        />
      </div>
      <div>
        <span className="label">Ngôn ngữ</span>
        <select className="field" value={lang} onChange={(e) => setLang(e.target.value)}>
          {VOICE_LANGUAGE_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      {error && (
        <div className="subtle" style={{ fontSize: 12.5, color: "var(--danger, #ef3e36)", display: "flex", alignItems: "center", gap: 6 }}>
          <Icon name="alert-triangle" size={13} /> {error}
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Button
          variant="primary"
          size="md"
          icon={saving ? "loader" : "upload"}
          disabled={!file || !transcript.trim() || saving}
          onClick={onApply}
        >
          {saving ? "Đang tải…" : seriesId ? "Lưu giọng mẫu" : "Dùng giọng mẫu này"}
        </Button>
      </div>
    </div>
  );
}

export function SetupScreen({ nav, route }: { nav: Nav; route: Route }) {
  // Two modes: configuring a brand-new series (route.draft, from the wizard) or
  // re-editing an existing one (route.series). In create mode there is no series
  // id yet — the series is persisted later by approveSeries on the Style screen,
  // so per-series uploads (music / voice sample) are deferred until then.
  const draft = route.draft;
  const series = route.series; // undefined in the create flow
  const headerName = series?.name || draft?.name || "Series mới";

  const initialSkill = series?.skill || draft?.skill || SKILLS[0].id;

  const [skill, setSkill] = React.useState(initialSkill);
  const [tmpl, setTmpl] = React.useState(
    SKILLS.find((s) => s.id === initialSkill)!.templates[0].id,
  );

  // New series config fields (Setup screen — integration §6 / risks #9). Provider
  // + key selection moved to the account-level Settings page (Cấu hình AI); this
  // screen is now series-only config: skill / language / length / density /
  // aspect / music.
  const [language, setLanguage] = React.useState(draft?.language || "vi");
  const [targetMinutes, setTargetMinutes] = React.useState(draft?.target_minutes || 10);
  const [density, setDensity] = React.useState<"light" | "standard" | "dense">(
    draft?.density || "standard",
  );
  const [aspect, setAspect] = React.useState<"16:9" | "9:16">(draft?.aspect || "16:9");
  const [musicName, setMusicName] = React.useState<string>("");

  // PER-SERIES toolset: script / image / voice provider for THIS series. Seeded
  // from the draft (wizard pre-picked the script provider) or the existing
  // series' providers when re-editing. The provider catalog + key status come
  // from GET /settings/providers (api keys stay per-user).
  const seriesProviders = series?.providers;
  const [catalog, setCatalog] = React.useState<ProviderKeys | null>(null);
  const [scriptProvider, setScriptProvider] = React.useState(
    draft?.providers?.script || seriesProviders?.script || "",
  );
  const [imageProvider, setImageProvider] = React.useState(
    draft?.providers?.image || seriesProviders?.image || "",
  );
  const [voiceProvider, setVoiceProvider] = React.useState(
    draft?.providers?.voice || seriesProviders?.voice || "",
  );

  // Staged voice-clone sample for the create flow (uploaded after approve makes
  // the series). Reported up to Style via the draft so it can upload post-approve.
  const [stagedVoiceSample, setStagedVoiceSample] = React.useState<
    { file: File; transcript: string; language: string } | null
  >(null);

  React.useEffect(() => {
    getProviderKeys()
      .then((k) => {
        setCatalog(k);
        // Default any unset task to the first usable (keyless or keyed) provider.
        const firstUsable = (items: ProviderKeyItem[]) =>
          items.find((p) => !p.requires_key || p.has_key)?.id || items[0]?.id || "";
        setScriptProvider((cur) => cur || firstUsable(k.script));
        setImageProvider((cur) => cur || firstUsable(k.image));
        setVoiceProvider((cur) => cur || firstUsable(k.voice));
      })
      .catch(() => setCatalog(null)); // backend unreachable → keep manual selection
  }, []);

  const voiceNeedsSample = catalog
    ? catalog.voice.find((v) => v.id === voiceProvider)?.note?.includes("clone") ||
      voiceProvider === "omnivoice"
    : voiceProvider === "omnivoice";

  const pickCategory = (id: string) => {
    setSkill(id);
    setTmpl(SKILLS.find((s) => s.id === id)!.templates[0].id);
  };
  const cat = SKILLS.find((s) => s.id === skill)!;
  const tplObj = cat.templates.find((t) => t.id === tmpl) || cat.templates[0];

  const onMusic = async (file: File | undefined) => {
    if (!file) return;
    setMusicName(file.name);
    // Per-series upload needs an existing series id; in the create flow it can be
    // added later from the project screen after approve.
    if (!series) return;
    try {
      await uploadMusic(series.id, file);
    } catch {
      /* upload best-effort; surfaced elsewhere */
    }
  };

  // Accumulate the Setup slice into the draft handed forward to the Style screen,
  // which runs the final approveSeries with outline + this config. The per-series
  // toolset (script / image / voice) is carried here so approve sets it on the
  // new series; api keys remain per-user.
  const buildNextDraft = (): SeriesDraft | undefined => {
    if (!draft) return undefined;
    return {
      ...draft,
      skill,
      language,
      target_minutes: targetMinutes,
      density,
      aspect,
      providers: {
        script: scriptProvider,
        image: imageProvider,
        voice: voiceProvider,
      },
      ...(stagedVoiceSample ? { voiceSample: stagedVoiceSample } : {}),
    };
  };

  const goToStyle = () => {
    const next = buildNextDraft();
    nav({ name: "style", ...(series ? { series } : {}), ...(next ? { draft: next } : {}) });
  };

  return (
    <div className="page" style={{ paddingBottom: 96 }}>
      <div style={{ marginBottom: 6 }}>
        <Badge tone="neutral" icon="folder">
          {headerName}
        </Badge>
      </div>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 22 }}>Cấu hình series</h2>
        <p className="muted" style={{ fontSize: 14, marginTop: 4 }}>
          Chọn thể loại, skill và các thông số riêng cho series này. Nhà cung cấp AI dùng chung được
          cấu hình ở trang Cấu hình AI.
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

      {/* Per-series AI toolset: script / image / voice provider for THIS series.
          API keys stay per-user (Cấu hình AI); here we only pick which provider
          this series uses. */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 14 }}>
        <Icon name="bot" size={18} style={{ color: "var(--brand)" }} />
        <h3 style={{ fontSize: 16 }}>3. Bộ công cụ AI cho series này</h3>
      </div>
      <Card style={{ padding: 16, marginBottom: 34 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14, gap: 10, flexWrap: "wrap" }}>
          <div className="subtle" style={{ fontSize: 12.5, lineHeight: 1.5, flex: 1, minWidth: 0 }}>
            Mỗi series chọn riêng provider kịch bản / ảnh / giọng. Key API nhập một lần trong Cấu hình
            AI và dùng chung.
          </div>
          <Button variant="ghost" size="sm" icon="key-round" onClick={() => nav({ name: "settings" })}>
            Quản lý key
          </Button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
          <ProviderPicker
            label="Viết kịch bản"
            options={catalog?.script ?? []}
            value={scriptProvider}
            onChange={setScriptProvider}
            nav={nav}
          />
          <ProviderPicker
            label="Dựng ảnh"
            options={catalog?.image ?? []}
            value={imageProvider}
            onChange={setImageProvider}
            nav={nav}
          />
          <ProviderPicker
            label="Giọng đọc"
            options={catalog?.voice ?? []}
            value={voiceProvider}
            onChange={setVoiceProvider}
            nav={nav}
          />
        </div>
        {voiceNeedsSample && (
          <VoiceSampleBlock
            seriesId={series?.id}
            language={language}
            staged={stagedVoiceSample}
            onStage={setStagedVoiceSample}
          />
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
          <b style={{ color: "var(--text)" }}>{tplObj.name}</b> · {cat.name} · {targetMinutes} phút ·{" "}
          {DENSITY_OPTIONS.find((d) => d.id === density)?.label}
        </div>
        {draft ? (
          // Create flow: the only forward path is Style, which runs approveSeries.
          <Button variant="primary" size="md" icon="palette" onClick={goToStyle}>
            Tiếp tục: chọn style hình ảnh
          </Button>
        ) : (
          <>
            <Button variant="secondary" size="md" icon="palette" onClick={goToStyle}>
              Chọn style hình ảnh
            </Button>
            <Button
              variant="primary"
              size="md"
              icon="check"
              onClick={() =>
                nav({ name: "project", ...(series ? { series } : {}), toast: "Đã lưu cấu hình Skill & Provider" })
              }
            >
              Lưu cấu hình
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
