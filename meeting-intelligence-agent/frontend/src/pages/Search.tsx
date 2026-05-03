import React, { useState } from 'react'
import { useQuery } from 'react-query'
import { Link } from 'react-router-dom'
import { Search as SearchIcon, FileText, Mic, CheckSquare, Lightbulb, Calendar } from 'lucide-react'
import { api } from '../lib/api'

type SearchResult = {
  meeting_id: string
  meeting_title: string
  meeting_date: string | null
  platform: string
  status: string
  relevance_score: number
  match_type: 'title' | 'summary' | 'decision' | 'topic' | 'transcript'
  snippet: string
  transcript_snippet?: string
  transcript_speaker?: string
}

const MATCH_META: Record<string, { label: string; icon: React.ReactNode; colour: string }> = {
  title:      { label: 'Title',     icon: <FileText className="w-3.5 h-3.5" />,    colour: 'bg-blue-100 text-blue-700' },
  summary:    { label: 'Summary',   icon: <FileText className="w-3.5 h-3.5" />,    colour: 'bg-indigo-100 text-indigo-700' },
  decision:   { label: 'Decision',  icon: <CheckSquare className="w-3.5 h-3.5" />, colour: 'bg-green-100 text-green-700' },
  topic:      { label: 'Topic',     icon: <Lightbulb className="w-3.5 h-3.5" />,   colour: 'bg-yellow-100 text-yellow-700' },
  transcript: { label: 'Transcript',icon: <Mic className="w-3.5 h-3.5" />,         colour: 'bg-purple-100 text-purple-700' },
}

function SnippetText({ text }: { text: string }) {
  // Render **bold** markers
  const parts = text.split(/(\*\*[^*]+\*\*)/)
  return (
    <span>
      {parts.map((part, i) =>
        part.startsWith('**') && part.endsWith('**')
          ? <mark key={i} className="bg-yellow-100 text-yellow-900 px-0.5 rounded">{part.slice(2, -2)}</mark>
          : <span key={i}>{part}</span>
      )}
    </span>
  )
}

const SearchPage: React.FC = () => {
  const [inputValue, setInputValue] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')

  const { data: results, isLoading, isFetching } = useQuery<SearchResult[]>(
    ['search', submittedQuery],
    async () => {
      if (!submittedQuery) return []
      const res = await api.get('/api/v1/search/', { params: { q: submittedQuery, limit: 30 } })
      return res.data
    },
    { enabled: Boolean(submittedQuery), keepPreviousData: true },
  )

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = inputValue.trim()
    if (trimmed.length >= 2) setSubmittedQuery(trimmed)
  }

  const loading = isLoading || isFetching

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Search Meetings</h1>
        <p className="mt-1 text-sm text-gray-500">
          Search across all meeting summaries, decisions, topics, and transcripts.
        </p>
      </div>

      {/* Search bar */}
      <form onSubmit={handleSubmit} className="flex gap-2">
        <div className="relative flex-1">
          <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            placeholder='e.g. "pricing strategy" or "Q3 roadmap" or "authentication"'
            className="w-full pl-10 pr-4 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
            autoFocus
          />
        </div>
        <button
          type="submit"
          disabled={inputValue.trim().length < 2}
          className="px-5 py-2.5 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Search
        </button>
      </form>

      {/* Loading */}
      {loading && (
        <div className="text-sm text-gray-500 flex items-center gap-2">
          <div className="w-4 h-4 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
          Searching…
        </div>
      )}

      {/* No results */}
      {!loading && submittedQuery && results && results.length === 0 && (
        <div className="text-center py-16 text-gray-400">
          <SearchIcon className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm">No meetings found for <strong>"{submittedQuery}"</strong></p>
          <p className="text-xs mt-1">Try different keywords or check spelling.</p>
        </div>
      )}

      {/* Results */}
      {!loading && results && results.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs text-gray-400 pb-1">
            {results.length} result{results.length === 1 ? '' : 's'} for <strong>"{submittedQuery}"</strong>
          </p>
          {results.map((r) => {
            const meta = MATCH_META[r.match_type] ?? MATCH_META.summary
            const date = r.meeting_date
              ? new Date(r.meeting_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
              : null
            const scoreBar = Math.round(r.relevance_score * 100)

            return (
              <div key={r.meeting_id} className="bg-white border border-gray-200 rounded-xl p-4 hover:border-indigo-300 hover:shadow-sm transition-all">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    {/* Title + badges */}
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <Link
                        to={`/meetings/${r.meeting_id}`}
                        className="text-base font-semibold text-gray-900 hover:text-indigo-700 truncate"
                      >
                        {r.meeting_title}
                      </Link>
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${meta.colour}`}>
                        {meta.icon}{meta.label}
                      </span>
                    </div>

                    {/* Meta */}
                    <div className="flex items-center gap-3 text-xs text-gray-400 mb-2">
                      {date && (
                        <span className="flex items-center gap-1">
                          <Calendar className="w-3 h-3" />{date}
                        </span>
                      )}
                      <span className="capitalize">{r.platform}</span>
                      <span className={`capitalize px-1.5 py-0.5 rounded text-xs ${
                        r.status === 'completed' ? 'bg-green-50 text-green-600' : 'bg-gray-100 text-gray-500'
                      }`}>{r.status}</span>
                    </div>

                    {/* Snippet */}
                    <p className="text-sm text-gray-600 leading-relaxed">
                      <SnippetText text={r.snippet} />
                    </p>

                    {/* Transcript snippet (if different) */}
                    {r.transcript_snippet && (
                      <div className="mt-2 pl-3 border-l-2 border-purple-200">
                        <p className="text-xs text-purple-600 font-medium mb-0.5">
                          {r.transcript_speaker ? `${r.transcript_speaker} said:` : 'From transcript:'}
                        </p>
                        <p className="text-xs text-gray-500 italic">
                          <SnippetText text={r.transcript_snippet} />
                        </p>
                      </div>
                    )}
                  </div>

                  {/* Relevance bar */}
                  <div className="flex flex-col items-center shrink-0 gap-1 pt-1">
                    <div className="w-1.5 h-14 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className="w-full bg-indigo-500 rounded-full transition-all"
                        style={{ height: `${scoreBar}%` }}
                      />
                    </div>
                    <span className="text-xs text-gray-400">{scoreBar}%</span>
                  </div>
                </div>

                <div className="mt-3 flex justify-end">
                  <Link
                    to={`/meetings/${r.meeting_id}`}
                    className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
                  >
                    Open meeting →
                  </Link>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Empty state (no query yet) */}
      {!submittedQuery && (
        <div className="text-center py-16 text-gray-300">
          <SearchIcon className="w-12 h-12 mx-auto mb-3" />
          <p className="text-sm text-gray-400">Type a keyword and press Search</p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            {['pricing', 'Q3 roadmap', 'authentication', 'hiring', 'sprint planning'].map(ex => (
              <button
                key={ex}
                onClick={() => { setInputValue(ex); setSubmittedQuery(ex) }}
                className="px-3 py-1 text-xs bg-gray-100 hover:bg-indigo-50 hover:text-indigo-700 text-gray-500 rounded-full transition-colors"
              >
                {ex}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default SearchPage
