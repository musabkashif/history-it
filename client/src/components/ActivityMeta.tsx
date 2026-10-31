import type { ReactNode } from 'react'
import { activityPhrase } from '../lib/formatTime'

export function MetadataLine({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400 ${className}`}>{children}</div>
}

export function ActivityMeta({
  createdAt,
  updatedAt,
  activityAt,
  activityLabel = 'Updated',
  now,
  className = '',
}: {
  createdAt?: string | null
  updatedAt?: string | null
  activityAt?: string | null
  activityLabel?: 'Updated' | 'Last activity' | 'Last status change'
  now?: number
  className?: string
}) {
  const created = activityPhrase('Created', createdAt, now)
  const activity = activityPhrase(activityLabel, activityAt ?? updatedAt, now)
  if (!created && !activity) return null
  return (
    <MetadataLine className={className}>
      {created && <span title={created.title}>{created.text}</span>}
      {created && activity && <span aria-hidden="true" className="text-slate-600">·</span>}
      {activity && <span title={activity.title}>{activity.text}</span>}
    </MetadataLine>
  )
}
