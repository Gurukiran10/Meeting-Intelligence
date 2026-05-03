import React from 'react'
import { useState } from 'react'
import { useQuery } from 'react-query'
import { Bell, CheckCircle2, Trash2 } from 'lucide-react'
import { api } from '../lib/api'

const Mentions: React.FC = () => {
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)
  const [banner, setBanner] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const { data: mentions, refetch, isLoading } = useQuery(['mentions', unreadOnly], async () => {
    const response = await api.get('/api/v1/mentions/', {
      params: { unread_only: unreadOnly },
    })
    return response.data
  })

  const list: any[] = mentions || []
  const allSelected = list.length > 0 && list.every((m) => selected.has(m.id))

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelected(new Set())
    } else {
      setSelected(new Set(list.map((m) => m.id)))
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

  const markRead = async (mentionId: string) => {
    await api.post(`/api/v1/mentions/${mentionId}/read`)
    refetch()
  }

  const handleDeleteSelected = async () => {
    if (selected.size === 0) return
    if (!window.confirm(`Delete ${selected.size} mention${selected.size > 1 ? 's' : ''}?`)) return
    setDeleting(true)
    try {
      const params = new URLSearchParams()
      selected.forEach((id) => params.append('ids', id))
      await api.delete(`/api/v1/mentions/?${params.toString()}`)
      setSelected(new Set())
      setBanner({ type: 'success', message: `Deleted ${selected.size} mention${selected.size > 1 ? 's' : ''}.` })
      refetch()
    } catch (err: any) {
      setBanner({ type: 'error', message: err?.response?.data?.detail || 'Failed to delete mentions.' })
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Mentions</h1>
          <p className="mt-2 text-gray-600">Track where you were mentioned in meetings.</p>
        </div>
        <div className="flex items-center gap-3">
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
          <button
            onClick={() => setUnreadOnly((value) => !value)}
            className="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            {unreadOnly ? 'Show All' : 'Unread Only'}
          </button>
        </div>
      </div>

      {banner && (
        <div className={`rounded-lg border px-4 py-3 text-sm ${banner.type === 'success' ? 'border-green-200 bg-green-50 text-green-700' : 'border-red-200 bg-red-50 text-red-700'}`}>
          {banner.message}
        </div>
      )}

      <div className="bg-white rounded-xl shadow-sm border border-gray-200 divide-y divide-gray-200">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading mentions...</div>
        ) : list.length ? (
          <>
            <div className="px-6 py-3 flex items-center gap-3">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={toggleSelectAll}
                className="w-4 h-4 rounded border-gray-300 text-primary-600"
              />
              <span className="text-sm text-gray-600">
                {allSelected ? 'Deselect all' : `Select all ${list.length}`}
              </span>
            </div>
            {list.map((mention: any) => (
              <div key={mention.id} className={`p-6 flex items-start justify-between ${selected.has(mention.id) ? 'bg-red-50' : ''}`}>
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={selected.has(mention.id)}
                    onChange={() => toggleOne(mention.id)}
                    className="mt-1 w-4 h-4 rounded border-gray-300 text-primary-600"
                  />
                  <Bell className={`w-5 h-5 mt-1 ${mention.notification_read ? 'text-gray-400' : 'text-primary-600'}`} />
                  <div>
                    <p className="text-sm font-medium text-gray-900">{mention.mentioned_text}</p>
                    <p className="mt-1 text-xs text-gray-500">Type: {mention.mention_type} • Sentiment: {mention.sentiment || 'neutral'}</p>
                    <p className="mt-1 text-xs text-gray-500">{new Date(mention.created_at).toLocaleString()}</p>
                  </div>
                </div>
                {!mention.notification_read && (
                  <button
                    onClick={() => markRead(mention.id)}
                    className="text-sm text-primary-600 hover:text-primary-700 flex items-center gap-1 shrink-0"
                  >
                    <CheckCircle2 className="w-4 h-4" /> Mark read
                  </button>
                )}
              </div>
            ))}
          </>
        ) : (
          <div className="p-10 text-center text-gray-500">No mentions found.</div>
        )}
      </div>
    </div>
  )
}

export default Mentions
