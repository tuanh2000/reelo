// ===== Backend integration surface =====
//
// Real network calls to the Reelo FastAPI backend. The base URL comes from
// NEXT_PUBLIC_API_BASE (default http://localhost:8000). Every request is sent
// with credentials so the Google-OAuth session cookie rides along. The 9 stub
// functions kept their signatures so screens that adopt them need no changes;
// where the backend speaks the full SeriesSpec shape we map to/from the UI
// `Series`/`Episode` types (see lib/data.ts).
//
// Contract source: reelo-backend/web/schemas.py + models/spec.py.

import type {
  GenJob,
  JobState,
  OutlineItem,
  Route,
  Series,
  Episode,
  EpisodeStatus,
} from "./data";

export const API_BASE =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_BASE) ||
  "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: {
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(headers || {}),
    },
    ...(json !== undefined ? { body: JSON.stringify(json) } : {}),
    ...rest,
  });
  if (!res.ok) {
    let body: unknown = null;
    let detail = res.statusText;
    try {
      body = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** ---- Backend SeriesSpec wire types (models/spec.py) -------------------- */
export interface SegmentSpec {
  index: number;
  narration: string;
  image_prompt: string;
  image_label: string;
  // Optional English search keywords for real-photo providers (web-commons).
  // Backend may omit it (generative providers); kept optional for round-trips.
  image_query?: string | null;
}
export interface EpisodeSpec {
  episode_id: string;
  title: string;
  order: number;
  desc?: string | null;
  target_minutes?: number | null;
  status: EpisodeStatus;
  youtube?: { title?: string; description?: string; tags?: string[] } | null;
  segments: SegmentSpec[];
}
export interface ImageStyleSpec {
  preset_id: string;
  base_prompt: string;
  palette: string[];
  description: string;
  aspect: "16:9" | "9:16";
  style_layer?: string | null;
}
export interface VoiceSampleSpec {
  audio_key: string;
  transcript: string;
  language?: string | null;
}
export interface VoiceConfigSpec {
  provider: string;
  voice_id: string;
  settings?: Record<string, unknown> | null;
  // Voice-clone (OmniVoice): mode "clone" uses voice_sample instead of voice_id.
  mode?: "preset" | "clone";
  voice_sample?: VoiceSampleSpec | null;
}
export interface SeriesSpec {
  schema_version?: number;
  series_id: string;
  name: string;
  topic: string;
  skill: string;
  language: string;
  target_minutes: number;
  density: "light" | "standard" | "dense";
  providers: { script: string; image: string; voice: string };
  image_style: ImageStyleSpec;
  voice: VoiceConfigSpec;
  episodes: EpisodeSpec[];
  music?: Record<string, unknown> | null;
  subtitles?: Record<string, unknown> | null;
}

/** Project a backend SeriesSpec onto the UI `Series` shape. */
export function specToSeries(spec: SeriesSpec): Series {
  const cover =
    spec.episodes.find((e) => e.segments?.length)?.segments[0]?.image_prompt || "";
  return {
    id: spec.series_id,
    name: spec.name,
    topic: spec.topic,
    skill: spec.skill,
    providers: spec.providers,
    cover,
    episodes: spec.episodes.map(
      (e): Episode => ({ id: e.episode_id, title: e.title, status: e.status }),
    ),
  };
}

/** Series CRUD ------------------------------------------------------------ */
export async function listSeries(): Promise<Series[]> {
  const data = await request<{ series: SeriesSpec[] }>("/series");
  return data.series.map(specToSeries);
}

/**
 * Persist a full SeriesSpec. The UI `Series` type is a projection, so callers
 * that already hold a SeriesSpec (from approve / listSeriesSpecs) should pass
 * it; a bare UI `Series` cannot round-trip (it lacks language/style/voice).
 */
export async function saveSeries(spec: SeriesSpec): Promise<Series> {
  const data = await request<{ series: SeriesSpec }>(`/series/${spec.series_id}`, {
    method: "PUT",
    json: { series: spec },
  });
  return specToSeries(data.series);
}

export async function createSeries(spec: SeriesSpec): Promise<SeriesSpec> {
  const data = await request<{ series: SeriesSpec }>("/series", {
    method: "POST",
    json: { series: spec },
  });
  return data.series;
}

/** Raw SeriesSpec list (when a screen needs the full spec, not the projection). */
export async function listSeriesSpecs(): Promise<SeriesSpec[]> {
  const data = await request<{ series: SeriesSpec[] }>("/series");
  return data.series;
}

/** Wizard chat / LLM ------------------------------------------------------ */
export interface ChatTurn {
  reply: string;
  outline?: OutlineItem[];
}
/** Optional Setup selection so Phase A honours the chosen skill/lang/provider. */
export interface WizardSetup {
  skill?: string;
  language?: string;
  provider?: string;
}
export async function sendWizardMessage(
  topic: string,
  history: { role: "ai" | "user"; text: string }[],
  setup: WizardSetup = {},
): Promise<ChatTurn> {
  const data = await request<{ reply: string; outline?: OutlineItem[] | null }>(
    "/wizard/message",
    { method: "POST", json: { idea: topic, history, ...setup } },
  );
  return { reply: data.reply, outline: data.outline || undefined };
}

/** Phase B approve — build + persist a SeriesSpec shell. */
export interface SeriesConfig {
  skill: string;
  language: string;
  target_minutes: number;
  density: "light" | "standard" | "dense";
  aspect: "16:9" | "9:16";
  providers: { script: string; image: string; voice: string };
  voice: VoiceConfigSpec;
  image_style: ImageStyleSpec;
}
export async function approveSeries(
  name: string,
  topic: string,
  outline: OutlineItem[],
  config: SeriesConfig,
): Promise<SeriesSpec> {
  const data = await request<{ series: SeriesSpec }>("/wizard/approve", {
    method: "POST",
    json: { name, topic, outline, config },
  });
  return data.series;
}

/** Lazy per-episode script gen (enqueue; poll the episode until scripted). */
export async function generateEpisodeScript(episodeId: string): Promise<EpisodeSpec> {
  const data = await request<{ episode: EpisodeSpec }>(
    `/episodes/${episodeId}/script`,
    { method: "POST" },
  );
  return data.episode;
}

/** Asset generation pipeline (poll progress) ----------------------------- */
export async function startGeneration(
  seriesId: string,
  episodeId: string,
): Promise<{ jobId: string }> {
  const data = await request<{ jobId: string; cost_estimate?: unknown }>(
    "/generation/start",
    { method: "POST", json: { series_id: seriesId, episode_id: episodeId } },
  );
  return { jobId: data.jobId };
}
export async function pollGeneration(jobId: string): Promise<GenJob[]> {
  const data = await request<{ jobs: GenJob[] }>(`/generation/${jobId}`);
  return data.jobs;
}
export async function retryChild(jobId: string, childId: string): Promise<GenJob[]> {
  const data = await request<{ jobs: GenJob[] }>(
    `/generation/${jobId}/retry/${childId}`,
    { method: "POST" },
  );
  return data.jobs;
}

/** Media curation (M2-12 / M2-13) — web-* candidate selection (photo OR clip) - */
export type MediaType = "image" | "video";
export interface ImageCandidate {
  id: string;
  thumb_url: string;
  full_url: string;
  title: string;
  author: string;
  license: string;
  source_url: string;
  descriptionurl: string;
  width: number;
  height: number;
  // media-aware fields (M2-13). `media_type` defaults to "image" server-side.
  media_type: MediaType;
  duration: number; // source clip length in seconds (video only)
  poster_url: string; // representative image for the grid (video only)
  preview_url: string; // optional short preview clip for hover-play (video)
  video_url: string; // mp4 downloaded at render time (video only)
  provider?: string | null; // "web-commons" | "web-pexels" (merge source)
}
export interface SegmentCandidates {
  index: number;
  query: string;
  text: string;
  candidates: ImageCandidate[];
  chosen_id: string | null;
}
export interface ImageCandidatesResult {
  provider: string;
  segments: SegmentCandidates[];
}

/**
 * Per-segment media candidate grids for human curation. The grid mixes real
 * photos (web-commons) with real video clips (web-pexels) when the image
 * provider is `web` (aggregate) or a single web-* provider; AI providers respond
 * 409 (no selection step). First call searches + caches; later calls return the
 * cache.
 */
export async function getImageCandidates(
  episodeId: string,
): Promise<ImageCandidatesResult> {
  return request<ImageCandidatesResult>(`/episodes/${episodeId}/image-candidates`);
}

/** Apply the user's choices ({segmentIndex: candidateId}); returns new state. */
export async function saveImageSelection(
  episodeId: string,
  selections: Record<number, string>,
): Promise<ImageCandidatesResult> {
  return request<ImageCandidatesResult>(`/episodes/${episodeId}/image-selection`, {
    method: "POST",
    json: { selections },
  });
}

/** Background music upload (optional). */
export async function uploadMusic(seriesId: string, file: File): Promise<{ path: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/series/${seriesId}/music`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return (await res.json()) as { path: string };
}

/**
 * Voice-clone reference upload (OmniVoice provider). Sends a sample clip +
 * its transcript + language; the backend normalizes to wav 24 kHz mono, stores
 * it, and flips the series voice config to clone mode. Returns the stored key,
 * the measured duration, and the resulting VoiceConfig.
 */
export interface VoiceSampleResult {
  audio_key: string;
  duration_s: number;
  voice: VoiceConfigSpec;
}
export async function uploadVoiceSample(
  seriesId: string,
  audio: File,
  transcript: string,
  language: string,
): Promise<VoiceSampleResult> {
  const form = new FormData();
  form.append("audio", audio);
  form.append("transcript", transcript);
  form.append("language", language);
  const res = await fetch(`${API_BASE}/series/${seriesId}/voice-sample`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body && body.detail) detail = String(body.detail);
    } catch {
      /* non-JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as VoiceSampleResult;
}

/** Style inference -------------------------------------------------------- */
export async function inferStyle(
  referenceImages: File[],
): Promise<{ palette: string[]; description: string }> {
  const form = new FormData();
  // FastAPI binds the multipart field to the handler param name; the /style/infer
  // endpoint expects `reference_images` (web/routers/style.py), NOT `images`.
  for (const f of referenceImages) form.append("reference_images", f);
  const res = await fetch(`${API_BASE}/style/infer`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return (await res.json()) as { palette: string[]; description: string };
}

/** YouTube publish / export ---------------------------------------------- */
export interface PublishMeta {
  title: string;
  description: string;
  tags: string[];
  visibility: "public" | "unlisted" | "private";
  thumbnailIndex: number;
}
export interface ExportResult {
  videoUrl: string;
  srtUrl?: string | null;
  thumbnailUrl?: string | null;
  metadata: Record<string, unknown>;
}
export async function publishToYouTube(
  seriesId: string,
  episodeId: string,
  meta: PublishMeta,
): Promise<ExportResult> {
  // v1: the backend returns signed URLs + metadata (user uploads to YouTube).
  const data = await request<{
    videoUrl: string;
    srtUrl?: string | null;
    thumbnailUrl?: string | null;
    metadata: Record<string, unknown>;
  }>("/publish/export", {
    method: "POST",
    json: {
      series_id: seriesId,
      episode_id: episodeId,
      meta: {
        title: meta.title,
        description: meta.description,
        tags: meta.tags,
        visibility: meta.visibility,
        thumbnailIndex: meta.thumbnailIndex,
      },
    },
  });
  return {
    videoUrl: data.videoUrl,
    srtUrl: data.srtUrl,
    thumbnailUrl: data.thumbnailUrl,
    metadata: data.metadata,
  };
}

/** Providers catalog (Module 3) ------------------------------------------ */
export interface ProviderOption {
  id: string;
  name: string;
  cost_tier: "free" | "paid";
  requires_key: boolean;
  key_help_url?: string | null;
  note?: string | null;
}
export interface ProvidersResponse {
  script: ProviderOption[];
  image: ProviderOption[];
  voice: ProviderOption[];
}
export async function getProviders(): Promise<ProvidersResponse> {
  return request<ProvidersResponse>("/providers");
}

/** BYOK key storage ------------------------------------------------------- */
export async function saveApiKey(
  provider: string,
  key: string,
): Promise<{ key_ref: string; valid: boolean | null }> {
  return request<{ key_ref: string; valid: boolean | null }>("/keys", {
    method: "POST",
    json: { provider, key },
  });
}
export async function keysStatus(): Promise<
  Record<string, { present: boolean; valid: boolean | null }>
> {
  const data = await request<{
    keys: Record<string, { present: boolean; valid: boolean | null }>;
  }>("/keys/status");
  return data.keys;
}
export async function deleteApiKey(keyRef: string): Promise<void> {
  await request(`/keys/${keyRef}`, { method: "DELETE" });
}

/** Auth (Google OAuth session) ------------------------------------------- */
export interface Me {
  id: string;
  email: string;
  name?: string | null;
  picture?: string | null;
}
/** Returns the current user, or null if not logged in (401). */
export async function getMe(): Promise<Me | null> {
  try {
    return await request<Me>("/auth/me");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}
/** Full-page redirect to Google's consent screen (server-driven). */
export function loginUrl(): string {
  return `${API_BASE}/auth/login`;
}
export async function logout(): Promise<void> {
  await request("/auth/logout", { method: "POST" });
}

// Re-export Route so backend code that builds nav targets can import from one place.
export type { Route, JobState };
