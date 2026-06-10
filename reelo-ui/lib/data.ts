// ===== Mock data + types for Reelo Studio =====
// All UI copy is in Vietnamese. This is FRONTEND mock data only — when the
// backend is wired up, replace these with data fetched from the API (see
// lib/api.ts for the stubbed endpoints).

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
  providers?: { script: string; image: string; voice: string };
}

export interface ScriptSegment {
  id: string;
  text: string;
  img: string;
}

export type JobState = "done" | "running" | "queued" | "error";

export interface GenJob {
  id: string;
  name: string;
  icon: string;
  state: JobState;
  progress: number;
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
    | "project";
  series?: Series;
  episode?: Episode;
  // Carries the in-progress create-series state through the wizard flow.
  draft?: SeriesDraft;
  toast?: string;
}

export type Nav = (r: Route) => void;

// When NEXT_PUBLIC_REQUIRE_AUTH=false (offline mock demo, no backend) the
// create-series screens fall back to the static SERIES/seed data instead of
// failing on network errors. Prod (default) always hits the real API.
export const DEMO_FALLBACK =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_REQUIRE_AUTH) === "false";

export const SKILLS: Skill[] = [
  {
    id: "religion",
    name: "Tôn giáo học thuật",
    desc: "Phân tích lịch sử & triết lý tôn giáo, giọng văn trang trọng.",
    icon: "scroll",
    accent: "#7c3aed",
    templates: [
      { id: "rel-1", name: "Lịch sử tôn giáo chuyên sâu", author: "Reelo Team", official: true },
      { id: "rel-2", name: "Tôn giáo & Triết học so sánh", author: "TS. Lê Minh" },
      { id: "rel-3", name: "Huyền thoại & Tín ngưỡng cổ", author: "Cộng đồng" },
    ],
  },
  {
    id: "story",
    name: "Storytelling",
    desc: "Kể chuyện lôi cuốn, nhịp điệu kịch tính, hook mạnh.",
    icon: "drama",
    accent: "#ef3e36",
    templates: [
      { id: "st-1", name: "Kể chuyện lịch sử kịch tính", author: "Reelo Team", official: true },
      { id: "st-2", name: "Chân dung nhân vật", author: "Phạm Hùng" },
      { id: "st-3", name: "Bí ẩn & Hồ sơ chưa giải", author: "Cộng đồng" },
    ],
  },
  {
    id: "explain",
    name: "Explainer",
    desc: "Giải thích khái niệm khó thành dễ hiểu trong vài phút.",
    icon: "lightbulb",
    accent: "#0ea5e9",
    templates: [
      { id: "ex-1", name: "Khoa học trong 5 phút", author: "Reelo Team", official: true },
      { id: "ex-2", name: "Công nghệ dễ hiểu", author: "Nguyễn Khoa" },
      { id: "ex-3", name: "Kinh tế căn bản", author: "Cộng đồng" },
    ],
  },
  {
    id: "news",
    name: "Tin tức nhanh",
    desc: "Tổng hợp & bình luận tin nóng, cập nhật hằng ngày.",
    icon: "newspaper",
    accent: "#16a34a",
    templates: [
      { id: "nw-1", name: "Bản tin tổng hợp", author: "Reelo Team", official: true },
      { id: "nw-2", name: "Bình luận thời sự", author: "Đỗ Lan" },
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

export const SERIES: Series[] = [
  {
    id: "s1",
    name: "Bí ẩn các tôn giáo cổ đại",
    topic: "Tôn giáo & Lịch sử",
    skill: "religion",
    providers: { script: "claude", image: "gemini", voice: "eleven" },
    cover: "Đền thờ cổ, ánh sáng vàng",
    episodes: [
      { id: "e1", title: "Nguồn gốc của các vị thần", status: "published", dur: "9:42", views: "12K" },
      { id: "e2", title: "Đa thần giáo Lưỡng Hà", status: "published", dur: "11:08", views: "8.3K" },
      { id: "e3", title: "Tôn giáo Ai Cập cổ đại", status: "assembled", dur: "10:21" },
      { id: "e4", title: "Bí ẩn các giáo phái Hy Lạp", status: "assets" },
      { id: "e5", title: "Hỏa giáo & Zoroaster", status: "scripted" },
      { id: "e6", title: "Tín ngưỡng La Mã sơ khai", status: "draft" },
      { id: "e7", title: "Sự trỗi dậy của độc thần giáo", status: "draft" },
      { id: "e8", title: "Di sản còn lại ngày nay", status: "draft" },
    ],
  },
  {
    id: "s2",
    name: "Đế chế La Mã: Trỗi dậy & Sụp đổ",
    topic: "Lịch sử La Mã",
    skill: "story",
    providers: { script: "chatgpt", image: "kie", voice: "hf" },
    cover: "Đấu trường La Mã lúc hoàng hôn",
    episodes: [
      { id: "e1", title: "Lập quốc bên dòng Tiber", status: "published", dur: "8:55", views: "21K" },
      { id: "e2", title: "Cộng hòa & những cuộc chiến", status: "published", dur: "12:30", views: "15K" },
      { id: "e3", title: "Caesar vượt sông Rubicon", status: "published", dur: "13:12", views: "31K" },
      { id: "e4", title: "Augustus & thời hoàng kim", status: "assembled" },
      { id: "e5", title: "Khủng hoảng thế kỷ III", status: "scripted" },
      { id: "e6", title: "Sự sụp đổ của phương Tây", status: "draft" },
    ],
  },
  {
    id: "s3",
    name: "Vũ trụ trong 5 phút",
    topic: "Khoa học vũ trụ",
    skill: "explain",
    providers: { script: "gemini", image: "gemini", voice: "edge" },
    cover: "Thiên hà xoáy, tông xanh tím",
    episodes: [
      { id: "e1", title: "Lỗ đen thực sự là gì?", status: "scripted" },
      { id: "e2", title: "Vì sao bầu trời tối?", status: "draft" },
      { id: "e3", title: "Vật chất tối quanh ta", status: "draft" },
      { id: "e4", title: "Sự sống ngoài Trái Đất", status: "draft" },
    ],
  },
];

// Outline produced by the chat wizard (screen 2 demo state)
export const WIZARD_SEED: OutlineItem[] = [
  { id: "w1", title: "Tập 1 — Phật giáo: Con đường trung đạo", desc: "Bối cảnh ra đời, Tứ diệu đế, ảnh hưởng tới châu Á.", pick: true },
  { id: "w2", title: "Tập 2 — Ấn Độ giáo: Vạn thần và luân hồi", desc: "Hệ thống thần linh, nghiệp báo, các trường phái.", pick: true },
  { id: "w3", title: "Tập 3 — Do Thái giáo: Giao ước cổ xưa", desc: "Lịch sử dân tộc, kinh Torah, truyền thống.", pick: true },
  { id: "w4", title: "Tập 4 — Kitô giáo lan tỏa toàn cầu", desc: "Từ một giáo phái nhỏ đến tôn giáo lớn nhất thế giới.", pick: true },
  { id: "w5", title: "Tập 5 — Hồi giáo: Năm trụ cột", desc: "Nguồn gốc, kinh Quran, sự bành trướng.", pick: false },
];

// Script segments for the workspace editor (screen 3 demo)
export const SCRIPT_SEGMENTS: ScriptSegment[] = [
  { id: "seg1", text: "Bốn nghìn năm trước, giữa hai con sông Tigris và Euphrates, những con người đầu tiên ngước nhìn bầu trời và đặt ra một câu hỏi vẫn ám ảnh chúng ta đến tận hôm nay: ai đã tạo ra tất cả những điều này?", img: "Cảnh bình minh trên đồng bằng Lưỡng Hà" },
  { id: "seg2", text: "Người Sumer tin rằng vũ trụ được cai quản bởi hàng trăm vị thần — mỗi dòng sông, mỗi cơn bão, mỗi mùa gặt đều có một đấng linh thiêng trông coi.", img: "Phù điêu các vị thần Sumer" },
  { id: "seg3", text: "Đứng đầu là Anu, vị thần bầu trời. Bên cạnh ngài là Enlil của gió bão, và Enki — vị thần của nước ngọt và trí tuệ, người được cho là đã ban cho loài người nền văn minh.", img: "Tượng thần Anu uy nghi" },
  { id: "seg4", text: "Nhưng các vị thần không hề xa cách. Trong những ngôi đền ziggurat khổng lồ, con người dâng lễ vật mỗi ngày, vì họ tin rằng sự sống còn của cả thành bang phụ thuộc vào lòng thành ấy.", img: "Đền ziggurat về đêm có lửa" },
];

// Generation jobs (screen 3 progress demo)
export const GEN_JOBS: GenJob[] = [
  { id: "j-voice", name: "Tổng hợp giọng đọc", icon: "mic", state: "done", progress: 100 },
  { id: "j-img1", name: "Ảnh đoạn 1", icon: "image", state: "done", progress: 100 },
  { id: "j-img2", name: "Ảnh đoạn 2", icon: "image", state: "done", progress: 100 },
  { id: "j-img3", name: "Ảnh đoạn 3", icon: "image", state: "running", progress: 64 },
  { id: "j-img4", name: "Ảnh đoạn 4", icon: "image", state: "queued", progress: 0 },
  { id: "j-render", name: "Render video .mp4", icon: "film", state: "queued", progress: 0 },
];

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
