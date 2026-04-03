import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'

// ─── Types ───────────────────────────────────────────────────────────────────

interface Integration {
  id: string
  name: string
  type: string
  description: string
  connected: boolean
  config: Record<string, string> | null
}

// ─── Icons (inline SVG for zero extra deps) ──────────────────────────────────

const SlackIcon = () => (
  <svg viewBox="0 0 24 24" className="w-8 h-8" fill="none">
    <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" fill="#E01E5A"/>
  </svg>
)

const LinearIcon = () => (
  <svg viewBox="0 0 24 24" className="w-8 h-8" fill="none">
    <path d="M0 14.008L9.992 24H14L0 10v4.008zM0 8l16 16h4L0 4v4zM4 0l20 20V16L8 0H4zM10 0l14 14V10L14 0h-4z" fill="#5E6AD2"/>
  </svg>
)

const ZoomIcon = () => (
  <svg viewBox="0 0 24 24" className="w-8 h-8" fill="none">
    <rect width="24" height="24" rx="4" fill="#2D8CFF"/>
    <path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h7A2.5 2.5 0 0 1 15 8.5v7A2.5 2.5 0 0 1 12.5 18h-7A2.5 2.5 0 0 1 3 15.5v-7zm12 1.207 4.553-2.276A.5.5 0 0 1 20.5 8v8a.5.5 0 0 1-.947.228L15 14.02v-4.313z" fill="white"/>
  </svg>
)

const GoogleIcon = () => (
  <svg viewBox="0 0 24 24" className="w-8 h-8" fill="none">
    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
  </svg>
)

const MicrosoftIcon = () => (
  <svg viewBox="0 0 24 24" className="w-8 h-8" fill="none">
    <path d="M11.4 2H2v9.4h9.4V2z" fill="#F25022"/>
    <path d="M22 2h-9.4v9.4H22V2z" fill="#7FBA00"/>
    <path d="M11.4 12.6H2V22h9.4v-9.4z" fill="#00A4EF"/>
    <path d="M22 12.6h-9.4V22H22v-9.4z" fill="#FFB900"/>
  </svg>
)

const icons: Record<string, JSX.Element> = {
  slack: <SlackIcon />,
  linear: <LinearIcon />,
  zoom: <ZoomIcon />,
  google: <GoogleIcon />,
  microsoft: <MicrosoftIcon />,
}

const typeColors: Record<string, string> = {
  communication: 'bg-pink-100 text-pink-700',
  project_management: 'bg-purple-100 text-purple-700',
  video: 'bg-blue-100 text-blue-700',
  calendar: 'bg-green-100 text-green-700',
}

// ─── Modal: per-integration connect form ─────────────────────────────────────

interface ConnectModalProps {
  integration: Integration
  onClose: () => void
  onConnected: () => void
}

