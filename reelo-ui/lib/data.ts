// ===== Types + static catalogs for Reelo Studio =====
// All UI copy is in Vietnamese. This file holds shared TYPES, display catalogs
// (SKILLS/PROVIDERS — labels rendered in the UI, mirroring services.yaml) and
// derived helpers. It contains NO business mock data: real series/episodes/jobs
// come from the API (lib/api.ts). Demo-only sample data lives in
// lib/demo-fixtures.ts and is gated behind DEMO_FALLBACK at every call site.

// Matches GET /providers (reelo-backend ProviderOption). `cost_tier` is whether
// the provider costs money; `requires_key` is whether the user must supply a
// BYOK key (every provider needs one EXCEPT keyless Edge-TTS — integration
// risk #1). `key_help_url` links to where the user gets that key.
export type CostTier = "free" | "paid";

export interface ProviderOptionData {
  id: string;
  name: string;
  cost_tier: CostTier;
  requires_key: boolean;
  key_help_url?: string;
  note: string;
}

export interface ProviderGroup {
  label: string;
  icon: string;
  options: ProviderOptionData[];
}

export interface SkillTemplate {
  id: string;
  name: string;
  author: string;
  official?: boolean;
}

export interface Skill {
  id: string;
  name: string;
  desc: string;
  icon: string;
  accent: string;
  templates: SkillTemplate[];
}

export type EpisodeStatus =
  | "draft"
  | "scripted"
  | "assets"
  | "assembled"
  | "published";

export interface Episode {
  id: string;
  title: string;
  status: EpisodeStatus;
  dur?: string;
  views?: string;
  // Lazy script-gen progress (from GET /episodes/{id}). Set by the project screen
  // only for `draft` episodes so it can show a "✍️ Đang viết kịch bản…" badge +
  // a "Xem tiến độ" button while the worker is writing the first script. Undefined
  // for episodes whose state is unambiguous from `status` alone.
  scriptStatus?: "running" | "done" | "error" | "cancelled" | null;
}

export interface Series {
  id: string;
  name: string;
  topic: string;
  skill: string;
  providers: { script: string; image: string; voice: string };
  cover: string;
  episodes: Episode[];
}

export interface OutlineItem {
  id: string;
  title: string;
  desc: string;
  pick: boolean;
}

// In-progress "create series" draft carried across wizard -> setup -> style ->
// approve (the screens don't share a store, so it rides on Route.draft). All
// fields are optional/partial because each screen fills in its slice; the final
// APPROVE step (style screen) reads the accumulated draft to call approveSeries.
export interface SeriesDraft {
  name: string;
  topic: string;
  outline: OutlineItem[];
  // Setup-screen config slice (filled by setup.tsx before reaching style).
  skill?: string;
  language?: string;
  target_minutes?: number;
  density?: "light" | "standard" | "dense";
  aspect?: "16:9" | "9:16";
  // PER-SERIES toolset chosen in the create flow (script/image/voice provider).
  providers?: { script: string; image: string; voice: string };
  // Staged voice-clone sample (OmniVoice) to upload right after approve creates
  // the series. In-memory only (rides on Route.draft, never persisted/serialized).
  voiceSample?: { file: File; transcript: string; language: string };
}

export interface ScriptSegment {
  id: string;
  text: string;
  img: string;
}

export type JobState = "done" | "running" | "queued" | "error" | "paused";

export interface GenJob {
  id: string;
  name: string;
  icon: string;
  state: JobState;
  progress: number;
  // Captured failure detail (copyable in the UI) — only set when state==="error".
  stderr?: string | null;
  // Signed URL to the finished image (image jobs only, once done) so the produce
  // screen previews each picture as it lands in storage. Minted fresh per poll.
  preview_url?: string | null;
}

// Internal client-side route (replace with a real router when wiring pages/URLs)
export interface Route {
  name:
    | "dashboard"
    | "wizard"
    | "setup"
    | "style"
    | "workspace"
    | "image-select"
    | "review"
    | "project"
    | "settings";
  series?: Series;
  episode?: Episode;
  // Carries the in-progress create-series state through the wizard flow.
  draft?: SeriesDraft;
  // Generation job id carried image-select/workspace → workspace producing view
  // (and so a reopened workspace can resume polling an in-flight produce).
  jobId?: string;
  // When set, workspace opens straight into the producing view (e.g. arriving
  // back from image-select after startGeneration was already called).
  producing?: boolean;
  toast?: string;
}

