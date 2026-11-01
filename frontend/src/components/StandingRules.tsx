/** Rules that apply to every campaign, shown wherever campaign rules appear. */
export function StandingRules({ rules }: { rules: string[] }) {
  return (
    <div>
      <p className="eyebrow">Rules for every campaign</p>
      <ul className="mt-3 space-y-2">
        {rules.map((rule) => (
          <li key={rule} className="max-w-prose text-sm leading-relaxed text-ink-300">
            {rule}
          </li>
        ))}
      </ul>
      <a
        href="https://contentrewards.com/ftc-compliance"
        target="_blank"
        rel="noreferrer"
        className="mt-3 inline-block text-xs text-ink-500 underline underline-offset-4 hover:text-ink-300"
      >
        Full disclosure rules
      </a>
    </div>
  )
}
