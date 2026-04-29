import React, { useEffect, useMemo, useState } from 'react'
import { useQuery } from 'react-query'
import { MessageSquareQuote, Plus, Search, Trash2, Users, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'

type Meeting = {
  id: string
  title: string
  description?: string | null
  platform: string
  status: string
  transcription_status?: string
  analysis_status?: string
  summary?: string | null
  scheduled_start: string
  attendee_count?: number
  attendee_ids?: string[]
  tags?: string[]
  agenda?: string[] | Record<string, unknown> | null
}

type UserOption = {
  id: string
  email: string
  username: string
  full_name: string
}

type MeetingForm = {
  title: string
  description: string
  meeting_type: string
  platform: 'manual' | 'zoom'
  scheduled_start: string
  scheduled_end: string
  agendaText: string
  tagsText: string
}

const emptyForm: MeetingForm = {
  title: '',
  description: '',
  meeting_type: 'internal',
  platform: 'manual',
  scheduled_start: '',
  scheduled_end: '',
  agendaText: '',
  tagsText: '',
}

const isLikelyEmail = (value: string) => /\S+@\S+\.\S+/.test(value)

const Meetings: React.FC = () => {
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [banner, setBanner] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  const [form, setForm] = useState<MeetingForm>(emptyForm)
  const [attendeeQuery, setAttendeeQuery] = useState('')
  const [selectedAttendees, setSelectedAttendees] = useState<string[]>([])

  const { data: meetings, isLoading, isError, error, refetch } = useQuery<Meeting[]>('meetings', async () => {
    const response = await api.get('/api/v1/meetings/', { timeout: 15000 })
    return response.data
  }, {
    retry: 1,
    refetchOnWindowFocus: false,
  })

  const { data: users } = useQuery<UserOption[]>('meeting-users', async () => {
    const response = await api.get('/api/v1/users/', { timeout: 15000 })
    return response.data
  }, {
    retry: 1,
    refetchOnWindowFocus: false,
  })

  const { data: allMentions } = useQuery<{ meeting_id: string }[]>('all-mentions', async () => {
    const response = await api.get('/api/v1/mentions/', { timeout: 15000 })
    return response.data
  }, {
    retry: 1,
    refetchOnWindowFocus: false,
  })

  const mentionCountByMeeting = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const m of allMentions || []) {
      if (m.meeting_id) counts[m.meeting_id] = (counts[m.meeting_id] || 0) + 1
    }
    return counts
  }, [allMentions])

  const attendeeSuggestions = useMemo(() => {
    const q = attendeeQuery.trim().toLowerCase()
    if (!q) return []

    return (users || [])
      .filter((user) => !selectedAttendees.includes(user.id))
      .filter((user) =>
        [user.full_name, user.email, user.username]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(q)),
      )
      .slice(0, 6)
  }, [attendeeQuery, selectedAttendees, users])

  const filteredMeetings = useMemo(() => {
    const list = meetings || []
    if (!search.trim()) return list
    const q = search.toLowerCase()
    return list.filter((meeting) =>
      [meeting.title, meeting.description, meeting.platform, meeting.status]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q)),
    )
  }, [meetings, search])

  const parsedAgenda = useMemo(
    () => form.agendaText.split('\n').map((item) => item.trim()).filter(Boolean),
    [form.agendaText],
  )

  const parsedTags = useMemo(
    () => form.tagsText.split(',').map((item) => item.trim()).filter(Boolean),
    [form.tagsText],
  )

  useEffect(() => {
    if (!banner) return
    const timeoutId = window.setTimeout(() => setBanner(null), 4000)
    return () => window.clearTimeout(timeoutId)
  }, [banner])

  const resetCreateForm = () => {
    setForm(emptyForm)
    setAttendeeQuery('')
    setSelectedAttendees([])
    setCreateError('')
  }

  const addAttendee = (value: string) => {
    const trimmed = value.trim()
    if (!trimmed) return

    setSelectedAttendees((prev) => (prev.includes(trimmed) ? prev : [...prev, trimmed]))
    setAttendeeQuery('')
  }

  const removeAttendee = (value: string) => {
    setSelectedAttendees((prev) => prev.filter((attendee) => attendee !== value))
  }

  const attendeeLabel = (value: string) => {
    const user = (users || []).find((candidate) => candidate.id === value)
    if (!user) return value
    return `${user.full_name} (${user.username || user.email})`
  }

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault()
    setCreateError('')

    const start = new Date(form.scheduled_start)
    const end = new Date(form.scheduled_end)

    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
      setCreateError('Please enter valid start and end date/time values.')
      return
    }

    if (end <= start) {
      setCreateError('End time must be after start time.')
      return
    }

    const pendingAttendee = attendeeQuery.trim()
    if (pendingAttendee) {
      if (!isLikelyEmail(pendingAttendee)) {
        setCreateError('Add the attendee search value first or enter a valid email address.')
        return
      }
      addAttendee(pendingAttendee)
    }

    try {
      setIsCreating(true)
      await api.post('/api/v1/meetings/', {
        title: form.title.trim(),
        description: form.description.trim(),
        meeting_type: form.meeting_type,
        platform: form.platform,
        scheduled_start: start.toISOString(),
        scheduled_end: end.toISOString(),
        attendee_ids: pendingAttendee && isLikelyEmail(pendingAttendee)
          ? [...selectedAttendees, pendingAttendee]
          : selectedAttendees,
        agenda: parsedAgenda.length ? { topics: parsedAgenda } : null,
        tags: parsedTags,
      }, { timeout: 15000 })

      setShowCreate(false)
      resetCreateForm()
      setBanner({ type: 'success', message: 'Meeting created successfully.' })
      refetch()
    } catch (err: any) {
      if (err?.code === 'ECONNABORTED') {
        setCreateError('Create request timed out. Please try again.')
        setBanner({ type: 'error', message: 'Create request timed out. Please retry.' })
      } else {
        const detail = err?.response?.data?.detail
        const message = Array.isArray(detail)
          ? detail.map((item: any) => item?.msg || 'Validation error').join(', ')
          : detail || 'Failed to create meeting. Please try again.'
        setCreateError(message)
        setBanner({ type: 'error', message })
      }
    } finally {
      setIsCreating(false)
    }
  }

  const handleDelete = async (meetingId: string) => {
    await api.delete(`/api/v1/meetings/${meetingId}`)
    refetch()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Meetings</h1>
          <p className="mt-2 text-gray-600">View and manage all your meeting recordings.</p>
        </div>
        <button
          onClick={() => {
            setShowCreate((value) => !value)
            if (showCreate) resetCreateForm()
          }}
          className="flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
        >
          <Plus className="w-5 h-5 mr-2" />
          New Meeting
        </button>
      </div>

      {banner && (
        <div
          className={`rounded-lg border px-4 py-3 text-sm ${
            banner.type === 'success'
              ? 'bg-green-50 border-green-200 text-green-700'
              : 'bg-red-50 border-red-200 text-red-700'
          }`}
        >
          {banner.message}
        </div>
      )}

      {showCreate && (
        <form onSubmit={handleCreate} className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
          <input
            required
            placeholder="Title"
            value={form.title}
            onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
            className="px-3 py-2 border border-gray-300 rounded-lg"
          />
          <select
            value={form.platform}
            onChange={(e) => setForm((prev) => ({ ...prev, platform: e.target.value as 'manual' | 'zoom' }))}
            className="px-3 py-2 border border-gray-300 rounded-lg"
          >
            <option value="manual">Manual Upload</option>
            <option value="zoom">Zoom</option>
          </select>
          <input
            placeholder="Description"
            value={form.description}
            onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
            className="px-3 py-2 border border-gray-300 rounded-lg md:col-span-2"
          />
          <div>
            <label className="block text-xs text-gray-500 mb-1">Start Time</label>
            <input
              required
              type="datetime-local"
              value={form.scheduled_start}
              onChange={(e) => setForm((prev) => ({ ...prev, scheduled_start: e.target.value }))}
              className="px-3 py-2 border border-gray-300 rounded-lg w-full"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">End Time</label>
            <input
              required
              type="datetime-local"
              value={form.scheduled_end}
              onChange={(e) => setForm((prev) => ({ ...prev, scheduled_end: e.target.value }))}
              className="px-3 py-2 border border-gray-300 rounded-lg w-full"
            />
          </div>

          <div className="md:col-span-2">
            <label className="block text-xs text-gray-500 mb-2">Attendees</label>
            <div className="rounded-lg border border-gray-300 px-3 py-3">
              <div className="flex flex-wrap gap-2 mb-3">
                {selectedAttendees.length > 0 ? selectedAttendees.map((attendee) => (
                  <span key={attendee} className="inline-flex items-center gap-2 rounded-full bg-primary-50 px-3 py-1 text-sm text-primary-700">
                    {attendeeLabel(attendee)}
                    <button type="button" onClick={() => removeAttendee(attendee)} className="text-primary-500 hover:text-primary-700">
                      <X className="w-4 h-4" />
                    </button>
                  </span>
                )) : (
                  <p className="text-sm text-gray-400">Add teammates by username, email, or enter any invite email.</p>
                )}
              </div>
              <div className="flex flex-col gap-2">
                <div className="flex gap-2">
                  <input
                    value={attendeeQuery}
                    onChange={(e) => setAttendeeQuery(e.target.value)}
                    placeholder="Type name, username, or email"
                    className="flex-1 px-3 py-2 border border-gray-200 rounded-lg"
                  />
                  <button
                    type="button"
                    onClick={() => addAttendee(attendeeQuery)}
                    className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50"
                  >
                    Add
                  </button>
                </div>
                {attendeeSuggestions.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {attendeeSuggestions.map((user) => (
                      <button
                        key={user.id}
                        type="button"
                        onClick={() => addAttendee(user.id)}
                        className="rounded-full border border-gray-200 px-3 py-1 text-sm text-gray-700 hover:border-primary-300 hover:text-primary-700"
                      >
                        {user.full_name} • {user.email}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="md:col-span-2">
            <label className="block text-xs text-gray-500 mb-1">Agenda / Topics</label>
            <textarea
              rows={4}
              placeholder={'One topic per line\nReview Q2 goals\nDecide launch owner\nRisks and blockers'}
              value={form.agendaText}
              onChange={(e) => setForm((prev) => ({ ...prev, agendaText: e.target.value }))}
              className="px-3 py-2 border border-gray-300 rounded-lg w-full"
            />
            <p className="mt-2 text-xs text-gray-500">
              {parsedAgenda.length ? `${parsedAgenda.length} topic${parsedAgenda.length === 1 ? '' : 's'} ready to send.` : 'Topics will be sent as a structured agenda list.'}
            </p>
          </div>

          <div className="md:col-span-2">
            <label className="block text-xs text-gray-500 mb-1">Tags</label>
            <input
              placeholder="planning, leadership, roadmap"
              value={form.tagsText}
              onChange={(e) => setForm((prev) => ({ ...prev, tagsText: e.target.value }))}
              className="px-3 py-2 border border-gray-300 rounded-lg w-full"
            />
          </div>

          <div className="md:col-span-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setShowCreate(false)
                resetCreateForm()
              }}
              className="px-4 py-2 border border-gray-300 rounded-lg"
            >
              Cancel
            </button>
            <button type="submit" disabled={isCreating} className="px-4 py-2 bg-primary-600 text-white rounded-lg disabled:opacity-60 disabled:cursor-not-allowed">
              {isCreating ? 'Creating...' : 'Create'}
            </button>
          </div>
          {createError && (
            <div className="md:col-span-2 text-sm text-red-600">{createError}</div>
          )}
        </form>
      )}

      <div className="flex items-center space-x-4">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Search meetings..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          />
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-200">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading meetings...</div>
        ) : isError ? (
          <div className="p-8 text-center">
            <p className="text-red-600 text-sm">
              {(error as any)?.response?.data?.detail ||
                (error as any)?.message ||
                'Could not load meetings right now.'}
            </p>
            <button
              onClick={() => refetch()}
              className="mt-3 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
            >
              Retry
            </button>
          </div>
        ) : filteredMeetings && filteredMeetings.length > 0 ? (
          <div className="divide-y divide-gray-200">
            {filteredMeetings.map((meeting) => (
              <div
                key={meeting.id}
                className="p-6 hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <Link to={`/meetings/${meeting.id}`} className="text-lg font-medium text-gray-900 hover:text-primary-700">
                      {meeting.title}
                    </Link>
                    {!meeting.summary && meeting.description && (
                      <p className="mt-1 text-sm text-gray-500">{meeting.description}</p>
                    )}
                    <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-gray-500">
                      <span>{new Date(meeting.scheduled_start).toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true })}</span>
                      <span>{meeting.platform}</span>
                      <span className="flex items-center gap-1">
                        <Users className="w-3.5 h-3.5" />
                        {meeting.attendee_count ?? meeting.attendee_ids?.length ?? 0}
                      </span>
                      <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                        meeting.status === 'completed' ? 'bg-green-100 text-green-700' :
                        meeting.status === 'in_progress' ? 'bg-blue-100 text-blue-700' :
                        meeting.status === 'failed' ? 'bg-red-100 text-red-700' :
                        'bg-gray-100 text-gray-700'
                      }`}>
                        {meeting.status}
                      </span>
                      {(mentionCountByMeeting[meeting.id] ?? 0) > 0 && (
                        <span className="flex items-center gap-1 px-2 py-1 rounded-full bg-blue-50 text-blue-600 text-xs font-medium">
                          <MessageSquareQuote className="w-3 h-3" />
                          {mentionCountByMeeting[meeting.id]} mention{mentionCountByMeeting[meeting.id] === 1 ? '' : 's'}
                        </span>
                      )}
                    </div>

                    {meeting.status === 'completed' && meeting.summary && (
                      <div className="mt-3 rounded-lg bg-slate-50 border border-slate-100 px-4 py-3">
                        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Summary</p>
                        <p className="text-sm text-gray-700 leading-relaxed">
                          {meeting.summary.length > 200 ? `${meeting.summary.slice(0, 200)}…` : meeting.summary}
                        </p>
                      </div>
                    )}

                    {Array.isArray(meeting.tags) && meeting.tags.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {meeting.tags.map((tag) => (
                          <span key={`${meeting.id}-${tag}`} className="rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-600">
                            #{tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => handleDelete(meeting.id)}
                    className="ml-4 text-gray-400 hover:text-red-600 shrink-0"
                    title="Delete meeting"
                  >
                    <Trash2 className="w-5 h-5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="p-12 text-center">
            <p className="text-gray-500">No meetings found.</p>
            <button onClick={() => setShowCreate(true)} className="mt-4 text-primary-600 hover:text-primary-700 font-medium">
              Create your first meeting
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default Meetings
