import { useEffect, useState } from 'react'

import {
  api,
  type Approach,
  type Clip,
  type Post,
  type PostKit as Kit,
  type Report,
  type YouTubeResult,
} from '../api'

const APPROACHES = [
  { key: 'search', label: 'Search', hint: 'For people looking up this topic' },
  { key: 'curiosity', label: 'Curiosity', hint: 'Opens a question for the scrolling feed' },
  { key: 'opinion', label: 'Strong opinion', hint: 'Invites comments, may split viewers' },
]

const PLATFORMS = [
  {
    key: 'tiktok',
    label: 'TikTok',
    upload: 'https://www.tiktok.com/tiktokstudio/upload',
    uploadLabel: 'Open TikTok upload',
    captionLabel: 'Caption',
    hasTitle: false,
    steps: [
      'Video uploaded',
      'Caption pasted, with the disclosure first',
      'Content disclosure on, with Branded content selected',
      'Audience set to everyone',
    ],
  },
  {
    key: 'instagram',
    label: 'Instagram',
    upload: 'https://www.instagram.com/',
    uploadLabel: 'Open Instagram',
    captionLabel: 'Caption',
    hasTitle: false,
    steps: [
      'Reel uploaded',
      'Caption pasted, with the disclosure first',
      'Paid partnership label added',
    ],
  },
  {
    key: 'youtube',
    label: 'YouTube',
    upload: 'https://studio.youtube.com/',
    uploadLabel: 'Open YouTube Studio',
    captionLabel: 'Description',
    hasTitle: true,
    steps: [
      'Short uploaded',
      'Title and description pasted',
      'Audience set to not made for kids',
      'Branded content turned on, under Show more',
      'Altered content set to No',
      'Language and category set',
      'Visibility set to public',
    ],
  },
]

/**
 * Everything needed to post one clip by hand.
 *
 * OpenAI's report goes to Qwen, which suggests an approach per clip, and the
 * user makes the final choice. YouTube results are shown only: YouTube's terms
 * forbid modifying them or deriving new data from them.
 */
export function PostKit({ clip, onError }: { clip: Clip; onError: (error: Error) => void }) {
  const [topic, setTopic] = useState('')
  const [approach, setApproach] = useState('search')
  const [keywords, setKeywords] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [approaches, setApproaches] = useState<Approach[]>([])
  const [kit, setKit] = useState<Kit | null>(null)
  const [posts, setPosts] = useState<Post[]>([])
  const [writing, setWriting] = useState(false)

  useEffect(() => {
    api
      .getPostKit(clip.id)
      .then((state) => {
        setReport(state.report)
        setApproaches(state.approaches)
        setKit(state.kit)
        setPosts(state.posts)
        if (state.kit) {
          setApproach(state.kit.approach)
          setKeywords(state.kit.keywords)
        } else if (state.approaches.length > 0) {
          setApproach(state.approaches[0].key)
          setKeywords(state.approaches[0].keywords.join(', '))
        }
      })
      .catch(onError)
    api
      .suggestTopic(clip.id)
      .then((suggested) => setTopic(suggested.topic))
      .catch(() => undefined)
  }, [clip.id, onError])

  const pick = (key: string) => {
    setApproach(key)
    const suggestion = approaches.find((item) => item.key === key)
    if (suggestion) setKeywords(suggestion.keywords.join(', '))
  }

  const write = async () => {
    setWriting(true)
    try {
      setKit((await api.writePostKit(clip.id, approach, keywords)).kit)
    } catch (err) {
      onError(err as Error)
    } finally {
      setWriting(false)
    }
  }

  return (
    <section aria-label="Post kit" className="space-y-8">
      <p className="eyebrow border-b border-ink-800 pb-2">Post kit</p>

      <Research
        clipId={clip.id}
        topic={topic}
        report={report}
        onTopic={setTopic}
        onResearched={(nextReport, nextApproaches) => {
          setReport(nextReport)
          setApproaches(nextApproaches)
          const suggestion = nextApproaches.find((item) => item.key === approach)
          if (suggestion) setKeywords(suggestion.keywords.join(', '))
        }}
        onError={onError}
      />

      <div>
        <p className="eyebrow">Choose an approach</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          {APPROACHES.map((item) => {
            const suggestion = approaches.find((option) => option.key === item.key)
            return (
            <button
              key={item.key}
              type="button"
              aria-pressed={item.key === approach}
              onClick={() => pick(item.key)}
              className={[
                'border p-3 text-left transition-colors duration-200',
                item.key === approach
                  ? 'border-sodium-500 bg-ink-850'
                  : 'border-ink-700 hover:border-ink-600',
              ].join(' ')}
            >
              <span className="block text-sm font-medium text-ink-100">{item.label}</span>
              {suggestion ? (
                <>
                  <span className="mt-1 block text-sm leading-snug text-ink-200">
                    {suggestion.headline}
                  </span>
                  <span className="mt-2 block text-xs leading-snug text-ink-400">
                    {suggestion.keywords.join(', ')}
                  </span>
                  <span className="mt-2 block text-xs leading-snug text-ink-300">
                    {suggestion.reason}
                  </span>
                </>
              ) : (
                <span className="mt-1 block text-xs leading-snug text-ink-400">{item.hint}</span>
              )}
            </button>
            )
          })}
        </div>

        <label className="mt-4 block">
          <span className="eyebrow">Your keywords, separated by commas</span>
          <input
            className="field mt-1 text-sm"
            value={keywords}
            onChange={(e) => setKeywords(e.target.value)}
            placeholder="learn English, speaking practice"
          />
        </label>

        <button type="button" onClick={write} disabled={writing} className="btn btn-primary mt-4">
          {writing ? 'Writing…' : kit ? 'Write again' : 'Write title and caption'}
        </button>
      </div>

      {kit && (
        <Publish
          key={`${kit.title}${kit.caption}`}
          clip={clip}
          kit={kit}
          posts={posts}
          onPosted={(post) =>
            setPosts((current) => [
              ...current.filter((item) => item.platform !== post.platform),
              post,
            ])
          }
          onError={onError}
        />
      )}
    </section>
  )
}

