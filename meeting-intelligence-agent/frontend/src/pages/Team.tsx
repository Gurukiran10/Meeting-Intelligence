import React, { useState } from 'react'
import { useQuery, useQueryClient } from 'react-query'
import { Copy, Mail, Send, ShieldCheck, Users } from 'lucide-react'

import { api } from '../lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'

const Team: React.FC = () => {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [successLink, setSuccessLink] = useState('')
  const [sending, setSending] = useState(false)

  const { data: currentUser } = useQuery('team-current-user', async () => {
    const response = await api.get('/api/v1/auth/me')
    return response.data
  })

  const { data: members = [], isLoading } = useQuery('organization-members', async () => {
    const response = await api.get('/api/v1/users/')
    return response.data
  })

  const handleInvite = async (event: React.FormEvent) => {
    event.preventDefault()
    setError('')
    setSuccessLink('')
    setSending(true)

    try {
      const response = await api.post('/api/v1/org/invite', { email })
      setSuccessLink(response.data.invite_link)
      setEmail('')
      queryClient.invalidateQueries('organization-members')
    } catch (inviteError: any) {
      setError(String(inviteError?.response?.data?.detail || 'Unable to send invite right now.'))
    } finally {
      setSending(false)
    }
  }

  const isAdmin = currentUser?.role === 'admin'
  const organizationName = currentUser?.organization?.name || 'your organization'

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Team</h1>
          <p className="mt-1.5 text-slate-500 font-medium">
            Invite teammates and manage roles for {organizationName}.
          </p>
          <div className="mt-3 inline-flex items-center rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-blue-700">
            Organization: {organizationName}
          </div>
        </div>
        <div className="inline-flex items-center rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-slate-500">
          <Users className="mr-2 h-3.5 w-3.5" />
          {members.length} member{members.length === 1 ? '' : 's'}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[360px_minmax(0,1fr)]">
        <Card className="border-slate-200/60 shadow-sm">
          <CardHeader>
            <CardTitle className="text-xl font-bold">Invite User</CardTitle>
            <CardDescription>
              {isAdmin
                ? 'Send an organization invite by email. The backend will log the invite link for now.'
                : 'Only admins can invite new members.'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleInvite} className="space-y-4">
              <div className="relative">
                <Mail className="absolute left-3 top-3.5 h-4 w-4 text-slate-400" />
                <Input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="teammate@company.com"
                  className="pl-10"
                  disabled={!isAdmin || sending}
                  required
                />
              </div>
              <Button
                type="submit"
                className="w-full bg-blue-600 hover:bg-blue-700"
                disabled={!isAdmin || sending}
              >
                <Send className="mr-2 h-4 w-4" />
                {sending ? 'Sending invite...' : 'Invite'}
              </Button>
            </form>

            {error ? (
              <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
                {error}
              </div>
            ) : null}

              {successLink ? (
                <div className="mt-4 rounded-lg border border-green-200 bg-green-50 px-3 py-3 text-sm text-green-700">
                  Invite created successfully.
                  <div className="mt-2 break-all text-xs text-green-800">{successLink}</div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-3 border-green-200 bg-white text-green-700 hover:bg-green-100"
                    onClick={() => navigator.clipboard.writeText(successLink)}
                  >
                    <Copy className="mr-2 h-3.5 w-3.5" />
                    Copy invite link
                  </Button>
                </div>
              ) : null}
          </CardContent>
        </Card>

        <Card className="border-slate-200/60 shadow-sm">
          <CardHeader>
            <CardTitle className="text-xl font-bold">Members</CardTitle>
            <CardDescription>Organization members visible in your current workspace.</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="py-10 text-center text-sm text-slate-400">Loading members...</div>
            ) : (
              <div className="space-y-3">
                {members.map((member: any) => (
                  <div
                    key={member.id}
                    className="flex items-center justify-between rounded-xl border border-slate-100 px-4 py-3 hover:bg-slate-50/60"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-bold text-slate-900">{member.full_name}</p>
                      <p className="truncate text-sm text-slate-500">{member.email}</p>
                      <p className="mt-1 text-xs uppercase tracking-wide text-slate-400">@{member.username}</p>
                    </div>
                    <Badge variant={member.role === 'admin' ? 'default' : 'secondary'} className="capitalize">
                      {member.role === 'admin' ? (
                        <span className="inline-flex items-center">
                          <ShieldCheck className="mr-1 h-3 w-3" />
                          Admin
                        </span>
                      ) : (
                        'Member'
                      )}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

export default Team