export type Nav = (r: Route) => void;

// Offline-demo switch. ONLY true when a developer explicitly opts out of auth
// (NEXT_PUBLIC_REQUIRE_AUTH=false) to browse the UI with no backend. In every
// other case — including prod, where the env var is unset — it is FALSE, so no
// demo fixture (lib/demo-fixtures.ts) is ever seeded. Default = false.
export const DEMO_FALLBACK =
  typeof process !== "undefined" &&
  process.env.NEXT_PUBLIC_REQUIRE_AUTH === "false";

// Skills are WRITING STYLES, not topic gates: every skill works for ANY subject
// (animals, science, history, technology, storytelling…). `explain` is the
// general-purpose default (listed first → SKILLS[0]); `religion` is a
// specialised scholarly style, offered as one option among others.
export const SKILLS: Skill[] = [
  {
    id: "explain",
    name: "Giải thích / Khoa học",
    desc: "Giải thích rõ ràng, chính xác mọi chủ đề: khoa học, động vật, công nghệ, kinh tế…",
    icon: "lightbulb",
    accent: "#0ea5e9",
    templates: [
      { id: "ex-1", name: "Giải thích phổ thông", author: "Reelo Team", official: true },
      { id: "ex-2", name: "Khoa học & Tự nhiên", author: "Nguyễn Khoa" },
      { id: "ex-3", name: "Công nghệ dễ hiểu", author: "Cộng đồng" },
    ],
  },
  {
    id: "story",
    name: "Kể chuyện",
    desc: "Kể chuyện lôi cuốn, giàu cảm xúc cho mọi chủ đề — nhân vật, sự kiện, bí ẩn.",
    icon: "drama",
    accent: "#ef3e36",
    templates: [
      { id: "st-1", name: "Kể chuyện kịch tính", author: "Reelo Team", official: true },
      { id: "st-2", name: "Chân dung nhân vật", author: "Phạm Hùng" },
      { id: "st-3", name: "Bí ẩn & Hồ sơ chưa giải", author: "Cộng đồng" },
    ],
  },
  {
    id: "news",
    name: "Tài liệu / Tin",
    desc: "Tường thuật khách quan, dựa trên dữ kiện: sự kiện, vấn đề, xu hướng.",
    icon: "newspaper",
    accent: "#16a34a",
    templates: [
      { id: "nw-1", name: "Phóng sự tài liệu", author: "Reelo Team", official: true },
      { id: "nw-2", name: "Bản tin tổng hợp", author: "Đỗ Lan" },
    ],
  },
  {
    id: "religion",
    name: "Tôn giáo & Lịch sử (học thuật)",
    desc: "Phân tích chuyên sâu nội dung tôn giáo/lịch sử, giọng văn học thuật trang trọng.",
    icon: "scroll",
    accent: "#7c3aed",
    templates: [
      { id: "rel-1", name: "Lịch sử tôn giáo chuyên sâu", author: "Reelo Team", official: true },
      { id: "rel-2", name: "Tôn giáo & Triết học so sánh", author: "TS. Lê Minh" },
      { id: "rel-3", name: "Huyền thoại & Tín ngưỡng cổ", author: "Cộng đồng" },
    ],
  },
];

