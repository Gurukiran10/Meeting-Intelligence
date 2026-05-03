import React from 'react'
import { useQuery } from 'react-query'
import { api } from '../lib/api'
import { TrendingUp, Users, AlertCircle, CheckCircle2, Clock, BarChart2, Lightbulb, VolumeX, RefreshCw, Ban, MessageSquareWarning } from 'lucide-react'

const statusColors: Record<string, string> = {
  completed: 'bg-green-100 text-green-700',
  scheduled: 'bg-gray-100 text-gray-700',
  transcribing: 'bg-blue-100 text-blue-700',
  failed: 'bg-red-100 text-red-700',
}

function MeetingTimeBreakdown({ breakdown }: { breakdown: any }) {
  const total = breakdown?.total_minutes || 1
  const bars = [
    { label: 'Strategic', key: 'strategic_minutes', color: 'bg-violet-500' },
    { label: 'Tactical', key: 'tactical_minutes', color: 'bg-blue-500' },
    { label: 'Status', key: 'status_minutes', color: 'bg-amber-400' },
  ]
  return (
    <div className="space-y-3">
      {bars.map(({ label, key, color }) => {
        const mins = breakdown?.[key] ?? 0
        const pct = Math.round((mins / total) * 100)
        return (
          <div key={key}>
            <div className="flex justify-between text-sm text-gray-600 mb-1">
              <span>{label}</span>
              <span>{Math.round(mins)} min ({pct}%)</span>
            </div>
            <div className="h-2 rounded-full bg-gray-100 overflow-hidden">
              <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
            </div>
          </div>
        )
      })}
      <p className="text-xs text-gray-400 pt-1">Total: {Math.round(total)} min</p>
    </div>
  )
}

