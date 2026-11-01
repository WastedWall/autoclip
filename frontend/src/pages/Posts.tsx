import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, type PostRow } from '../api'
import { ErrorNote } from '../components/ErrorNote'

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'todo', label: 'To post' },
  { key: 'posted', label: 'Posted' },
]

const PLATFORM_NAMES: Record<string, string> = {
  instagram: 'Instagram',
  tiktok: 'TikTok',
  youtube: 'YouTube',
}

/** Every kept clip on every platform, with the results for the go or stop decision. */
export function Posts() {
  const [rows, setRows] = useState<PostRow[]>([])
  const [filter, setFilter] = useState('all')
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    api.listPosts().then(setRows).catch(setError)
  }, [])

  const update = async (row: PostRow, patch: Partial<NonNullable<PostRow['post']>>) => {
    if (!row.post) return
    const next = { ...row.post, ...patch }
    try {
      const saved = await api.updatePost(row.post.id, {
        views_48h: next.views_48h,
        views_7d: next.views_7d,
        submitted: next.submitted,
      })
      setRows((current) =>
        current.map((item) => (item.post?.id === saved.id ? { ...item, post: saved } : item)),
      )
    } catch (err) {
      setError(err as Error)
    }
  }

  const visible = rows.filter(
    (row) => filter === 'all' || (filter === 'posted') === Boolean(row.post),
  )

  return (
    <div className="pt-14">
      <div className="rise flex flex-wrap items-end justify-between gap-4">
        <h1 className="font-display text-[clamp(2.25rem,5vw,3.5rem)] leading-[0.95] text-ink-100">
          Your <span className="italic text-sodium-500">posts</span>
        </h1>
        <a href="/api/posts.csv" className="btn btn-ghost">
          Download log as CSV
        </a>
      </div>

      <div className="mt-8 flex flex-wrap gap-2" role="group" aria-label="Filter">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            aria-pressed={item.key === filter}
            onClick={() => setFilter(item.key)}
            className={[
              'btn',
              item.key === filter ? 'border-sodium-500 text-ink-100' : 'btn-ghost',
            ].join(' ')}
          >
            {item.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mt-6 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {rows.length === 0 ? (
        <p className="mt-10 text-sm text-ink-400">
          Nothing here yet. Keep a clip on the review page and it shows up here.
        </p>
      ) : (
        <div className="mt-5 overflow-x-auto border-t border-ink-800">
          <table className="w-full min-w-[860px] border-collapse text-sm">
            <thead>
              <tr className="text-left text-xs text-ink-400">
                <th scope="col" className="py-3 pr-3 font-semibold">Clip</th>
                <th scope="col" className="p-3 font-semibold">Platform</th>
                <th scope="col" className="p-3 font-semibold">Status</th>
                <th scope="col" className="p-3 font-semibold">Views after 48 hours</th>
                <th scope="col" className="p-3 font-semibold">Views after 7 days</th>
                <th scope="col" className="p-3 font-semibold">Submitted to campaign</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={`${row.clip_id}${row.platform}`} className="border-t border-ink-850">
                  <td className="max-w-[340px] py-3 pr-3">
                    <Link to={`/jobs/${row.job_id}/clips`} className="text-ink-100 hover:text-sodium-500">
                      {row.clip_title || 'Untitled'}
                    </Link>
                  </td>
                  <td className="p-3 text-ink-200">{PLATFORM_NAMES[row.platform]}</td>
                  <td className="p-3 text-ink-200">
                    {row.post ? (
                      <a
                        href={row.post.url}
                        target="_blank"
                        rel="noreferrer"
                        className="underline underline-offset-4 hover:text-ink-100"
                      >
                        Posted
                      </a>
                    ) : (
                      'To post'
                    )}
                  </td>
                  <td className="p-3">
                    <Views
                      label="Views after 48 hours"
                      value={row.post?.views_48h ?? null}
                      disabled={!row.post}
                      onSave={(views_48h) => update(row, { views_48h })}
                    />
                  </td>
                  <td className="p-3">
                    <Views
                      label="Views after 7 days"
                      value={row.post?.views_7d ?? null}
                      disabled={!row.post}
                      onSave={(views_7d) => update(row, { views_7d })}
                    />
                  </td>
                  <td className="p-3">
                    <input
                      type="checkbox"
                      aria-label="Submitted to campaign"
                      checked={row.post?.submitted ?? false}
                      disabled={!row.post}
                      onChange={(e) => update(row, { submitted: e.target.checked })}
                      className="size-4 accent-sodium-500"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/** Saves when the field loses focus, so typing a number makes one request. */
function Views({
  label,
  value,
  disabled,
  onSave,
}: {
  label: string
  value: number | null
  disabled: boolean
  onSave: (value: number | null) => void
}) {
  const [text, setText] = useState(value?.toString() ?? '')

  return (
    <input
      type="number"
      min={0}
      aria-label={label}
      value={text}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        const next = text === '' ? null : Number(text)
        if (next !== value) onSave(next)
      }}
      className="numeric w-24 border border-ink-700 bg-ink-850 px-2.5 py-2 text-sm text-ink-100 focus:border-sodium-500 focus:outline-none disabled:opacity-40"
    />
  )
}
