/**
 * Typed client for the AutoClip API.
 *
 * Same-origin throughout: the server hands out the built bundle, so there is no
 * base URL to configure and no CORS to negotiate.
 */

export interface Source {
  id: string
  type: string
  title: string
  url: string | null
  filename: string | null
  channel: string | null
  duration_s: number
  width: number | null
  height: number | null
  fps: number | null
  has_audio: boolean
  has_video: boolean
  created_at: string
}

export type JobStatus = 'queued' | 'running' | 'failed' | 'done' | 'cancelled'

export interface Job {
  id: string
  source_id: string
  status: JobStatus
  current_stage: string
  progress: number
  error: string | null
  provider: string
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  source: Source | null
}

export interface ExportRecord {
  id: string
  clip_id: string
  ratio: string
  style: string
  size_bytes: number
  created_at: string
  download_url: string
}

export type ClipStatus = 'candidate' | 'kept' | 'discarded' | 'exported'

export interface Clip {
  id: string
  job_id: string
  rank: number
  start_s: number
  end_s: number
  duration_s: number
  start_word: number
  end_word: number
  title: string
  hook: string
  score: number
  reason: string
  status: ClipStatus
  user_trimmed: boolean
  caption_style: string
  ratio: string
  exports: ExportRecord[]
}

export interface Word {
  text: string
  start: number
  end: number
  speaker: string | null
}

export interface CropKeyframe {
  t: number
  x: number
  y: number
}

export interface CropSegment {
  start_s: number
  end_s: number
  width: number
  height: number
  keyframes: CropKeyframe[]
  strategy: 'track' | 'wide' | 'general'
  zoom: number
  fit: boolean
}

/** Mirrors autoclip.pipeline.reframe.croppath.CropPath. */
export interface CropPath {
  source_width: number
  source_height: number
  segments: CropSegment[]
}

export interface CaptionStyle {
  key: string
  label: string
  description: string
  preview: {
    font: string
    primary: string
    accent: string | null
    outline: string
    outlineWidth: number
    allCaps: boolean
    sizeRatio: number
    marginRatio: number
    boxed: boolean
    animation: string
    maxWords: number
  }
}

export interface ProviderStatus {
  name: string
  available: boolean
  detail: string
  models: string[]
  requires_key: boolean
  has_key: boolean
}

export interface Settings {
  active_provider: string
  providers: Record<string, { model: string; base_url: string | null }>
  whisper: { model: string; compute_type: string; language: string; diarization: boolean }
  clips: { min_duration_s: number; max_duration_s: number; max_clips: number }
  ingest: {
    ytdlp_format: string
    cookies_from_browser: string
    prefer_youtube_captions: boolean
  }
  export: {
    ratio: string
    caption_style: string
    loudness_lufs: number
    prefer_hardware_encoder: boolean
    crf: number
    write_srt: boolean
  }
  insecure_secret_storage: boolean
  keys_present: Record<string, boolean>
}

export interface SystemStatus {
  ready: boolean
  python_version: string
  platform: string
  ffmpeg_version: string | null
  has_libass: boolean
  nvenc_works: boolean
  accel: string
  gpu_name: string | null
  compute_type: string
  diarization_available: boolean
}

export interface JobSettingsOverrides {
  provider?: string
  whisper_model?: string
  language?: string
  diarization?: boolean
  min_duration_s?: number
  max_duration_s?: number
  max_clips?: number
  caption_style?: string
  ratio?: string
  campaign_id?: string
  rules_confirmed?: boolean
}

export interface CampaignPayout {
  platform: string
  rate_cents: number
  min_cents: number
  max_cents: number
  min_views: number
  max_views: number
}

export interface Campaign {
  id: string
  name: string
  host: string
  status: string
  launched: string
  description: string
  payouts: CampaignPayout[]
  budget_cents: number
  spent_cents: number
  creators: number
  approved_clips: number
  total_views: number
  needs_application: boolean
  rule_count: number
  requirement_count: number
  reference_count: number
  rules: string
  brand: string
  required_tags: string
  rules_complete: boolean
  standing_rules: string[]
  blocked_topics: string[]
}

/** The rules a job was made under. */
export interface JobCampaign {
  campaign_id: string
  name: string
  brief: string
  brand: string
  standing_rules: string[]
}

export interface PostKit {
  approach: string
  keywords: string
  title: string
  caption: string
  category: string
  language: string
  flags: string[]
}

export interface Post {
  id: string
  clip_id: string
  platform: string
  url: string
  approach: string
  title: string
  caption: string
  views_48h: number | null
  views_7d: number | null
  submitted: boolean
  posted_at: string
}

export interface PostRow {
  clip_id: string
  job_id: string
  clip_title: string
  platform: string
  post: Post | null
}

/** A live OpenAI report. Citations must stay visible and clickable. */
export interface Report {
  text: string
  citations: { title: string; url: string; start: number; end: number }[]
}

export interface Approach {
  key: string
  headline: string
  keywords: string[]
  reason: string
}

export interface PostKitState {
  report: Report | null
  approaches: Approach[]
  kit: PostKit | null
  posts: Post[]
}

export interface YouTubeResult {
  title: string
  channel: string
  views: number
  url: string
}

/** An API error carrying the server's message and its actionable hint. */
export class ApiError extends Error {
  hint: string
  status: number

