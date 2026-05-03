import React from 'react'
import { useState } from 'react'
import { useQuery } from 'react-query'
import { api } from '../lib/api'

const Settings: React.FC = () => {
  const { data: profile, refetch } = useQuery('settings-profile', async () => {
    const response = await api.get('/api/v1/users/me')
    return response.data
  })

  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const [fullName, setFullName] = useState('')
  const [timezone, setTimezone] = useState('UTC')
  const [department, setDepartment] = useState('')
  const [jobTitle, setJobTitle] = useState('')
  const [emailEnabled, setEmailEnabled] = useState(true)
  const [slackEnabled, setSlackEnabled] = useState(true)
  const [autoOpenCalendar, setAutoOpenCalendar] = useState(false)

  React.useEffect(() => {
    if (!profile) return
    setFullName(profile.full_name || '')
    setTimezone(profile.timezone || 'UTC')
    setDepartment(profile.department || '')
    setJobTitle(profile.job_title || '')
    setEmailEnabled(profile.notification_settings?.email_enabled ?? true)
    setSlackEnabled(profile.notification_settings?.slack_enabled ?? true)
    setAutoOpenCalendar(profile.preferences?.auto_open_calendar ?? false)
  }, [profile])

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    await api.patch('/api/v1/users/me', {
      full_name: fullName,
      timezone,
      department,
      job_title: jobTitle,
      notification_settings: {
        ...(profile?.notification_settings || {}),
        email_enabled: emailEnabled,
        slack_enabled: slackEnabled,
      },
      preferences: {
        ...(profile?.preferences || {}),
        auto_open_calendar: autoOpenCalendar,
      },
    })
    await refetch()
    setSaving(false)
    setSaved(true)
    setTimeout(() => setSaved(false), 2500)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Settings</h1>
        <p className="mt-2 text-gray-600">Manage profile and notification preferences.</p>
      </div>

      <form onSubmit={handleSave} className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-4 max-w-2xl">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Full Name</label>
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} className="w-full px-3 py-2 border border-gray-300 rounded-lg" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Timezone</label>
          <input value={timezone} onChange={(e) => setTimezone(e.target.value)} className="w-full px-3 py-2 border border-gray-300 rounded-lg" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Department</label>
          <input value={department} onChange={(e) => setDepartment(e.target.value)} className="w-full px-3 py-2 border border-gray-300 rounded-lg" />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Job Title</label>
          <input value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} className="w-full px-3 py-2 border border-gray-300 rounded-lg" />
        </div>

        {/* Notifications */}
        <div className="pt-2 border-t border-gray-200 space-y-2">
          <p className="text-sm font-medium text-gray-700">Notifications</p>
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={emailEnabled} onChange={(e) => setEmailEnabled(e.target.checked)} />
            Email notifications
          </label>
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input type="checkbox" checked={slackEnabled} onChange={(e) => setSlackEnabled(e.target.checked)} />
            Slack notifications
          </label>
        </div>

        {/* Meeting creation behaviour */}
        <div className="pt-2 border-t border-gray-200 space-y-2">
          <p className="text-sm font-medium text-gray-700">Meeting Creation</p>
          <label className="flex items-start gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={autoOpenCalendar}
              onChange={(e) => setAutoOpenCalendar(e.target.checked)}
            />
            <span>
              Auto-open in Google Calendar after creating a Google Meet meeting
              <span className="block text-xs text-gray-500 mt-0.5">
                Opens the calendar event in a new tab immediately after the meeting is created.
              </span>
            </span>
          </label>
        </div>

        <div className="flex items-center gap-3">
          <button type="submit" disabled={saving} className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-60">
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
          {saved && <span className="text-sm text-green-600">Saved!</span>}
        </div>
      </form>
    </div>
  )
}

export default Settings
