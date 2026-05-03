import React, { useMemo, useState } from 'react'
import { useQuery } from 'react-query'
import { CheckCircle, Circle, Clock, Trash2 } from 'lucide-react'
import { api } from '../lib/api'

const ActionItems: React.FC = () => {
  const [error, setError] = useState('')
  const [banner, setBanner] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [priorityFilter, setPriorityFilter] = useState('all')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)
  const [form, setForm] = useState({
    title: '',
    description: '',
    meeting_id: '',
    priority: 'medium',
    due_date: '',
  })

  const { data: actionItems, refetch } = useQuery('action-items', async () => {
    const response = await api.get('/api/v1/action-items/')
    return response.data
  })

  const { data: meetings } = useQuery('meetings-for-actions', async () => {
    const response = await api.get('/api/v1/meetings/')
    return response.data
  })

  const items = actionItems || []
  const now = new Date()

  const filteredItems = useMemo(() => {
    return items.filter((item: any) => {
      const matchesSearch = !search.trim() || [item.title, item.description, meetingTitle(item.meeting_id)]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(search.toLowerCase()))

      const matchesStatus = statusFilter === 'all' || item.status === statusFilter
      const matchesPriority = priorityFilter === 'all' || item.priority === priorityFilter
      return matchesSearch && matchesStatus && matchesPriority
    })
  }, [items, search, statusFilter, priorityFilter, meetings])

  const overdueCount = filteredItems.filter((item: any) => (
    item.due_date && new Date(item.due_date) < now && item.status !== 'completed'
  )).length

  const allVisibleIds = filteredItems.map((i: any) => i.id)
  const allSelected = allVisibleIds.length > 0 && allVisibleIds.every((id: string) => selected.has(id))

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelected(new Set())
    } else {
      setSelected(new Set(allVisibleIds))
    }
  }

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault()
    setError('')
    setBanner(null)
    try {
      await api.post('/api/v1/action-items/', {
        title: form.title.trim(),
        description: form.description.trim(),
        meeting_id: form.meeting_id,
        due_date: form.due_date ? new Date(form.due_date).toISOString() : null,
        priority: form.priority,
      })
      setForm({ title: '', description: '', meeting_id: '', priority: 'medium', due_date: '' })
      setBanner({ type: 'success', message: 'Action item created successfully.' })
      refetch()
    } catch (err: any) {
      const message = err?.response?.data?.detail || 'Failed to create action item'
      setError(message)
      setBanner({ type: 'error', message })
    }
  }

  const handleMarkComplete = async (id: string) => {
    try {
      await api.post(`/api/v1/action-items/${id}/complete`)
      setBanner({ type: 'success', message: 'Action item marked complete.' })
      refetch()
    } catch (err: any) {
      setBanner({ type: 'error', message: err?.response?.data?.detail || 'Failed to update action item.' })
    }
  }

  const handleStatusChange = async (id: string, newStatus: string) => {
    try {
      await api.patch(`/api/v1/action-items/${id}`, { status: newStatus })
      setBanner({ type: 'success', message: `Action item moved to ${newStatus.replace('_', ' ')}.` })
      refetch()
    } catch (err: any) {
      setBanner({ type: 'error', message: err?.response?.data?.detail || 'Failed to update status.' })
    }
  }

  const handleDeleteSelected = async () => {
    if (selected.size === 0) return
    if (!window.confirm(`Delete ${selected.size} action item${selected.size > 1 ? 's' : ''}?`)) return
    setDeleting(true)
    try {
      const params = new URLSearchParams()
      selected.forEach((id) => params.append('ids', id))
      await api.delete(`/api/v1/action-items/?${params.toString()}`)
      setSelected(new Set())
      setBanner({ type: 'success', message: `Deleted ${selected.size} action item${selected.size > 1 ? 's' : ''}.` })
      refetch()
    } catch (err: any) {
      setBanner({ type: 'error', message: err?.response?.data?.detail || 'Failed to delete action items.' })
    } finally {
      setDeleting(false)
    }
  }

  const meetingTitle = (meetingId: string) => {
    const meeting = (meetings || []).find((m: any) => m.id === meetingId)
    return meeting?.title || 'Unknown meeting'
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="w-5 h-5 text-green-500" />
      case 'in_progress':
        return <Clock className="w-5 h-5 text-blue-500" />
      default:
        return <Circle className="w-5 h-5 text-gray-400" />
    }
  }

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'high':
      case 'urgent':
        return 'bg-red-100 text-red-700'
      case 'medium':
        return 'bg-yellow-100 text-yellow-700'
      default:
        return 'bg-gray-100 text-gray-700'
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Action Items</h1>
          <p className="mt-2 text-gray-600">Track and manage your meeting action items.</p>
        </div>
        {selected.size > 0 && (
          <button
            onClick={handleDeleteSelected}
            disabled={deleting}
            className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50"
          >
            <Trash2 className="w-4 h-4" />
            {deleting ? 'Deleting…' : `Delete ${selected.size} selected`}
          </button>
        )}
      </div>

      {banner && (
        <div className={`rounded-lg border px-4 py-3 text-sm ${banner.type === 'success' ? 'border-green-200 bg-green-50 text-green-700' : 'border-red-200 bg-red-50 text-red-700'}`}>
          {banner.message}
        </div>
      )}

      <form onSubmit={handleCreate} className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 grid grid-cols-1 md:grid-cols-5 gap-3">
        <input
          required
          placeholder="Action title"
          value={form.title}
          onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
          className="px-3 py-2 border border-gray-300 rounded-lg"
        />
        <input
          placeholder="Description"
          value={form.description}
          onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
          className="px-3 py-2 border border-gray-300 rounded-lg"
        />
        <select
          required
          value={form.meeting_id}
          onChange={(e) => setForm((prev) => ({ ...prev, meeting_id: e.target.value }))}
          className="px-3 py-2 border border-gray-300 rounded-lg"
        >
          <option value="">Select meeting</option>
          {(meetings || []).map((meeting: any) => (
            <option key={meeting.id} value={meeting.id}>{meeting.title}</option>
          ))}
        </select>
        <input
          type="date"
          value={form.due_date}
          onChange={(e) => setForm((prev) => ({ ...prev, due_date: e.target.value }))}
          className="px-3 py-2 border border-gray-300 rounded-lg"
        />
        <button type="submit" className="px-4 py-2 bg-primary-600 text-white rounded-lg">Create Item</button>
      </form>
      {error && (
        <div className="text-sm text-red-600 -mt-3">{error}</div>
      )}

      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-4 grid grid-cols-1 md:grid-cols-3 gap-3">
        <input
          placeholder="Search action items"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg"
        />
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="px-3 py-2 border border-gray-300 rounded-lg">
          <option value="all">All statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="blocked">Blocked</option>
          <option value="completed">Completed</option>
        </select>
        <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)} className="px-3 py-2 border border-gray-300 rounded-lg">
          <option value="all">All priorities</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
          <option value="urgent">Urgent</option>
        </select>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <p className="text-sm text-gray-600">Total</p>
          <p className="mt-1 text-2xl font-bold text-gray-900">{filteredItems.length}</p>
        </div>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <p className="text-sm text-gray-600">In Progress</p>
          <p className="mt-1 text-2xl font-bold text-blue-600">
            {filteredItems.filter((i: any) => i.status === 'in_progress').length}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <p className="text-sm text-gray-600">Completed</p>
          <p className="mt-1 text-2xl font-bold text-green-600">
            {filteredItems.filter((i: any) => i.status === 'completed').length}
          </p>
        </div>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <p className="text-sm text-gray-600">Overdue</p>
          <p className="mt-1 text-2xl font-bold text-red-600">{overdueCount}</p>
        </div>
      </div>

      {/* Action Items List */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-200">
        {filteredItems.length > 0 && (
          <div className="px-6 py-3 border-b border-gray-200 flex items-center gap-3">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleSelectAll}
              className="w-4 h-4 rounded border-gray-300 text-primary-600"
            />
            <span className="text-sm text-gray-600">
              {allSelected ? 'Deselect all' : `Select all ${filteredItems.length}`}
            </span>
          </div>
        )}
        <div className="divide-y divide-gray-200">
          {filteredItems.length ? filteredItems.map((item: any) => (
            <div key={item.id} className={`p-6 hover:bg-gray-50 transition-colors ${selected.has(item.id) ? 'bg-red-50' : ''}`}>
              <div className="flex items-start space-x-4">
                <input
                  type="checkbox"
                  checked={selected.has(item.id)}
                  onChange={() => toggleOne(item.id)}
                  className="mt-1 w-4 h-4 rounded border-gray-300 text-primary-600"
                />
                {getStatusIcon(item.status)}
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h3 className="text-base font-medium text-gray-900">{item.title}</h3>
                      <p className="mt-1 text-sm text-gray-500">{item.description}</p>
                      <div className="mt-3 flex items-center space-x-3 text-sm text-gray-500">
                        <span>From: {meetingTitle(item.meeting_id)}</span>
                        <span>•</span>
                        <span>Due: {item.due_date ? new Date(item.due_date).toLocaleDateString() : 'No due date'}</span>
                        {item.category && (
                          <>
                            <span>•</span>
                            <span>Category: {item.category}</span>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="ml-4 flex items-center gap-2">
                      <span className={`px-3 py-1 rounded-full text-xs font-medium ${getPriorityColor(item.priority)}`}>
                        {item.priority}
                      </span>
                      <select
                        value={item.status}
                        onChange={(e) => handleStatusChange(item.id, e.target.value)}
                        className="px-2 py-1 border border-gray-300 rounded-lg text-xs"
                      >
                        <option value="open">Open</option>
                        <option value="in_progress">In Progress</option>
                        <option value="blocked">Blocked</option>
                        <option value="completed">Completed</option>
                      </select>
                      {item.status !== 'completed' && (
                        <button
                          className="text-sm text-primary-600 hover:text-primary-700"
                          onClick={() => handleMarkComplete(item.id)}
                        >
                          Mark Complete
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )) : (
            <div className="p-10 text-center text-gray-500">No action items match the current filters.</div>
          )}
        </div>
      </div>
    </div>
  )
}

export default ActionItems