function ConnectModal({ integration, onClose, onConnected }: ConnectModalProps) {
  const [fields, setFields] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [googleOAuthLoading, setGoogleOAuthLoading] = useState(false)

  const set = (k: string, v: string) => setFields(f => ({ ...f, [k]: v }))

  const handleSubmit = async () => {
    setError('')
    setLoading(true)
    try {
      await api.post(`/api/v1/integrations/${integration.id}/connect`, fields)
      onConnected()
      onClose()
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Connection failed. Check your credentials.')
    } finally {
      setLoading(false)
    }
  }

  const startGoogleOAuth = async () => {
    setError('')
    setGoogleOAuthLoading(true)

    try {
      const calendarId = (fields.calendar_id || '').trim() || 'primary'
      const clientId = (fields.client_id || '').trim() || undefined
      const clientSecret = (fields.client_secret || '').trim() || undefined

      sessionStorage.removeItem('mia_google_oauth_last_processed_code')

      sessionStorage.setItem('mia_google_oauth_context', JSON.stringify({
        calendar_id: calendarId,
        client_id: clientId,
        client_secret: clientSecret,
        created_at: Date.now(),
      }))

      const params: Record<string, string> = { calendar_id: calendarId }
      if (clientId) params.client_id = clientId

      const response = await api.get('/api/v1/integrations/google/oauth-url', { params })

      const url = response.data?.url
      if (!url) {
        throw new Error('Missing Google OAuth URL from backend')
      }

      window.location.href = url
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to start Google OAuth flow')
      setGoogleOAuthLoading(false)
    }
  }

  const renderFields = () => {
    switch (integration.id) {
      case 'slack':
        return (
          <>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Slack Bot Token <span className="text-red-500">*</span></label>
              <input
                type="password"
                placeholder="xoxb-xxxxxxxx-xxxxxxxx-xxxxxxxxxxxxxxxx"
                className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                value={fields.bot_token || ''}
                onChange={e => set('bot_token', e.target.value)}
              />
              <p className="text-xs text-gray-500 mt-1">
                Get it from <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">api.slack.com/apps</a> → Your App → OAuth & Permissions → Bot User OAuth Token
              </p>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Default Channel</label>
              <input
                type="text"
                placeholder="#general"
                className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                value={fields.default_channel || ''}
                onChange={e => set('default_channel', e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Incoming Webhook URL (optional)</label>
              <input
                type="text"
                placeholder="https://hooks.slack.com/services/..."
                className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                value={fields.webhook_url || ''}
                onChange={e => set('webhook_url', e.target.value)}
              />
            </div>
          </>
        )
      case 'linear':
        return (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Linear Personal API Key <span className="text-red-500">*</span></label>
            <input
              type="password"
              placeholder="lin_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
              value={fields.api_key || ''}
              onChange={e => set('api_key', e.target.value)}
            />
            <p className="text-xs text-gray-500 mt-1">
              Get it from <a href="https://linear.app/settings/api" target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">linear.app/settings/api</a> → Personal API Keys → Create key
            </p>
          </div>
        )
      case 'zoom':
        return (
          <>
            <div className="p-3 bg-blue-50 rounded-lg text-xs text-blue-700 mb-2">
              Create a Server-to-Server OAuth app at <a href="https://marketplace.zoom.us/develop/create" target="_blank" rel="noreferrer" className="underline font-medium">marketplace.zoom.us</a>. Required scopes for this app: <code>user:read:user:admin</code> and <code>meeting:read:list_meetings:admin</code>. For Zoom history, recordings, and transcripts, also add <code>cloud_recording:read:list_user_recordings:admin</code> and <code>cloud_recording:read:recording:admin</code>.
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Account ID <span className="text-red-500">*</span></label>
              <input type="text" placeholder="xxxxxxxxxxxxxxxxxxxxxxxx" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.account_id || ''} onChange={e => set('account_id', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Client ID <span className="text-red-500">*</span></label>
              <input type="text" placeholder="xxxxxxxxxxxxxxxxxxxxxxxx" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.client_id || ''} onChange={e => set('client_id', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Client Secret <span className="text-red-500">*</span></label>
              <input type="password" placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.client_secret || ''} onChange={e => set('client_secret', e.target.value)} />
            </div>
          </>
        )
      case 'google':
        return (
          <>
            <div className="p-3 bg-yellow-50 rounded-lg text-xs text-yellow-800 mb-2">
              Option A: API key for public calendars. Option B: service account JSON for shared private calendars. Option C (recommended): Google OAuth one-click flow for real Google Meet access.
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
              <input type="password" placeholder="AIzaSy..." className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.api_key || ''} onChange={e => set('api_key', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Calendar ID</label>
              <input type="text" placeholder="primary or your-email@gmail.com" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.calendar_id || ''} onChange={e => set('calendar_id', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Service Account JSON (optional, for private calendars)</label>
              <textarea rows={4} placeholder='{"type": "service_account", ...}' className="w-full border rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.service_account_json || ''} onChange={e => set('service_account_json', e.target.value)} />
            </div>
            <div className="pt-1 border-t" />
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">OAuth Client ID (optional if set in backend env)</label>
              <input type="text" placeholder="xxxxxxxx.apps.googleusercontent.com" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.client_id || ''} onChange={e => set('client_id', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">OAuth Client Secret (optional if set in backend env)</label>
              <input type="password" placeholder="GOCSPX-..." className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.client_secret || ''} onChange={e => set('client_secret', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">OAuth Redirect URI</label>
              <input type="text" placeholder={`${window.location.origin}/integrations`} className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.redirect_uri || ''} onChange={e => set('redirect_uri', e.target.value)} />
            </div>
            <div>
              <button
                type="button"
                onClick={startGoogleOAuth}
                disabled={googleOAuthLoading}
                className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {googleOAuthLoading ? 'Redirecting to Google...' : 'Continue with Google OAuth'}
              </button>
              <p className="text-xs text-gray-500 mt-1">Use this for real Google Meet integration without manual auth code copy/paste.</p>
            </div>
          </>
        )
      case 'microsoft':
        return (
          <>
            <div className="p-3 bg-blue-50 rounded-lg text-xs text-blue-700 mb-2">
              Register an app at <a href="https://portal.azure.com/#blade/Microsoft_AAD_IAM/ActiveDirectoryMenuBlade/RegisteredApps" target="_blank" rel="noreferrer" className="underline font-medium">Azure Portal</a>. Add API permissions: <code>Calendars.Read</code>, <code>OnlineMeetings.Read</code>. Grant admin consent.
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Tenant ID <span className="text-red-500">*</span></label>
              <input type="text" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.tenant_id || ''} onChange={e => set('tenant_id', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Client ID <span className="text-red-500">*</span></label>
              <input type="text" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.client_id || ''} onChange={e => set('client_id', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Client Secret <span className="text-red-500">*</span></label>
              <input type="password" placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.client_secret || ''} onChange={e => set('client_secret', e.target.value)} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Calendar User Email (optional)</label>
              <input type="email" placeholder="user@company.com" className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none" value={fields.calendar_user || ''} onChange={e => set('calendar_user', e.target.value)} />
            </div>
          </>
        )
      default:
        return null
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-md max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          {/* Header */}
          <div className="flex items-center gap-3 mb-5">
            <div className="p-2 rounded-xl bg-gray-50">{icons[integration.id]}</div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Connect {integration.name}</h2>
              <p className="text-sm text-gray-500">{integration.description}</p>
            </div>
          </div>

          {/* Fields */}
          <div className="space-y-4">
            {renderFields()}
          </div>

          {/* Error */}
          {error && (
            <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="mt-6 flex gap-3">
            <button onClick={onClose} className="flex-1 px-4 py-2 border rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50">
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={loading}
              className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading ? (
                <><span className="h-4 w-4 border-2 border-white border-t-transparent rounded-full animate-spin" />Connecting...</>
              ) : 'Connect'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Modal: Test confirmation ─────────────────────────────────────────────────

interface TestModalProps {
  integration: Integration
  onClose: () => void
  onConfirm: (data?: Record<string, any>) => Promise<void>
}

function TestModal({ integration, onClose, onConfirm }: TestModalProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [slackChannel, setSlackChannel] = useState(integration.config?.default_channel || '#general')

  const handleConfirm = async () => {
    setError('')
    setLoading(true)
    try {
      const data = integration.id === 'slack' ? { channel: slackChannel } : undefined
      await onConfirm(data)
      onClose()
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Test failed')
    } finally {
      setLoading(false)
    }
  }

  const getTestDescription = () => {
    switch (integration.id) {
      case 'slack':
        return `Send a test message to ${slackChannel}`
      case 'linear':
        return 'Verify API key and fetch team info'
      case 'zoom':
        return 'Verify OAuth credentials and fetch account info'
      case 'google':
        return 'Verify API key and fetch calendar info'
      case 'microsoft':
        return 'Verify credentials and fetch organization info'
      default:
        return 'Run integration test'
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-md">
        <div className="p-6">
          {/* Header */}
          <div className="flex items-center gap-3 mb-5">
            <div className="p-2 rounded-xl bg-blue-50">{icons[integration.id]}</div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Test {integration.name}</h2>
              <p className="text-sm text-gray-500">Verify your connection</p>
            </div>
          </div>

          {/* Description */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4">
            <p className="text-sm text-blue-900">
              ✓ {getTestDescription()}
            </p>
          </div>

          {/* Channel input for Slack */}
          {integration.id === 'slack' && (
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Channel to test
              </label>
              <input
                type="text"
                value={slackChannel}
                onChange={(e) => setSlackChannel(e.target.value)}
                placeholder="#general"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-500 mt-1">Use # prefix for channels or @ for users</p>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 mb-4">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2 border rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={loading}
              className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading ? (
                <><span className="h-4 w-4 border-2 border-white border-t-transparent rounded-full animate-spin" />Testing...</>
              ) : 'Test Connection'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function Integrations() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const qc = useQueryClient()
  const [connectingId, setConnectingId] = useState<string | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [syncingId, setSyncingId] = useState<string | null>(null)
  const [historyImportingId, setHistoryImportingId] = useState<string | null>(null)
  const [zoomHistoryDaysBack, setZoomHistoryDaysBack] = useState<number>(30)
  const [autoSyncRunning, setAutoSyncRunning] = useState(false)
  const [autoJoinRunning, setAutoJoinRunning] = useState(false)
  const [autoSyncSavingPlatform, setAutoSyncSavingPlatform] = useState<string | null>(null)
  const [autoSyncResult, setAutoSyncResult] = useState<{ msg: string; ok: boolean } | null>(null)
  const [capturePolicySaving, setCapturePolicySaving] = useState(false)
  const [testResult, setTestResult] = useState<Record<string, { msg: string; ok: boolean }>>({})
  const [syncResult, setSyncResult] = useState<Record<string, { msg: string; ok: boolean }>>({})
  const [capturePolicyForm, setCapturePolicyForm] = useState({
    auto_join_enabled: true,
    auto_transcription_enabled: true,
    retention_days: 30,
    require_explicit_consent: true,
    respect_no_record_requests: true,
    smart_recording_enabled: true,
    min_team_size: 1,
  })

  useEffect(() => {
    const code = searchParams.get('code')
    if (!code) return

    const lastProcessedCode = sessionStorage.getItem('mia_google_oauth_last_processed_code')
    if (lastProcessedCode === code) {
      navigate('/integrations', { replace: true })
      return
    }

    sessionStorage.setItem('mia_google_oauth_last_processed_code', code)

    const runGoogleOAuthCallback = async () => {
      setAutoSyncResult({ ok: true, msg: 'Completing Google OAuth...' })

      try {
        const rawContext = sessionStorage.getItem('mia_google_oauth_context')
        const ctx = rawContext ? JSON.parse(rawContext) : {}

        await api.post('/api/v1/integrations/google/oauth/callback', {
          code,
          redirect_uri: ctx.redirect_uri || undefined,
          calendar_id: ctx.calendar_id || 'primary',
          client_id: ctx.client_id || undefined,
          client_secret: ctx.client_secret || undefined,
        })

        sessionStorage.removeItem('mia_google_oauth_context')
        setAutoSyncResult({ ok: true, msg: 'Google OAuth connected successfully' })
        await qc.refetchQueries('integrations')
        await qc.refetchQueries('capture-policy')
      } catch (e: any) {
        setAutoSyncResult({ ok: false, msg: e?.response?.data?.detail || 'Google OAuth callback failed' })
      } finally {
        navigate('/integrations', { replace: true })
        setTimeout(() => setAutoSyncResult(null), 6000)
      }
    }

    runGoogleOAuthCallback()
  }, [navigate, qc, searchParams])

  const { data: integrations = [], isLoading } = useQuery(
    'integrations',
    () => api.get('/api/v1/integrations/').then(r => r.data as Integration[]),
  )

  const { data: autoSyncStatus } = useQuery(
    'auto-sync-status',
    () => api.get('/api/v1/integrations/auto-sync/status').then(r => r.data as {
      last_run_at?: string
      next_run_at?: string
      enabled?: Record<string, boolean>
      platforms?: Record<string, {
        status: string
        updated_at?: string
        detail?: string
        last_success_at?: string
        last_error_at?: string
        metrics?: { created?: number; updated?: number; skipped?: number; fetched?: number }
      }>
    }),
  )

  useQuery(
    'capture-policy',
    () => api.get('/api/v1/integrations/capture-policy').then(r => r.data as {
      auto_join_enabled: boolean
      auto_transcription_enabled: boolean
      retention_days: number
      require_explicit_consent: boolean
      respect_no_record_requests: boolean
      smart_recording_enabled: boolean
      min_team_size: number
    }),
    {
      onSuccess: (data) => {
        setCapturePolicyForm({
          auto_join_enabled: !!data.auto_join_enabled,
          auto_transcription_enabled: !!data.auto_transcription_enabled,
          retention_days: Number(data.retention_days || 30),
          require_explicit_consent: !!data.require_explicit_consent,
          respect_no_record_requests: !!data.respect_no_record_requests,
          smart_recording_enabled: !!data.smart_recording_enabled,
          min_team_size: Number(data.min_team_size || 1),
        })
      },
    },
  )

  const zoomConnected = integrations.find(i => i.id === 'zoom')?.connected
  const googleConnected = integrations.find(i => i.id === 'google')?.connected

  const { data: zoomMeetings = [] } = useQuery(
    'zoom-meetings',
    () => api.get('/api/v1/integrations/zoom/meetings').then(r => r.data as {
      id: string; topic: string; start_time: string; duration: number; join_url: string
    }[]),
    { enabled: !!zoomConnected },
  )

  const { data: googleMeetEvents = [], refetch: refetchGoogleMeet } = useQuery(
    'google-meet-upcoming',
    () => api.get('/api/v1/integrations/google/meet/upcoming', { params: { days_ahead: 30 } }).then(r => r.data as {
      events: Array<{
        id: string; summary: string; start: { dateTime?: string; date?: string };
        end: { dateTime?: string; date?: string }; _meet_url: string;
        attendees?: Array<{ email: string; displayName?: string }>
      }>; count: number
    }),
    { enabled: !!googleConnected, select: d => d.events },
  )

  const disconnectMutation = useMutation(
    (id: string) => api.delete(`/api/v1/integrations/${id}/disconnect`),
    { onSuccess: () => qc.invalidateQueries('integrations') },
  )

  const handleTest = (id: string) => {
    setTestingId(id)
  }

  const performTest = async (id: string, data?: Record<string, any>) => {
    try {
      const r = await api.post(`/api/v1/integrations/${id}/test`, data || {})
      const d = r.data
      let msg = 'Test passed!'
      if (id === 'slack') msg = d.message || `Sent to ${d.channel}`
      if (id === 'linear') msg = `Teams: ${d.teams?.join(', ') || 'none found'}`
      if (id === 'zoom') msg = `Connected as ${d.user} (${d.email})`
      if (id === 'google') msg = `Calendar: ${d.summary || d.calendar_id || 'connected'}`
      if (id === 'microsoft') msg = `Organization: ${d.organization}`
      setTestResult(t => ({ ...t, [id]: { msg, ok: true } }))
    } catch (e: any) {
      setTestResult(t => ({ ...t, [id]: { msg: e?.response?.data?.detail || 'Test failed', ok: false } }))
      throw e
    } finally {
      setTimeout(() => setTestResult(t => { const n = { ...t }; delete n[id]; return n }), 5000)
    }
  }

  const performSync = async (id: string) => {
    setSyncingId(id)
    try {
      const params = id === 'zoom' ? { limit: 20 } : { days_ahead: 30, limit: 50 }
      const r = await api.post(`/api/v1/integrations/${id}/sync`, {}, { params })
      const d = r.data
      const msg = `Synced: ${d.created || 0} created, ${d.updated || 0} updated, ${d.skipped || 0} skipped`
      setSyncResult(s => ({ ...s, [id]: { msg, ok: true } }))

      if (id === 'zoom') {
        qc.invalidateQueries('zoom-meetings')
      }
      if (id === 'google') {
        qc.invalidateQueries('google-meet-upcoming')
      }
      qc.invalidateQueries('meetings')
    } catch (e: any) {
      setSyncResult(s => ({ ...s, [id]: { msg: e?.response?.data?.detail || 'Sync failed', ok: false } }))
    } finally {
      setSyncingId(null)
      setTimeout(() => setSyncResult(s => { const n = { ...s }; delete n[id]; return n }), 5000)
    }
  }

  const performHistoryImport = async (id: string, daysBack: number) => {
    setHistoryImportingId(id)
    try {
      const r = await api.post(`/api/v1/integrations/${id}/import-history`, {}, {
        params: {
          days_back: daysBack,
          limit: 20,
          import_recordings: true,
          import_transcripts: true,
        },
      })
      const d = r.data
      const msg = `History: ${d.imported || 0} imported, ${d.updated || 0} updated, ${d.transcripts_imported || 0} transcripts, ${d.recordings_found || 0} recordings`
      setSyncResult(s => ({ ...s, [id]: { msg, ok: true } }))
      qc.invalidateQueries('meetings')
    } catch (e: any) {
      setSyncResult(s => ({ ...s, [id]: { msg: e?.response?.data?.detail || 'History import failed', ok: false } }))
    } finally {
      setHistoryImportingId(null)
      setTimeout(() => setSyncResult(s => { const n = { ...s }; delete n[id]; return n }), 5000)
    }
  }

  const runAutoSyncNow = async () => {
    setAutoSyncRunning(true)
    try {
      const response = await api.post('/api/v1/integrations/auto-sync/run-now')
      const results = response.data?.results || {}
      const platforms = Object.keys(results)
      const successCount = platforms.filter((platform) => results[platform]?.status !== 'error').length
      const errorCount = platforms.filter((platform) => results[platform]?.status === 'error').length

      setAutoSyncResult({
        ok: errorCount === 0,
        msg: `Auto sync finished: ${successCount} succeeded, ${errorCount} failed across ${platforms.length} platform${platforms.length === 1 ? '' : 's'}`,
      })

      await qc.refetchQueries('auto-sync-status')
      await qc.refetchQueries('meetings')
      await qc.refetchQueries('zoom-meetings')
      await qc.refetchQueries('integrations')
    } finally {
      setAutoSyncRunning(false)
      setTimeout(() => setAutoSyncResult(null), 6000)
    }
  }

  const runAutoJoinNow = async () => {
    setAutoJoinRunning(true)
    try {
      const response = await api.post('/api/v1/integrations/bots/auto-join/run-now')
      const joined = Number(response.data?.joined || 0)
      const blocked = Number(response.data?.blocked || 0)
      const skipped = Number(response.data?.skipped || 0)
      setAutoSyncResult({
        ok: true,
        msg: `Bot dispatch finished: ${joined} joined, ${blocked} blocked, ${skipped} skipped`,
      })
      await qc.refetchQueries('meetings')
    } catch (e: any) {
      setAutoSyncResult({ ok: false, msg: e?.response?.data?.detail || 'Bot auto-join run failed' })
    } finally {
      setAutoJoinRunning(false)
      setTimeout(() => setAutoSyncResult(null), 6000)
    }
  }

  const saveCapturePolicy = async () => {
    setCapturePolicySaving(true)
    try {
      await api.put('/api/v1/integrations/capture-policy', capturePolicyForm)
      setAutoSyncResult({ ok: true, msg: 'Capture policy saved' })
      await qc.refetchQueries('capture-policy')
    } catch (e: any) {
      setAutoSyncResult({ ok: false, msg: e?.response?.data?.detail || 'Failed to save capture policy' })
    } finally {
      setCapturePolicySaving(false)
      setTimeout(() => setAutoSyncResult(null), 6000)
    }
  }

  const toggleAutoSyncPlatform = async (platform: string, enabled: boolean) => {
    setAutoSyncSavingPlatform(platform)
    try {
      await api.post('/api/v1/integrations/auto-sync/settings', { platform, enabled })
      setAutoSyncResult({
        ok: true,
        msg: `${platform.charAt(0).toUpperCase() + platform.slice(1)} auto sync ${enabled ? 'enabled' : 'disabled'}`,
      })
      await qc.refetchQueries('auto-sync-status')
    } catch (e: any) {
      setAutoSyncResult({ ok: false, msg: e?.response?.data?.detail || 'Failed to update auto sync setting' })
    } finally {
      setAutoSyncSavingPlatform(null)
      setTimeout(() => setAutoSyncResult(null), 6000)
    }
  }

  const connected = integrations.filter(i => i.connected)
  const notConnected = integrations.filter(i => !i.connected)
  const autoSyncPlatforms = connected
    .filter(i => ['zoom', 'google', 'microsoft'].includes(i.id))
    .map(i => i.id)

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <div className="bg-white border-b px-6 py-5">
        <div className="max-w-4xl mx-auto">
          <h1 className="text-2xl font-bold text-gray-900">Integrations</h1>
          <p className="text-gray-500 mt-1">Connect your tools to supercharge meeting intelligence.</p>
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-6 py-8 space-y-8">

        {/* Auto sync status */}
        <div className="bg-white rounded-xl border p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-700">Integration Auto Sync</h2>
              <p className="text-xs text-gray-500 mt-1">
                Last run: {autoSyncStatus?.last_run_at ? new Date(autoSyncStatus.last_run_at).toLocaleString() : 'Never'}
              </p>
              <p className="text-xs text-gray-500 mt-1">
                Next run: {autoSyncStatus?.next_run_at ? new Date(autoSyncStatus.next_run_at).toLocaleString() : 'Pending first run'}
              </p>
            </div>
            <button
              onClick={runAutoSyncNow}
              disabled={autoSyncRunning}
              className="text-xs px-3 py-1.5 bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100 font-medium transition-colors disabled:opacity-50"
            >
              {autoSyncRunning ? 'Running...' : 'Run now'}
            </button>
          </div>
          <div className="mt-2 flex justify-end">
            <button
              onClick={runAutoJoinNow}
              disabled={autoJoinRunning}
              className="text-xs px-3 py-1.5 bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 font-medium transition-colors disabled:opacity-50"
            >
              {autoJoinRunning ? 'Dispatching bots...' : 'Run bot auto-join'}
            </button>
          </div>
          {autoSyncResult && (
            <div className={`mt-3 text-xs px-3 py-2 rounded-lg ${autoSyncResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              {autoSyncResult.ok ? '✓ ' : '✗ '}{autoSyncResult.msg}
            </div>
          )}
          {autoSyncPlatforms.length > 0 && (
            <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2">
              {autoSyncPlatforms.map((platform) => {
                const status = autoSyncStatus?.platforms?.[platform]
                const isEnabled = autoSyncStatus?.enabled?.[platform] ?? true
                const isHealthy = status?.status === 'ok' || status?.status === 'skipped' || !status

                return (
                <div key={platform} className={`text-xs rounded-lg px-3 py-2 ${isHealthy ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-medium capitalize">{platform}</div>
                    <button
                      onClick={() => toggleAutoSyncPlatform(platform, !isEnabled)}
                      disabled={autoSyncSavingPlatform === platform}
                      className={`px-2 py-1 rounded-md font-medium ${isEnabled ? 'bg-white/80 text-green-700' : 'bg-white/80 text-gray-600'} disabled:opacity-50`}
                    >
                      {autoSyncSavingPlatform === platform ? 'Saving...' : (isEnabled ? 'Enabled' : 'Disabled')}
                    </button>
                  </div>
                  <div className="mt-1">
                    {status?.status === 'error'
                      ? (status.detail || 'Last run failed')
                      : status?.status === 'skipped'
                        ? (status.detail || 'Skipped')
                        : 'Last run succeeded'}
                  </div>
                  {status?.metrics && status.status === 'ok' && (
                    <div className="mt-1 opacity-80">
                      {status.metrics.created || 0} created · {status.metrics.updated || 0} updated · {status.metrics.fetched || 0} fetched
                    </div>
                  )}
                  <div className="mt-1 opacity-80">
                    Last success: {status?.last_success_at ? new Date(status.last_success_at).toLocaleString() : '—'}
                  </div>
                  <div className="opacity-80">
                    Last error: {status?.last_error_at ? new Date(status.last_error_at).toLocaleString() : '—'}
                  </div>
                </div>
              )})}
            </div>
          )}
        </div>

        {/* Capture policy */}
        <div className="bg-white rounded-xl border p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-700">Meeting Capture Policy</h2>
              <p className="text-xs text-gray-500 mt-1">Controls consent, smart recording rules, and retention for captured meetings.</p>
            </div>
            <button
              onClick={saveCapturePolicy}
              disabled={capturePolicySaving}
              className="text-xs px-3 py-1.5 bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100 font-medium transition-colors disabled:opacity-50"
            >
              {capturePolicySaving ? 'Saving...' : 'Save policy'}
            </button>
          </div>
          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <label className="text-xs text-gray-600 flex items-center gap-2">
              <input
                type="checkbox"
                checked={capturePolicyForm.auto_join_enabled}
                onChange={(e) => setCapturePolicyForm(s => ({ ...s, auto_join_enabled: e.target.checked }))}
              />
              Auto-join enabled
            </label>
            <label className="text-xs text-gray-600 flex items-center gap-2">
              <input
                type="checkbox"
                checked={capturePolicyForm.auto_transcription_enabled}
                onChange={(e) => setCapturePolicyForm(s => ({ ...s, auto_transcription_enabled: e.target.checked }))}
              />
              Auto transcription
            </label>
            <label className="text-xs text-gray-600 flex items-center gap-2">
              <input
                type="checkbox"
                checked={capturePolicyForm.require_explicit_consent}
                onChange={(e) => setCapturePolicyForm(s => ({ ...s, require_explicit_consent: e.target.checked }))}
              />
              Require consent
            </label>
            <label className="text-xs text-gray-600 flex items-center gap-2">
              <input
                type="checkbox"
                checked={capturePolicyForm.respect_no_record_requests}
                onChange={(e) => setCapturePolicyForm(s => ({ ...s, respect_no_record_requests: e.target.checked }))}
              />
              Respect no-record
            </label>
            <label className="text-xs text-gray-600 flex items-center gap-2">
              <input
                type="checkbox"
                checked={capturePolicyForm.smart_recording_enabled}
                onChange={(e) => setCapturePolicyForm(s => ({ ...s, smart_recording_enabled: e.target.checked }))}
              />
              Smart recording
            </label>
            <label className="text-xs text-gray-600">
              Retention
              <select
                value={capturePolicyForm.retention_days}
                onChange={(e) => setCapturePolicyForm(s => ({ ...s, retention_days: Number(e.target.value) }))}
                className="ml-2 text-xs border rounded px-2 py-1"
              >
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
              </select>
            </label>
            <label className="text-xs text-gray-600">
              Min team size
              <input
                type="number"
                min={1}
                max={200}
                value={capturePolicyForm.min_team_size}
                onChange={(e) => setCapturePolicyForm(s => ({ ...s, min_team_size: Number(e.target.value || 1) }))}
                className="ml-2 w-16 text-xs border rounded px-2 py-1"
              />
            </label>
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-white rounded-xl border p-4 text-center">
            <div className="text-3xl font-bold text-indigo-600">{connected.length}</div>
            <div className="text-sm text-gray-500 mt-1">Connected</div>
          </div>
          <div className="bg-white rounded-xl border p-4 text-center">
            <div className="text-3xl font-bold text-gray-400">{notConnected.length}</div>
            <div className="text-sm text-gray-500 mt-1">Available</div>
          </div>
          <div className="bg-white rounded-xl border p-4 text-center">
            <div className="text-3xl font-bold text-green-500">{integrations.length}</div>
            <div className="text-sm text-gray-500 mt-1">Total</div>
          </div>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-12">
            <div className="h-8 w-8 border-4 border-indigo-600 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <>
            {/* Connected */}
            {connected.length > 0 && (
              <div>
                <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Connected</h2>
                <div className="space-y-3">
                  {connected.map(intg => (
                    <IntegrationCard
                      key={intg.id}
                      integration={intg}
                      testResult={testResult[intg.id]}
                      syncResult={syncResult[intg.id]}
                      syncing={syncingId === intg.id}
                      historyImporting={historyImportingId === intg.id}
                      zoomHistoryDaysBack={zoomHistoryDaysBack}
                      onZoomHistoryDaysBackChange={setZoomHistoryDaysBack}
                      onConnect={() => setConnectingId(intg.id)}
                      onDisconnect={() => disconnectMutation.mutate(intg.id)}
                      onTest={() => handleTest(intg.id)}
                      onSync={() => performSync(intg.id)}
                      onImportHistory={() => performHistoryImport(intg.id, zoomHistoryDaysBack)}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Available */}
            {notConnected.length > 0 && (
              <div>
                <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Available</h2>
                <div className="space-y-3">
                  {notConnected.map(intg => (
                    <IntegrationCard
                      key={intg.id}
                      integration={intg}
                      testResult={testResult[intg.id]}
                      syncResult={syncResult[intg.id]}
                      syncing={syncingId === intg.id}
                      historyImporting={historyImportingId === intg.id}
                      zoomHistoryDaysBack={zoomHistoryDaysBack}
                      onZoomHistoryDaysBackChange={setZoomHistoryDaysBack}
                      onConnect={() => setConnectingId(intg.id)}
                      onDisconnect={() => disconnectMutation.mutate(intg.id)}
                      onTest={() => handleTest(intg.id)}
                      onSync={() => performSync(intg.id)}
                      onImportHistory={() => performHistoryImport(intg.id, zoomHistoryDaysBack)}
                    />
                  ))}
                </div>
              </div>
            )}
            {/* Zoom upcoming meetings panel */}
            {zoomConnected && zoomMeetings.length > 0 && (
              <div>
                <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Upcoming Zoom Meetings</h2>
                <div className="bg-white rounded-xl border divide-y">
                  {zoomMeetings.map(m => (
                    <div key={m.id} className="flex items-center justify-between px-5 py-3">
                      <div>
                        <p className="text-sm font-medium text-gray-900">{m.topic}</p>
                        <p className="text-xs text-gray-500">{new Date(m.start_time).toLocaleString()} · {m.duration} min</p>
                      </div>
                      <a
                        href={m.join_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs px-3 py-1.5 bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 font-medium"
                      >
                        Join
                      </a>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Google Meet upcoming events panel */}
            {googleConnected && googleMeetEvents.length > 0 && (
              <div>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider">Upcoming Google Meet Events</h2>
                  <button
                    onClick={() => refetchGoogleMeet()}
                    className="text-xs px-3 py-1.5 bg-green-50 text-green-700 rounded-lg hover:bg-green-100 font-medium"
                  >
                    Refresh
                  </button>
                </div>
                <div className="bg-white rounded-xl border divide-y">
                  {googleMeetEvents.map(ev => {
                    const startRaw = ev.start?.dateTime || ev.start?.date || ''
                    const startDate = startRaw ? new Date(startRaw).toLocaleString() : '—'
                    const attendeeCount = ev.attendees?.length ?? 0
                    return (
                      <div key={ev.id} className="flex items-center justify-between px-5 py-3">
                        <div>
                          <p className="text-sm font-medium text-gray-900">{ev.summary || 'Google Meet'}</p>
                          <p className="text-xs text-gray-500">
                            {startDate}{attendeeCount > 0 ? ` · ${attendeeCount} attendee${attendeeCount !== 1 ? 's' : ''}` : ''}
                          </p>
                        </div>
                        <a
                          href={ev._meet_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs px-3 py-1.5 bg-green-50 text-green-700 rounded-lg hover:bg-green-100 font-medium"
                        >
                          Join Meet
                        </a>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Connect modal */}
      {connectingId && (
        <ConnectModal
          integration={integrations.find(i => i.id === connectingId)!}
          onClose={() => setConnectingId(null)}
          onConnected={() => qc.invalidateQueries('integrations')}
        />
      )}

      {/* Test modal */}
      {testingId && (
        <TestModal
          integration={integrations.find(i => i.id === testingId)!}
          onClose={() => setTestingId(null)}
          onConfirm={(data) => performTest(testingId, data)}
        />
      )}
    </div>
  )
}

// ─── Card component ───────────────────────────────────────────────────────────

interface CardProps {
  integration: Integration
  testResult?: { msg: string; ok: boolean }
  syncResult?: { msg: string; ok: boolean }
  syncing?: boolean
  historyImporting?: boolean
  zoomHistoryDaysBack: number
  onZoomHistoryDaysBackChange: (daysBack: number) => void
  onConnect: () => void
  onDisconnect: () => void
  onTest: () => void
  onSync: () => void
  onImportHistory: () => void
}

function IntegrationCard({ integration, testResult, syncResult, syncing, historyImporting, zoomHistoryDaysBack, onZoomHistoryDaysBackChange, onConnect, onDisconnect, onTest, onSync, onImportHistory }: CardProps) {
  const { id, name, type, description, connected, config } = integration
  const canSync = id === 'zoom' || id === 'microsoft' || id === 'google'
  const canImportHistory = id === 'zoom'

  return (
    <div className={`bg-white rounded-xl border-2 p-5 transition-all ${connected ? 'border-green-200' : 'border-gray-200 hover:border-gray-300'}`}>
      <div className="flex items-start gap-4">
        {/* Icon */}
        <div className="p-2 rounded-xl bg-gray-50 flex-shrink-0">{icons[id]}</div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-gray-900">{name}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${typeColors[type] || 'bg-gray-100 text-gray-600'}`}>
              {type.replace('_', ' ')}
            </span>
            {connected && (
              <span className="flex items-center gap-1 text-xs text-green-600 font-medium bg-green-50 px-2 py-0.5 rounded-full">
                <span className="h-1.5 w-1.5 rounded-full bg-green-500 inline-block" /> Connected
              </span>
            )}
          </div>
          <p className="text-sm text-gray-500 mt-1">{description}</p>

          {/* Config preview when connected */}
          {connected && config && (
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-gray-500">
              {Object.entries(config).map(([k, v]) => v ? (
                <span key={k} className="bg-gray-50 rounded-md px-2 py-1">
                  <span className="font-medium text-gray-600">{k.replace('_', ' ')}: </span>{v}
                </span>
              ) : null)}
            </div>
          )}

          {/* Test result banner */}
          {testResult && (
            <div className={`mt-2 text-xs px-3 py-1.5 rounded-lg ${testResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              {testResult.ok ? '✓ ' : '✗ '}{testResult.msg}
            </div>
          )}

          {syncResult && (
            <div className={`mt-2 text-xs px-3 py-1.5 rounded-lg ${syncResult.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              {syncResult.ok ? '✓ ' : '✗ '}{syncResult.msg}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {connected ? (
            <>
              {canSync && (
                <button
                  onClick={onSync}
                  disabled={!!syncing || !!historyImporting}
                  className="text-xs px-3 py-1.5 bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 font-medium transition-colors disabled:opacity-50"
                >
                  {syncing ? 'Syncing...' : 'Sync'}
                </button>
              )}
              {canImportHistory && (
                <>
                  <select
                    value={zoomHistoryDaysBack}
                    onChange={(e) => onZoomHistoryDaysBackChange(Number(e.target.value))}
                    disabled={!!syncing || !!historyImporting}
                    className="text-xs px-2 py-1.5 border border-purple-200 text-purple-700 rounded-lg bg-purple-50 focus:outline-none"
                  >
                    <option value={30}>30d</option>
                    <option value={90}>90d</option>
                    <option value={180}>180d</option>
                  </select>
                  <button
                    onClick={onImportHistory}
                    disabled={!!syncing || !!historyImporting}
                    className="text-xs px-3 py-1.5 bg-purple-50 text-purple-600 rounded-lg hover:bg-purple-100 font-medium transition-colors disabled:opacity-50"
                  >
                    {historyImporting ? 'Importing...' : 'History'}
                  </button>
                </>
              )}
              <button
                onClick={onTest}
                className="text-xs px-3 py-1.5 bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100 font-medium transition-colors"
              >
                Test
              </button>
              <button
                onClick={onDisconnect}
                className="text-xs px-3 py-1.5 bg-red-50 text-red-600 rounded-lg hover:bg-red-100 font-medium transition-colors"
              >
                Disconnect
              </button>
            </>
          ) : (
            <button
              onClick={onConnect}
              className="text-sm px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 font-medium transition-colors"
            >
              Connect
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