  constructor(message: string, status: number, hint = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.hint = hint
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    let hint = ''
    try {
      const body = await response.json()
      const detail = body.detail
      if (typeof detail === 'string') {
        message = detail
      } else if (detail && typeof detail === 'object') {
        // The ingest endpoints return {message, hint} so the UI can show the
        // fix alongside the failure.
        message = detail.message ?? message
        hint = detail.hint ?? ''
      }
    } catch {
      /* Response had no JSON body; the status line is the best we have. */
    }
    throw new ApiError(message, response.status, hint)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string; version: string }>('/api/health'),
  system: () => request<SystemStatus>('/api/system'),
  fetchModels: () => request<void>('/api/system/models', { method: 'POST' }),

  listSources: () => request<Source[]>('/api/sources'),

  ingestYouTube: (url: string, cookiesFromBrowser?: string) =>
    request<Source>('/api/sources/youtube', {
      method: 'POST',
      body: JSON.stringify({ url, cookies_from_browser: cookiesFromBrowser || null }),
    }),

  uploadSource: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Source>('/api/sources/upload', { method: 'POST', body: form })
  },

  listJobs: (limit = 25) => request<Job[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),

  createJob: (sourceId: string, settings: JobSettingsOverrides = {}) =>
    request<Job>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ source_id: sourceId, settings }),
    }),

  jobCampaign: (id: string) => request<JobCampaign | null>(`/api/jobs/${id}/campaign`),

  listCampaigns: () => request<Campaign[]>('/api/campaigns'),
  getCampaign: (id: string) => request<Campaign>(`/api/campaigns/${id}`),

  readCampaign: (link: string) =>
    request<Campaign>('/api/campaigns', { method: 'POST', body: JSON.stringify({ link }) }),

  putCampaignRules: (
    id: string,
    details: { rules: string; brand: string; required_tags: string },
  ) =>
    request<Campaign>(`/api/campaigns/${id}/rules`, {
      method: 'PUT',
      body: JSON.stringify(details),
    }),

  campaignReading: (id: string) =>
    request<{ lines: string[] }>(`/api/campaigns/${id}/reading`, { method: 'POST' }),

  getPostKit: (clipId: string) => request<PostKitState>(`/api/clips/${clipId}/post-kit`),

  writePostKit: (clipId: string, approach: string, keywords: string) =>
    request<PostKitState>(`/api/clips/${clipId}/post-kit`, {
      method: 'POST',
      body: JSON.stringify({ approach, keywords }),
    }),

  researchClip: (clipId: string, topic: string) =>
    request<PostKitState>(`/api/clips/${clipId}/research`, {
      method: 'POST',
      body: JSON.stringify({ topic }),
    }),

  suggestTopic: (clipId: string) =>
    request<{ topic: string }>(`/api/clips/${clipId}/topic`, { method: 'POST' }),

  researchYouTube: (topic: string) =>
    request<YouTubeResult[]>('/api/research/youtube', {
      method: 'POST',
      body: JSON.stringify({ topic }),
    }),

  revealClip: (clipId: string) =>
    request<void>(`/api/clips/${clipId}/reveal`, { method: 'POST' }),

  markPosted: (
    clipId: string,
    platform: string,
    post: { url: string; approach: string; title: string; caption: string },
  ) =>
    request<Post>(`/api/clips/${clipId}/posts/${platform}`, {
      method: 'PUT',
      body: JSON.stringify(post),
    }),

  listPosts: () => request<PostRow[]>('/api/posts'),

  updatePost: (
    id: string,
    results: { views_48h: number | null; views_7d: number | null; submitted: boolean },
  ) => request<Post>(`/api/posts/${id}`, { method: 'PATCH', body: JSON.stringify(results) }),

  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryJob: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),

  listClips: (jobId: string) => request<Clip[]>(`/api/jobs/${jobId}/clips`),
  getClip: (clipId: string) => request<Clip>(`/api/clips/${clipId}`),

  getCropPath: (clipId: string) => request<CropPath>(`/api/clips/${clipId}/crop-path`),
  getClipWords: (clipId: string) => request<Word[]>(`/api/clips/${clipId}/words`),

  patchClip: (
    clipId: string,
    patch: { start_s?: number; end_s?: number; title?: string; status?: ClipStatus },
  ) => request<Clip>(`/api/clips/${clipId}`, { method: 'PATCH', body: JSON.stringify(patch) }),

  patchCaptions: (
    clipId: string,
    patch: { words?: Word[]; caption_style?: string; ratio?: string },
  ) =>
    request<Clip>(`/api/clips/${clipId}/captions`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  exportClip: (clipId: string, ratio: string, style: string, writeSrt = false) =>
    request<ExportRecord>(`/api/clips/${clipId}/export`, {
      method: 'POST',
      body: JSON.stringify({ ratio, style, write_srt: writeSrt }),
    }),

  captionStyles: () => request<CaptionStyle[]>('/api/caption-styles'),
  providerStatus: () => request<ProviderStatus[]>('/api/providers/status'),

  getSettings: () => request<Settings>('/api/settings'),
  putSettings: (patch: Partial<Settings>) =>
    request<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(patch) }),

  putSecret: (key: string, value: string) =>
    request<void>('/api/settings/secrets', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),

  deleteSecret: (key: string) =>
    request<void>(`/api/settings/secrets/${key}`, { method: 'DELETE' }),

  mediaUrl: (jobId: string) => `/api/jobs/${jobId}/media`,
}

/** Format seconds as m:ss, or h:mm:ss past an hour. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const s = total % 60
  const m = Math.floor(total / 60) % 60
  const h = Math.floor(total / 3600)
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

/** Format seconds as timecode with centiseconds, for trim handles. */
export function formatTimecode(seconds: number): string {
  const total = Math.max(0, seconds)
  const cs = Math.floor((total % 1) * 100)
  const s = Math.floor(total) % 60
  const m = Math.floor(total / 60)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(m)}:${pad(s)}.${pad(cs)}`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}
