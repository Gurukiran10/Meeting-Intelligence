import React, { useEffect, useMemo, useState } from 'react'
import { useQuery } from 'react-query'
import { Plus, Search, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'

const Meetings: React.FC = () => {
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  const [banner, setBanner] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  const [form, setForm] = useState({
    title: '',
    description: '',
    meeting_type: 'internal',
    platform: 'manual',
    scheduled_start: '',
    scheduled_end: '',
  })

  const { data: meetings, isLoading, refetch } = useQuery('meetings', async () => {
    const response = await api.get('/api/v1/meetings/')
    return response.data
  })

  const filteredMeetings = useMemo(() => {
    const list = meetings || []
    if (!search.trim()) return list
    const q = search.toLowerCase()
    return list.filter((meeting: any) =>
      [meeting.title, meeting.description, meeting.platform, meeting.status]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(q)),
    )
  }, [meetings, search])

  useEffect(() => {
    if (!banner) return
    const timeoutId = window.setTimeout(() => setBanner(null), 4000)
    return () => window.clearTimeout(timeoutId)
  }, [banner])

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

    try {
      setIsCreating(true)
      await api.post('/api/v1/meetings/', {
        ...form,
        title: form.title.trim(),
        description: form.description.trim(),
        scheduled_start: start.toISOString(),
        scheduled_end: end.toISOString(),
        attendee_ids: [],
        tags: [],
      }, { timeout: 15000 })

      setShowCreate(false)
      setForm({
        title: '',
        description: '',
        meeting_type: 'internal',
        platform: 'manual',
        scheduled_start: '',
        scheduled_end: '',
      })
      setBanner({ type: 'success', message: 'Meeting created successfully.' })
      refetch()
    } catch (err: any) {
      if (err?.code === 'ECONNABORTED') {
        setCreateError('Create request timed out. Please try again.')
        setBanner({ type: 'error', message: 'Create request timed out. Please retry.' })
      } else {
        const message = err?.response?.data?.detail || 'Failed to create meeting. Please try again.'
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
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Meetings</h1>
          <p className="mt-2 text-gray-600">View and manage all your meeting recordings.</p>
        </div>
        <button
          onClick={() => setShowCreate((value) => !value)}
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
            onChange={(e) => setForm((prev) => ({ ...prev, platform: e.target.value }))}
            className="px-3 py-2 border border-gray-300 rounded-lg"
          >
            <option value="manual">Manual Upload</option>
            <option value="zoom">Zoom</option>
            <option value="google_meet">Google Meet</option>
            <option value="microsoft_teams">Microsoft Teams</option>
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
          <div className="md:col-span-2 flex justify-end gap-2">
            <button type="button" onClick={() => setShowCreate(false)} className="px-4 py-2 border border-gray-300 rounded-lg">Cancel</button>
            <button type="submit" disabled={isCreating} className="px-4 py-2 bg-primary-600 text-white rounded-lg disabled:opacity-60 disabled:cursor-not-allowed">
              {isCreating ? 'Creating...' : 'Create'}
            </button>
          </div>
          {createError && (
            <div className="md:col-span-2 text-sm text-red-600">{createError}</div>
          )}
        </form>
      )}

      {/* Search and Filters */}
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

      {/* Meetings List */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading meetings...</div>
        ) : filteredMeetings && filteredMeetings.length > 0 ? (
          <div className="divide-y divide-gray-200">
            {filteredMeetings.map((meeting: any) => (
              <div
                key={meeting.id}
                className="p-6 hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <Link to={`/meetings/${meeting.id}`} className="text-lg font-medium text-gray-900 hover:text-primary-700">
                      {meeting.title}
                    </Link>
                    <p className="mt-1 text-sm text-gray-500">{meeting.description}</p>
                    <div className="mt-3 flex items-center space-x-4 text-sm text-gray-500">
                      <span>{new Date(meeting.scheduled_start).toLocaleDateString()}</span>
                      <span>•</span>
                      <span>{meeting.platform}</span>
                      <span>•</span>
                      <span className={`px-2 py-1 rounded-full text-xs ${
                        meeting.status === 'completed' ? 'bg-green-100 text-green-700' :
                        meeting.status === 'in_progress' ? 'bg-blue-100 text-blue-700' :
                        'bg-gray-100 text-gray-700'
                      }`}>
                        {meeting.status}
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => handleDelete(meeting.id)}
                    className="ml-4 text-gray-400 hover:text-red-600"
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
            <button className="mt-4 text-primary-600 hover:text-primary-700 font-medium">
              Upload your first meeting recording
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default Meetings
