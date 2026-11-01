import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, api, type Campaign, type CampaignPayout } from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { StandingRules } from '../components/StandingRules'

const PLATFORM_NAMES: Record<string, string> = {
  instagram: 'Instagram',
  tiktok: 'TikTok',
  youtube: 'YouTube',
}

/**
 * Paste a campaign link, read its rules, and clip a video for it.
 *
 * Only the pasted public page is read. Joining and getting reference files stay
 * with the user, so hidden rules are pasted in here by hand.
 */
export function CampaignPage() {
  const { campaignId } = useParams()
  const navigate = useNavigate()
  const [link, setLink] = useState('')
  const [reading, setReading] = useState(false)
  const [campaign, setCampaign] = useState<Campaign | null>(null)
  const [saved, setSaved] = useState<Campaign[]>([])
  const [error, setError] = useState<ApiError | Error | null>(null)

  useEffect(() => {
    api.listCampaigns().then(setSaved).catch(() => undefined)
  }, [campaign?.id])

  useEffect(() => {
    if (!campaignId) {
      setCampaign(null)
      return
    }
    api.getCampaign(campaignId).then(setCampaign).catch((err) => setError(err as Error))
  }, [campaignId])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!link.trim()) return
    setReading(true)
    setError(null)
    try {
      const loaded = await api.readCampaign(link.trim())
      setCampaign(loaded)
      setLink('')
      navigate(`/campaigns/${loaded.id}`)
    } catch (err) {
      setError(err as Error)
    } finally {
      setReading(false)
    }
  }

  return (
    <div className="pt-14">
      <div className="rise max-w-3xl">
        <p className="eyebrow">You join · The tool reads · You decide</p>
        <h1 className="mt-5 font-display text-[clamp(2.25rem,5vw,4rem)] leading-[0.95] text-ink-100">
          Campaign <span className="italic text-sodium-500">rules</span> first.
        </h1>
      </div>

      <form onSubmit={submit} className="rise mt-12 max-w-3xl">
        <label htmlFor="campaign-link" className="eyebrow">
          Paste a Content Rewards link
        </label>
        <div className="mt-3 flex items-end gap-4">
          <input
            id="campaign-link"
            className="field text-lg"
            placeholder="https://contentrewards.com/c/campaigns/…"
            value={link}
            onChange={(e) => setLink(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            disabled={reading}
          />
          <button
            type="submit"
            className="btn btn-primary shrink-0"
            disabled={reading || !link.trim()}
          >
            {reading ? 'Reading…' : 'Read campaign'}
          </button>
        </div>
      </form>

      {error && (
        <div className="mt-8 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {campaign ? (
        <CampaignDetail
          key={campaign.id}
          campaign={campaign}
          onChange={setCampaign}
          onError={setError}
        />
      ) : (
        <SavedCampaigns campaigns={saved} />
      )}
    </div>
  )
}

function SavedCampaigns({ campaigns }: { campaigns: Campaign[] }) {
  if (campaigns.length === 0) return null

  return (
    <section className="mt-20 max-w-3xl">
      <h2 className="eyebrow border-b border-ink-800 pb-3">Saved</h2>
      <ul>
        {campaigns.map((campaign) => (
          <li key={campaign.id}>
            <Link
              to={`/campaigns/${campaign.id}`}
              className="flex items-baseline justify-between gap-4 border-b border-ink-850 py-4 text-ink-200 transition-colors hover:text-ink-100"
            >
              <span className="truncate">{campaign.name}</span>
              <span className="text-xs text-ink-500">{campaign.host}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

function CampaignDetail({
  campaign,
  onChange,
  onError,
}: {
  campaign: Campaign
  onChange: (campaign: Campaign) => void
  onError: (error: Error) => void
}) {
  return (
    <div className="rise mt-14 border-t border-ink-800 pt-10">
      <p className="eyebrow">
        {campaign.host} · {campaign.status} · launched {campaign.launched}
      </p>
      <h2 className="mt-3 max-w-4xl font-display text-[clamp(1.75rem,3.5vw,2.75rem)] leading-tight text-ink-100">
        {campaign.name}
      </h2>
      <p className="mt-4 max-w-prose text-sm leading-relaxed text-ink-300">
        {campaign.description}
      </p>

      {campaign.blocked_topics.length > 0 && (
        <p className="mt-6 max-w-prose border-l-2 border-signal-bad pl-4 text-sm font-medium text-ink-100">
          This campaign mentions {campaign.blocked_topics.join(', ')}. Gambling and crypto are
          excluded, so it can't be clipped.
        </p>
      )}

      <div className="mt-10 grid gap-x-16 gap-y-12 lg:grid-cols-[1.2fr_1fr]">
        <div className="space-y-12">
          <RulesEditor campaign={campaign} onChange={onChange} onError={onError} />
          <StandingRules rules={campaign.standing_rules} />
        </div>
        <div className="space-y-12">
          <Numbers campaign={campaign} />
          <Reading key={campaign.rules} campaign={campaign} />
          <StartClipping campaign={campaign} onError={onError} />
        </div>
      </div>
    </div>
  )
}

function RulesEditor({
  campaign,
  onChange,
  onError,
}: {
  campaign: Campaign
  onChange: (campaign: Campaign) => void
  onError: (error: Error) => void
}) {
  const [text, setText] = useState(campaign.rules)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      onChange(await api.putCampaignRules(campaign.id, text))
    } catch (err) {
      onError(err as Error)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <p className="eyebrow">Rules for this campaign</p>
      <p className="mt-3 text-sm text-ink-300">
        Hidden until you join: {campaign.rule_count} rules, {campaign.requirement_count} creator
        requirements, {campaign.reference_count} reference files.
      </p>
      {!campaign.rules_complete && (
        <p className="mt-3 max-w-prose border-l-2 border-sodium-600 pl-4 text-sm text-ink-200">
          Not complete. Join the campaign on Content Rewards, then paste its rules and file links
          here.
        </p>
      )}
      <textarea
        aria-label="Rules for this campaign"
        className="mt-4 h-56 w-full resize-y border border-ink-700 bg-ink-850 p-3 text-sm leading-relaxed text-ink-100 focus:border-sodium-500 focus:outline-none"
        placeholder="Paste the rules from the joined campaign"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <button
        type="button"
        onClick={save}
        className="btn btn-ghost mt-3"
        disabled={saving || text.trim() === campaign.rules}
      >
        {saving ? 'Saving…' : 'Save rules'}
      </button>
    </section>
  )
}

function Numbers({ campaign }: { campaign: Campaign }) {
  const left = campaign.budget_cents - campaign.spent_cents
  const average = campaign.approved_clips
    ? Math.floor(campaign.total_views / campaign.approved_clips)
    : 0

  return (
    <section>
      <p className="eyebrow">Payout</p>
      <ul className="mt-3">
        {campaign.payouts.map((payout) => (
          <PayoutRow key={payout.platform} payout={payout} />
        ))}
      </ul>

      <p className="eyebrow mt-8">Budget</p>
      <p className="numeric mt-3 text-sm text-ink-200">
        {dollars(left, 0)} left of {dollars(campaign.budget_cents, 0)}
      </p>
      <p className="numeric mt-1 text-xs text-ink-500">
        {campaign.creators.toLocaleString()} creators ·{' '}
        {campaign.approved_clips.toLocaleString()} clips ·{' '}
        {campaign.total_views.toLocaleString()} views
        {average > 0 && ` · ${average.toLocaleString()} views per clip`}
      </p>
      {campaign.needs_application && (
        <p className="mt-3 text-sm text-ink-300">You must apply before you can join.</p>
      )}
    </section>
  )
}

function PayoutRow({ payout }: { payout: CampaignPayout }) {
  return (
    <li className="border-b border-ink-850 py-2.5">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm text-ink-200">
          {PLATFORM_NAMES[payout.platform] ?? payout.platform}
        </span>
        <span className="numeric text-sm text-ink-200">
          {dollars(payout.rate_cents, 2)} per 1,000 views
        </span>
      </div>
      <p className="numeric mt-1 text-xs text-ink-500">
        Minimum {dollars(payout.min_cents, 2)} ({payout.min_views.toLocaleString()} views) ·
        Maximum {dollars(payout.max_cents, 2)} ({payout.max_views.toLocaleString()} views)
      </p>
    </li>
  )
}

function Reading({ campaign }: { campaign: Campaign }) {
  const [lines, setLines] = useState<string[] | null>(null)
  const [failed, setFailed] = useState('')

  useEffect(() => {
    let stale = false
    api
      .campaignReading(campaign.id)
      .then((reading) => !stale && setLines(reading.lines))
      .catch((err: Error) => !stale && setFailed(err.message))
    return () => {
      stale = true
    }
  }, [campaign.id])

  return (
    <section>
      <p className="eyebrow">AI reading · The rules always win</p>
      {failed && <p className="mt-3 text-sm text-ink-500">No reading: {failed}</p>}
      {!failed && !lines && <p className="mt-3 text-sm text-ink-500">Reading…</p>}
      {lines && (
        <ul className="mt-3 space-y-1.5">
          {lines.map((line) => (
            <li key={line} className="max-w-prose text-sm leading-relaxed text-ink-300">
              {line}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function StartClipping({
  campaign,
  onError,
}: {
  campaign: Campaign
  onError: (error: Error) => void
}) {
  const navigate = useNavigate()
  const [file, setFile] = useState<File | null>(null)
  const [url, setUrl] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const blocked = campaign.blocked_topics.length > 0
  const hasVideo = Boolean(file || url.trim())

  const start = async () => {
    setBusy(true)
    try {
      const source = file ? await api.uploadSource(file) : await api.ingestYouTube(url.trim())
      const job = await api.createJob(source.id, {
        campaign_id: campaign.id,
        rules_confirmed: confirmed,
      })
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      onError(err as Error)
      setBusy(false)
    }
  }

  return (
    <section>
      <p className="eyebrow">Give a video</p>
      <button
        type="button"
        onClick={() => fileInput.current?.click()}
        className="btn btn-ghost mt-3 w-full"
        disabled={blocked || busy}
      >
        {file ? file.name : 'Choose a video file'}
      </button>
      <input
        ref={fileInput}
        type="file"
        className="hidden"
        accept="video/*,audio/*"
        onChange={(e) => {
          setFile(e.target.files?.[0] ?? null)
          setUrl('')
        }}
      />
      <input
        aria-label="YouTube link"
        className="field mt-4 text-sm"
        placeholder="Or a YouTube link"
        value={url}
        onChange={(e) => {
          setUrl(e.target.value)
          setFile(null)
        }}
        autoComplete="off"
        spellCheck={false}
        disabled={blocked || busy}
      />

      <label className="mt-6 flex items-start gap-3 text-sm text-ink-200">
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)}
          disabled={blocked || busy}
          className="mt-0.5 size-4 accent-sodium-500"
        />
        {campaign.rules_complete
          ? 'I have read the rules'
          : 'Clip without the hidden rules. I have read what is shown here'}
      </label>

      <button
        type="button"
        onClick={start}
        className="btn btn-primary mt-5"
        disabled={blocked || busy || !hasVideo || !confirmed}
      >
        {busy ? 'Starting…' : 'Clip this video'}
      </button>
    </section>
  )
}

function dollars(cents: number, decimals: number): string {
  return `$${(cents / 100).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`
}