function Research({
  clipId,
  topic,
  report,
  onTopic,
  onResearched,
  onError,
}: {
  clipId: string
  topic: string
  report: Report | null
  onTopic: (topic: string) => void
  onResearched: (report: Report | null, approaches: Approach[]) => void
  onError: (error: Error) => void
}) {
  const [busy, setBusy] = useState<'report' | 'youtube' | null>(null)
  const [videos, setVideos] = useState<YouTubeResult[] | null>(null)

  const runReport = async () => {
    setBusy('report')
    try {
      const state = await api.researchClip(clipId, topic.trim())
      onResearched(state.report, state.approaches)
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(null)
    }
  }

  const runYouTube = async () => {
    setBusy('youtube')
    try {
      setVideos(await api.researchYouTube(topic.trim()))
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div>
      <label className="block">
        <span className="eyebrow">Research topic. Only this leaves your PC</span>
        <input
          className="field mt-1 text-sm"
          value={topic}
          onChange={(e) => onTopic(e.target.value)}
          placeholder="Finding a topic…"
        />
      </label>
      <div className="mt-3 flex flex-wrap gap-3">
        <button
          type="button"
          className="btn btn-ghost"
          disabled={busy !== null || topic.trim().length < 2}
          onClick={runReport}
        >
          {busy === 'report' ? 'Researching…' : 'Research with OpenAI'}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          disabled={busy !== null || topic.trim().length < 2}
          onClick={runYouTube}
        >
          {busy === 'youtube' ? 'Searching…' : 'Search YouTube'}
        </button>
      </div>

      {report && (
        <div className="mt-5 border-l-2 border-ink-700 pl-4">
          <p className="eyebrow">SEO report from OpenAI</p>
          <p className="mt-3 max-h-96 overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-ink-200">
            <CitedText report={report} />
          </p>
        </div>
      )}

      {videos && (
        <div className="mt-5 border-l-2 border-ink-700 pl-4">
          <p className="eyebrow">From YouTube. For you to read</p>
          {videos.length === 0 && <p className="mt-3 text-sm text-ink-400">No results.</p>}
          <ul className="mt-3 space-y-2">
            {videos.map((video) => (
              <li key={video.url} className="text-sm">
                <a
                  href={video.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-ink-200 underline underline-offset-4 hover:text-ink-100"
                >
                  {video.title}
                </a>
                <span className="numeric block text-xs text-ink-400">
                  {video.channel} · {video.views.toLocaleString()} views
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** The report with clickable source links inline, as OpenAI requires. */
function CitedText({ report }: { report: Report }) {
  const citations = [...report.citations].sort((a, b) => a.start - b.start)
  let position = 0
  const parts = citations.map((citation) => {
    const text = report.text.slice(position, citation.start)
    position = Math.max(position, citation.end)
    return (
      <span key={`${citation.url}${citation.start}`}>
        {text}
        <a
          href={citation.url}
          target="_blank"
          rel="noreferrer"
          className="text-sodium-500 underline underline-offset-2"
        >
          [{citation.title}]
        </a>
      </span>
    )
  })

  return (
    <>
      {parts}
      {report.text.slice(position)}
    </>
  )
}

function Publish({
  clip,
  kit,
  posts,
  onPosted,
  onError,
}: {
  clip: Clip
  kit: Kit
  posts: Post[]
  onPosted: (post: Post) => void
  onError: (error: Error) => void
}) {
  const [platformKey, setPlatformKey] = useState('tiktok')
  const [title, setTitle] = useState(kit.title)
  const [caption, setCaption] = useState(kit.caption)
  const [checks, setChecks] = useState<Record<string, boolean>>({})
  const [links, setLinks] = useState<Record<string, string>>({})
  const [copied, setCopied] = useState(false)
  const [saving, setSaving] = useState(false)

  const platform = PLATFORMS.find((item) => item.key === platformKey) ?? PLATFORMS[0]
  const posted = posts.find((post) => post.platform === platform.key)
  const link = links[platform.key] ?? posted?.url ?? ''
  const allChecked = platform.steps.every((_, index) => checks[`${platform.key}${index}`])
  const ready = allChecked && link.startsWith('https://') && !saving

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(caption)
      setCopied(true)
    } catch (err) {
      onError(err as Error)
    }
  }

  const reveal = () => api.revealClip(clip.id).catch(onError)

  const markPosted = async () => {
    setSaving(true)
    try {
      onPosted(
        await api.markPosted(clip.id, platform.key, {
          url: link,
          approach: kit.approach,
          title,
          caption,
        }),
      )
    } catch (err) {
      onError(err as Error)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      {kit.flags.length > 0 && (
        <ul className="mb-5 space-y-1 border-l-2 border-sodium-600 pl-4 text-sm text-ink-200">
          {kit.flags.map((flag) => (
            <li key={flag}>{flag}</li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap gap-2" role="group" aria-label="Platform">
        {PLATFORMS.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={item.key === platform.key}
            onClick={() => {
              setPlatformKey(item.key)
              setCopied(false)
            }}
            className={[
              'btn',
              item.key === platform.key ? 'border-sodium-500 text-ink-100' : 'btn-ghost',
            ].join(' ')}
          >
            {item.label}
            {posts.some((post) => post.platform === item.key) && ' · posted'}
          </button>
        ))}
      </div>

      {platform.hasTitle && (
        <label className="mt-5 block">
          <span className="eyebrow">Title</span>
          <input
            className="field mt-1 text-sm"
            value={title}
            maxLength={100}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
      )}

      <label className="mt-5 block">
        <span className="eyebrow">{platform.captionLabel}</span>
        <textarea
          className="mt-2 h-36 w-full resize-y border border-ink-700 bg-ink-850 p-3 text-sm leading-relaxed text-ink-100 focus:border-sodium-500 focus:outline-none"
          value={caption}
          onChange={(e) => {
            setCaption(e.target.value)
            setCopied(false)
          }}
        />
      </label>

      {platform.hasTitle && kit.category && (
        <p className="mt-2 text-xs text-ink-400">
          Suggested category: {kit.category} · Language: {kit.language}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-3">
        <button type="button" onClick={copy} className="btn btn-primary">
          {copied ? 'Copied' : `Copy ${platform.captionLabel.toLowerCase()}`}
        </button>
        <button type="button" onClick={reveal} className="btn btn-ghost">
          Show video file
        </button>
        <a href={platform.upload} target="_blank" rel="noreferrer" className="btn btn-ghost">
          {platform.uploadLabel}
        </a>
      </div>

      <p className="eyebrow mt-6">Before you post</p>
      <ul className="mt-2">
        {platform.steps.map((step, index) => {
          const key = `${platform.key}${index}`
          return (
            <li key={key}>
              <label className="flex items-start gap-3 py-1.5 text-sm text-ink-200">
                <input
                  type="checkbox"
                  checked={Boolean(checks[key])}
                  onChange={(e) => setChecks({ ...checks, [key]: e.target.checked })}
                  className="mt-0.5 size-4 accent-sodium-500"
                />
                {step}
              </label>
            </li>
          )
        })}
      </ul>

      <label className="mt-4 block">
        <span className="eyebrow">Link to your post</span>
        <input
          className="field mt-1 text-sm"
          value={link}
          onChange={(e) => setLinks({ ...links, [platform.key]: e.target.value })}
          placeholder="https://…"
          autoComplete="off"
        />
      </label>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" onClick={markPosted} disabled={!ready} className="btn btn-primary">
          Mark as posted
        </button>
        <span className="text-xs text-ink-400">
          {posted ? 'Posted, and saved to your log' : 'Tick every step and paste the link first'}
        </span>
      </div>
    </div>
  )
}