function WeeklyLoadChart({ trends }: { trends: any[] }) {
  if (!trends?.length) return <p className="text-sm text-gray-400">No data yet.</p>
  const maxHours = Math.max(...trends.map((t: any) => t.hours || 0), 0.1)
  return (
    <div className="flex items-end gap-2 h-24">
      {trends.map((point: any, i: number) => {
        const height = Math.max(8, ((point.hours || 0) / maxHours) * 96)
        return (
          <div key={i} className="flex flex-col items-center gap-1 flex-1">
            <span className="text-xs text-gray-400">{point.hours}h</span>
            <div
              className="w-full rounded-t bg-violet-400"
              style={{ height }}
              title={`${point.week}: ${point.meetings} meetings, ${point.hours}h`}
            />
            <span className="text-[10px] text-gray-400 truncate w-full text-center">
              {point.week?.replace(/^\d{4}-/, '') ?? ''}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function FollowThroughTable({ people }: { people: any[] }) {
  if (!people?.length) return <p className="text-sm text-gray-400">No data yet.</p>
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-gray-500 border-b border-gray-100">
          <th className="pb-2 font-medium">Person</th>
          <th className="pb-2 font-medium text-right">Completion</th>
          <th className="pb-2 font-medium text-right">Open</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-gray-50">
        {people.map((p: any, i: number) => {
          const rate = p.completion_rate ?? 0
          const color = rate >= 80 ? 'text-green-600' : rate >= 50 ? 'text-amber-600' : 'text-red-600'
          return (
            <tr key={i} className="hover:bg-gray-50">
              <td className="py-2 text-gray-800">{p.user_name || p.user_id}</td>
              <td className={`py-2 text-right font-semibold ${color}`}>{rate}%</td>
              <td className="py-2 text-right text-gray-500">{p.open_items}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

const Analytics: React.FC = () => {
  const { data: dashboard } = useQuery('analytics-dashboard', async () => {
    const response = await api.get('/api/v1/analytics/dashboard')
    return response.data
  })

  const { data: efficiency } = useQuery('meeting-efficiency', async () => {
    const response = await api.get('/api/v1/analytics/meeting-efficiency')
    return response.data
  })

  const { data: intelligence } = useQuery('intelligence-report', async () => {
    const response = await api.get('/api/v1/analytics/intelligence-report')
    return response.data
  })

  const { data: patterns } = useQuery('patterns', async () => {
    const response = await api.get('/api/v1/patterns/')
    return response.data
  })

  const personal = intelligence?.personal_insights
  const team = intelligence?.team_insights
  const recommendations: string[] = intelligence?.recommendations ?? []

  const silentAttendees: any[] = patterns?.silent_attendees ?? []
  const chronicOverdue: any[] = patterns?.chronic_overdue ?? []
  const unresolvedTopics: any[] = patterns?.unresolved_topics ?? []
  const blockedItems: any[] = patterns?.blocked_items ?? []
  const totalPatterns: number = patterns?.summary?.total_patterns ?? 0

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Analytics</h1>
        <p className="mt-2 text-gray-600">Measure meeting efficiency and execution quality.</p>
      </div>

      {/* Top stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
          <p className="text-sm text-gray-600">Total Meetings</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{dashboard?.meeting_stats?.total_meetings ?? 0}</p>
        </div>
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
          <p className="text-sm text-gray-600">Total Hours</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{dashboard?.meeting_stats?.total_hours ?? 0}</p>
        </div>
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
          <p className="text-sm text-gray-600">Action Completion</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{dashboard?.action_item_stats?.completion_rate ?? 0}%</p>
        </div>
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4">
          <p className="text-sm text-gray-600">Decision Velocity</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{dashboard?.decision_velocity ?? 0}/hr</p>
        </div>
      </div>

      {/* Personal Insights */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <div className="flex items-center gap-2 mb-5">
          <BarChart2 size={18} className="text-violet-500" />
          <h2 className="text-lg font-semibold text-gray-900">Personal Insights</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Meeting time breakdown */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">Meeting Time by Type</p>
            <MeetingTimeBreakdown breakdown={personal?.meeting_time_breakdown} />
          </div>

          {/* Speaking time + action completion */}
          <div className="space-y-4">
            <div>
              <p className="text-sm font-medium text-gray-700 mb-2">Action Completion Rate</p>
              <div className="flex items-end gap-3">
                <span className="text-3xl font-bold text-gray-900">{personal?.action_completion_rate ?? 0}%</span>
                <CheckCircle2 size={20} className={personal?.action_completion_rate >= 70 ? 'text-green-500' : 'text-amber-500'} />
              </div>
              <div className="mt-2 h-2 rounded-full bg-gray-100 overflow-hidden">
                <div
                  className={`h-full rounded-full ${(personal?.action_completion_rate ?? 0) >= 70 ? 'bg-green-400' : 'bg-amber-400'}`}
                  style={{ width: `${personal?.action_completion_rate ?? 0}%` }}
                />
              </div>
            </div>
            <div>
              <p className="text-sm font-medium text-gray-700 mb-1">Speaking Time</p>
              <p className="text-2xl font-bold text-gray-900">{personal?.speaking_time?.total_minutes ?? 0} <span className="text-base font-normal text-gray-500">min total</span></p>
              <p className="text-sm text-gray-500">{personal?.speaking_time?.avg_minutes_per_meeting ?? 0} min avg / meeting</p>
            </div>
          </div>

          {/* Weekly load */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">Weekly Meeting Load (last 8 weeks)</p>
            <WeeklyLoadChart trends={personal?.meeting_load_trends ?? []} />
          </div>
        </div>
      </div>

      {/* Team Insights */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Follow-through rates */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Users size={18} className="text-blue-500" />
            <h2 className="text-lg font-semibold text-gray-900">Follow-Through by Person</h2>
          </div>
          <FollowThroughTable people={team?.follow_through_rates_by_person ?? []} />
        </div>

        {/* Low-value recurring meetings */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <div className="flex items-center gap-2 mb-4">
            <AlertCircle size={18} className="text-amber-500" />
            <h2 className="text-lg font-semibold text-gray-900">Low-Value Recurring Meetings</h2>
            <span className="ml-auto text-xs text-gray-400">No decisions captured across all occurrences</span>
          </div>
          {(team?.low_value_recurring_meetings ?? []).length === 0 ? (
            <p className="text-sm text-gray-400">None detected — great job making decisions!</p>
          ) : (
            <div className="space-y-3">
              {team.low_value_recurring_meetings.map((m: any, i: number) => (
                <div key={i} className="flex items-center justify-between rounded-lg border border-amber-100 bg-amber-50 px-3 py-2">
                  <div>
                    <p className="text-sm font-medium text-gray-800">{m.title}</p>
                    <p className="text-xs text-gray-500">{m.count} occurrences · avg {m.avg_duration_minutes} min</p>
                  </div>
                  <span className="text-xs text-amber-700 font-medium">Consider async</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Lightbulb size={18} className="text-violet-500" />
            <h2 className="text-lg font-semibold text-gray-900">Recommendations</h2>
          </div>
          <ul className="space-y-3">
            {recommendations.map((rec, i) => (
              <li key={i} className="flex items-start gap-3 rounded-lg border border-violet-100 bg-violet-50 px-4 py-3">
                <TrendingUp size={16} className="text-violet-500 mt-0.5 shrink-0" />
                <p className="text-sm text-gray-700">{rec}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Recurring Patterns */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <div className="flex items-center gap-2 mb-5">
          <RefreshCw size={18} className="text-rose-500" />
          <h2 className="text-lg font-semibold text-gray-900">Recurring Patterns</h2>
          {totalPatterns > 0 && (
            <span className="ml-auto bg-rose-100 text-rose-700 text-xs font-semibold px-2 py-0.5 rounded-full">
              {totalPatterns} detected
            </span>
          )}
        </div>

        {totalPatterns === 0 && (
          <p className="text-sm text-gray-400">No problematic patterns detected — things look healthy!</p>
        )}

        <div className="space-y-6">
          {/* Silent Attendees */}
          {silentAttendees.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <VolumeX size={15} className="text-amber-500" />
                <p className="text-sm font-semibold text-gray-700">Silent Attendees</p>
                <span className="text-xs text-gray-400">(invited but never speak)</span>
              </div>
              <div className="space-y-2">
                {silentAttendees.map((p: any, i: number) => (
                  <div key={i} className="flex items-start justify-between rounded-lg border border-amber-100 bg-amber-50 px-4 py-3 gap-3">
                    <div>
                      <p className="text-sm font-medium text-gray-800">{p.user_name}</p>
                      <p className="text-xs text-gray-500 mt-0.5">Silent in last {p.meetings_silent} meetings</p>
                      <p className="text-xs text-gray-400 mt-1">{p.recommendation}</p>
                    </div>
                    <span className="shrink-0 text-xs bg-amber-200 text-amber-800 px-2 py-0.5 rounded-full">{p.meetings_silent}× silent</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Chronic Overdue */}
          {chronicOverdue.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Clock size={15} className="text-red-500" />
                <p className="text-sm font-semibold text-gray-700">Chronic Overdue</p>
                <span className="text-xs text-gray-400">(3+ overdue action items)</span>
              </div>
              <div className="space-y-2">
                {chronicOverdue.map((p: any, i: number) => (
                  <div key={i} className={`rounded-lg border px-4 py-3 ${p.severity === 'error' ? 'border-red-200 bg-red-50' : 'border-orange-100 bg-orange-50'}`}>
                    <div className="flex items-center justify-between gap-3 mb-2">
                      <p className="text-sm font-medium text-gray-800">{p.user_name}</p>
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${p.severity === 'error' ? 'bg-red-200 text-red-800' : 'bg-orange-200 text-orange-800'}`}>
                        {p.overdue_count} overdue · avg {p.avg_days_overdue}d late
                      </span>
                    </div>
                    <div className="space-y-1">
                      {p.items.slice(0, 3).map((item: any, j: number) => (
                        <p key={j} className="text-xs text-gray-600">· {item.title} <span className="text-gray-400">({item.days_overdue}d overdue)</span></p>
                      ))}
                      {p.items.length > 3 && <p className="text-xs text-gray-400">+{p.items.length - 3} more</p>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Unresolved Recurring Topics */}
          {unresolvedTopics.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <MessageSquareWarning size={15} className="text-blue-500" />
                <p className="text-sm font-semibold text-gray-700">Unresolved Recurring Topics</p>
                <span className="text-xs text-gray-400">(discussed 3+ times, no decision)</span>
              </div>
              <div className="space-y-2">
                {unresolvedTopics.map((p: any, i: number) => (
                  <div key={i} className="flex items-start justify-between rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 gap-3">
                    <div>
                      <p className="text-sm font-medium text-gray-800">"{p.topic}"</p>
                      <p className="text-xs text-gray-400 mt-0.5">Last seen: {p.last_seen ? new Date(p.last_seen).toLocaleDateString() : '—'}</p>
                      <p className="text-xs text-gray-500 mt-1">{p.recommendation}</p>
                    </div>
                    <span className="shrink-0 text-xs bg-blue-200 text-blue-800 px-2 py-0.5 rounded-full">{p.occurrences}× raised</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Blocked Items */}
          {blockedItems.length > 0 && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Ban size={15} className="text-gray-500" />
                <p className="text-sm font-semibold text-gray-700">Long-Blocked Items</p>
                <span className="text-xs text-gray-400">(blocked for 7+ days)</span>
              </div>
              <div className="space-y-2">
                {blockedItems.map((p: any, i: number) => (
                  <div key={i} className={`rounded-lg border px-4 py-3 ${p.severity === 'error' ? 'border-red-200 bg-red-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-medium text-gray-800">{p.title}</p>
                        <p className="text-xs text-gray-500 mt-0.5">Owner: {p.owner_name} · from: {p.meeting_title}</p>
                      </div>
                      <span className={`shrink-0 text-xs font-semibold px-2 py-0.5 rounded-full ${p.severity === 'error' ? 'bg-red-200 text-red-800' : 'bg-gray-200 text-gray-700'}`}>
                        {p.days_blocked}d blocked
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Meeting Efficiency */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900">Meeting Efficiency</h2>
        <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div>
            <p className="text-gray-500">Avg Decisions Per Hour</p>
            <p className="font-semibold text-gray-900">{efficiency?.avg_decisions_per_hour ?? 0}</p>
          </div>
          <div>
            <p className="text-gray-500">Avg Action Items Per Hour</p>
            <p className="font-semibold text-gray-900">{efficiency?.avg_action_items_per_hour ?? 0}</p>
          </div>
          <div>
            <p className="text-gray-500">Execution Completion</p>
            <p className="font-semibold text-gray-900">{efficiency?.completion_ratio ?? 0}%</p>
          </div>
        </div>

        <div className="mt-6">
          <h3 className="text-sm font-semibold text-gray-900 mb-2">Sentiment Trend</h3>
          <div className="flex items-end gap-3 h-28">
            {(efficiency?.sentiment_trend || []).map((point: any, index: number) => {
              const normalized = Number(point?.score ?? 0)
              const height = Math.max(12, ((normalized + 1) / 2) * 100)
              return (
                <div key={index} className="flex flex-col items-center gap-2">
                  <div
                    className="bg-violet-500 rounded-t w-8"
                    style={{ height: `${height}%` }}
                    title={`${point?.label}: ${normalized}`}
                  />
                  <span className="text-xs text-gray-500">{point?.label}</span>
                </div>
              )
            })}
          </div>
        </div>

        <div className="mt-8 grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div>
            <h3 className="text-sm font-semibold text-gray-900 mb-3">Status Breakdown</h3>
            <div className="space-y-2">
              {Object.entries(efficiency?.status_breakdown || {}).length ? (
                Object.entries(efficiency?.status_breakdown || {}).map(([status, count]) => (
                  <div key={status} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2">
                    <span className="text-sm text-gray-700 capitalize">{status.replace('_', ' ')}</span>
                    <span className="text-sm font-semibold text-gray-900">{String(count)}</span>
                  </div>
                ))
              ) : (
                <div className="text-sm text-gray-500">No status data yet.</div>
              )}
            </div>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-gray-900 mb-3">Recent Meetings</h3>
            <div className="space-y-3">
              {(efficiency?.recent_meetings || []).length ? (
                efficiency.recent_meetings.map((meeting: any) => (
                  <div key={meeting.id} className="rounded-lg border border-gray-200 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="font-medium text-gray-900">{meeting.title}</p>
                        <p className="mt-1 text-xs text-gray-500">{new Date(meeting.scheduled_start).toLocaleString()}</p>
                      </div>
                      <span className={`px-2 py-1 rounded-full text-xs ${statusColors[meeting.status] || 'bg-gray-100 text-gray-700'}`}>
                        {meeting.status}
                      </span>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-xs text-gray-600">
                      <div>
                        <p>Duration</p>
                        <p className="font-semibold text-gray-900">{meeting.duration_minutes} min</p>
                      </div>
                      <div>
                        <p>Actions</p>
                        <p className="font-semibold text-gray-900">{meeting.action_items_count}</p>
                      </div>
                      <div>
                        <p>Decisions</p>
                        <p className="font-semibold text-gray-900">{meeting.decisions_count}</p>
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="text-sm text-gray-500">No meeting insights yet.</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default Analytics