// Fallback catalog mirroring reelo-backend/services.yaml. At runtime the Setup
// screen replaces this with the live GET /providers response (api.getProviders);
// this static copy keeps the UI usable offline and documents the contract shape.
export const PROVIDERS: Record<string, ProviderGroup> = {
  script: {
    label: "Nghiên cứu & Kịch bản",
    icon: "pen-line",
    options: [
      { id: "gemini", name: "Gemini (Google AI Studio)", cost_tier: "free", requires_key: true, key_help_url: "https://aistudio.google.com/apikey", note: "Google" },
      { id: "claude", name: "Claude (Anthropic)", cost_tier: "paid", requires_key: true, key_help_url: "https://console.anthropic.com/settings/keys", note: "Anthropic" },
      { id: "claude-cli", name: "Claude (đăng nhập subscription)", cost_tier: "free", requires_key: true, key_help_url: "https://docs.anthropic.com/en/docs/claude-code/setup#authentication", note: "BYO tài khoản Claude" },
      { id: "chatgpt", name: "ChatGPT (OpenAI)", cost_tier: "paid", requires_key: true, key_help_url: "https://platform.openai.com/api-keys", note: "OpenAI" },
      { id: "deepseek", name: "DeepSeek", cost_tier: "paid", requires_key: true, key_help_url: "https://platform.deepseek.com/api_keys", note: "Rất rẻ" },
    ],
  },
  image: {
    label: "Hình ảnh & Video",
    icon: "image",
    options: [
      { id: "kie", name: "kie.ai", cost_tier: "free", requires_key: true, key_help_url: "https://kie.ai", note: "Flux" },
      { id: "gemini", name: "Gemini (Imagen)", cost_tier: "free", requires_key: true, key_help_url: "https://aistudio.google.com/apikey", note: "Imagen" },
      { id: "web", name: "Web · Ảnh thật + Video clip", cost_tier: "free", requires_key: false, note: "Trộn ảnh Commons + clip Pexels (nếu có key)" },
      { id: "web-commons", name: "Web · Ảnh thật (Commons)", cost_tier: "free", requires_key: false, note: "Ảnh thật PD/CC, không cần key" },
      { id: "web-openverse", name: "Web · Ảnh thật (Openverse)", cost_tier: "free", requires_key: false, note: "~800M ảnh CC/PD, không cần key" },
      { id: "web-pexels", name: "Web · Video clip (Pexels)", cost_tier: "free", requires_key: true, key_help_url: "https://www.pexels.com/api/", note: "Clip thật CC0, cần key Pexels" },
      { id: "sd", name: "Stable Diffusion", cost_tier: "paid", requires_key: true, key_help_url: "https://platform.stability.ai/account/keys", note: "Self-host" },
    ],
  },
  voice: {
    label: "Giọng đọc (TTS)",
    icon: "mic",
    options: [
      { id: "edge", name: "Edge-TTS", cost_tier: "free", requires_key: false, note: "Miễn phí, không cần key" },
      { id: "eleven", name: "ElevenLabs", cost_tier: "paid", requires_key: true, key_help_url: "https://elevenlabs.io/app/settings/api-keys", note: "Siêu thực" },
      // OmniVoice zero-shot clone: keyless (Reelo-hosted GPU) but needs an
      // uploaded voice sample + transcript before producing.
      { id: "omnivoice", name: "Giọng clone (OmniVoice)", cost_tier: "paid", requires_key: false, note: "Clone giọng — cần tải mẫu + transcript" },
      { id: "hf", name: "HuggingFace", cost_tier: "paid", requires_key: true, key_help_url: "https://huggingface.co/settings/tokens", note: "Open models" },
    ],
  },
};

export const PIPELINE = [
  { id: "script", name: "Kịch bản", icon: "pen-line" },
  { id: "voice", name: "Giọng đọc", icon: "mic" },
  { id: "images", name: "Hình ảnh", icon: "image" },
  { id: "assemble", name: "Dựng video", icon: "film" },
  { id: "review", name: "Duyệt & Xuất bản", icon: "youtube" },
];

export const EP_STATUS: Record<EpisodeStatus, { label: string; color: string; step: number }> = {
  draft: { label: "Nháp", color: "#94a3b8", step: 0 },
  scripted: { label: "Đã có kịch bản", color: "#0ea5e9", step: 1 },
  assets: { label: "Đang tạo asset", color: "#f59e0b", step: 2 },
  assembled: { label: "Đã dựng", color: "#8b5cf6", step: 3 },
  published: { label: "Đã xuất bản", color: "#16a34a", step: 4 },
};

// ---- Derived helpers used across screens ----
export function skillOf(id: string): Skill {
  return SKILLS.find((s) => s.id === id) || SKILLS[0];
}
export function provName(group: string, id: string): string {
  return (PROVIDERS[group].options.find((o) => o.id === id) || ({} as ProviderOptionData)).name;
}
export function pubCount(s: Series): number {
  return s.episodes.filter((e) => e.status === "published").length;
}
