import React, { useEffect, useMemo, useState } from 'react'
import { useQuery } from 'react-query'
import { Link, useParams } from 'react-router-dom'
import { UploadCloud } from 'lucide-react'
import { api } from '../lib/api'

const MeetingDetail: React.FC = () => {
  const { id } = useParams()
  const [isUploading, setIsUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: meeting, isLoading, refetch } = useQuery(
    ['meeting', id],
    async () => {
      const response = await api.get(`/api/v1/meetings/${id}`)
      return response.data
    },
    {
      refetchInterval: (data: any) => {
        if (!data) return false
        const status = String(data.status || '').toLowerCase()
        const transcriptionStatus = String(data.transcription_status || '').toLowerCase()
        const analysisStatus = String(data.analysis_status || '').toLowerCase()
        const isProcessing =
          ['transcribing', 'processing', 'in_progress'].includes(status) ||
          transcriptionStatus === 'processing' ||
          analysisStatus === 'processing'
        return isProcessing ? 2500 : false
      },
    },
  )

  const { data: allActionItems } = useQuery(
    ['meeting-action-items', id],
    async () => {
      const response = await api.get('/api/v1/action-items/')
      return response.data
    },
    { enabled: Boolean(id) },
  )

  const { data: allMentions } = useQuery(
    ['meeting-mentions', id],
    async () => {
      const response = await api.get('/api/v1/mentions/')
      return response.data
    },
    { enabled: Boolean(id) },
  )

  const meetingActionItems = useMemo(
    () => (allActionItems || []).filter((item: any) => item.meeting_id === id),
    [allActionItems, id],
  )

  const meetingMentions = useMemo(
    () => (allMentions || []).filter((mention: any) => mention.meeting_id === id),
    [allMentions, id],
  )

  const decisions = Array.isArray(meeting?.key_decisions) ? meeting.key_decisions : []
  const topics = Array.isArray(meeting?.discussion_topics) ? meeting.discussion_topics : []

  const processingText = useMemo(() => {
    if (!meeting) return ''
    const status = String(meeting.status || '').toLowerCase()
    const transcriptionStatus = String(meeting.transcription_status || '').toLowerCase()
    const analysisStatus = String(meeting.analysis_status || '').toLowerCase()

    if (transcriptionStatus === 'processing' || status === 'transcribing') {
      return 'Recording uploaded. Transcription is in progress...'
    }
    if (analysisStatus === 'processing' || status === 'processing' || status === 'in_progress') {
      return 'Transcription completed. AI analysis is in progress...'
    }
    if (status === 'failed' || transcriptionStatus === 'failed' || analysisStatus === 'failed') {
      return 'Processing failed. Please upload again.'
    }
    return ''
  }, [meeting])

  useEffect(() => {
    if (!meeting || !uploadMessage || uploadMessage.type !== 'success') return

    const status = String(meeting.status || '').toLowerCase()
    const transcriptionStatus = String(meeting.transcription_status || '').toLowerCase()
    const analysisStatus = String(meeting.analysis_status || '').toLowerCase()

    const isProcessing =
      ['transcribing', 'processing', 'in_progress'].includes(status) ||
      transcriptionStatus === 'processing' ||
      analysisStatus === 'processing'

    const isFailed =
      status === 'failed' ||
      transcriptionStatus === 'failed' ||
      analysisStatus === 'failed'

    if (isFailed) {
      setUploadMessage({ type: 'error', text: 'Processing failed. Please upload again.' })
      return
    }

    if (!isProcessing && uploadMessage.text === 'Upload successful. Processing started.') {
      setUploadMessage({ type: 'success', text: 'Processing complete.' })
    }
  }, [meeting, uploadMessage])

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file || !id) return

    const formData = new FormData()
    formData.append('file', file)

    try {
      setIsUploading(true)
      setUploadMessage(null)
      await api.post(`/api/v1/meetings/${id}/upload`, formData, { timeout: 30000 })
      setUploadMessage({ type: 'success', text: 'Upload successful. Processing started.' })
      await refetch()
    } catch (err: any) {
      const message = err?.response?.data?.detail || 'Upload failed. Please try again.'
      setUploadMessage({ type: 'error', text: message })
    } finally {
      setIsUploading(false)
      event.target.value = ''
    }
  }

  if (isLoading) {
    return <div className="text-gray-600">Loading meeting...</div>
  }

  if (!meeting) {
    return <div className="text-red-600">Meeting not found.</div>
  }

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <h1 className="text-3xl font-bold text-gray-900">{meeting.title}</h1>
        <p className="mt-2 text-gray-600">{meeting.description || 'No description provided.'}</p>

        <div className="mt-6 grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-gray-500">Platform</p>
            <p className="font-medium text-gray-900 capitalize">{meeting.platform}</p>
          </div>
          <div>
            <p className="text-gray-500">Status</p>
            <p className="font-medium text-gray-900 capitalize">{meeting.status}</p>
          </div>
          <div>
            <p className="text-gray-500">Scheduled Start</p>
            <p className="font-medium text-gray-900">{new Date(meeting.scheduled_start).toLocaleString()}</p>
          </div>
          <div>
            <p className="text-gray-500">Scheduled End</p>
            <p className="font-medium text-gray-900">{new Date(meeting.scheduled_end).toLocaleString()}</p>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900">Recording Upload</h2>
        <p className="mt-1 text-sm text-gray-600">Upload audio/video to trigger transcription and analysis.</p>
        <label className={`mt-4 inline-flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg cursor-pointer ${isUploading ? 'opacity-60 cursor-not-allowed' : 'hover:bg-primary-700'}`}>
          <UploadCloud className="w-5 h-5 mr-2" />
          {isUploading ? 'Uploading...' : 'Upload Recording'}
          <input type="file" className="hidden" onChange={handleUpload} disabled={isUploading} />
        </label>
        {uploadMessage && (
          <div
            className={`mt-3 text-sm ${
              uploadMessage.type === 'success' ? 'text-green-700' : 'text-red-600'
            }`}
          >
            {uploadMessage.text}
          </div>
        )}
        {processingText && (
          <div className={`mt-2 text-sm ${processingText.includes('failed') ? 'text-red-600' : 'text-blue-700'}`}>
            {processingText}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900">AI Summary</h2>
        <p className="mt-2 text-gray-700 whitespace-pre-wrap">{meeting.summary || 'Summary will appear after processing.'}</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">Key Decisions</h2>
            <span className="text-sm text-gray-500">{decisions.length}</span>
          </div>
          {decisions.length ? (
            <div className="mt-4 space-y-3">
              {decisions.map((decision: any, index: number) => (
                <div key={index} className="rounded-lg border border-gray-200 p-4">
                  <p className="font-medium text-gray-900">{decision.decision || `Decision ${index + 1}`}</p>
                  {decision.reasoning && (
                    <p className="mt-1 text-sm text-gray-600">{decision.reasoning}</p>
                  )}
                  <div className="mt-2 text-xs text-gray-500">
                    Impact: {decision.impact_level || 'n/a'}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-4 text-sm text-gray-500">No decisions extracted yet.</p>
          )}
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">Discussion Topics</h2>
            <span className="text-sm text-gray-500">{topics.length}</span>
          </div>
          {topics.length ? (
            <div className="mt-4 flex flex-wrap gap-2">
              {topics.map((topic: string, index: number) => (
                <span key={index} className="rounded-full bg-primary-50 px-3 py-1 text-sm text-primary-700">
                  {topic}
                </span>
              ))}
            </div>
          ) : (
            <p className="mt-4 text-sm text-gray-500">Topics will appear after analysis.</p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">Action Items</h2>
            <Link to="/action-items" className="text-sm text-primary-600 hover:text-primary-700">View all</Link>
          </div>
          {meetingActionItems.length ? (
            <div className="mt-4 space-y-3">
              {meetingActionItems.slice(0, 5).map((item: any) => (
                <div key={item.id} className="rounded-lg border border-gray-200 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium text-gray-900">{item.title}</p>
                      <p className="mt-1 text-sm text-gray-600">{item.description || 'No description'}</p>
                    </div>
                    <span className="rounded-full bg-gray-100 px-2 py-1 text-xs text-gray-700 capitalize">
                      {item.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-4 text-sm text-gray-500">No action items linked to this meeting yet.</p>
          )}
        </div>

        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">Mentions</h2>
            <Link to="/mentions" className="text-sm text-primary-600 hover:text-primary-700">View all</Link>
          </div>
          {meetingMentions.length ? (
            <div className="mt-4 space-y-3">
              {meetingMentions.slice(0, 5).map((mention: any) => (
                <div key={mention.id} className="rounded-lg border border-gray-200 p-4">
                  <p className="text-sm font-medium text-gray-900">{mention.mentioned_text}</p>
                  <div className="mt-2 flex items-center gap-2 text-xs text-gray-500">
                    <span className="capitalize">{mention.mention_type}</span>
                    <span>•</span>
                    <span>{mention.sentiment || 'neutral'}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-4 text-sm text-gray-500">No mentions linked to this meeting yet.</p>
          )}
        </div>
      </div>
    </div>
  )
}

export default MeetingDetail
