import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Bot,
  Brain,
  Briefcase,
  Check,
  ChevronDown,
  CircleDot,
  Globe,
  LoaderCircle,
  MessageSquare,
  PanelLeft,
  Plane,
  Plus,
  RefreshCw,
  Route,
  Search,
  SendHorizonal,
  Sparkles,
  Trash2,
  User,
  Waves,
  X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { v4 as uuidv4 } from 'uuid'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { streamChat, type ChatStreamEvent } from '@/lib/chat-stream'
import { env } from '@/lib/env'
import {
  listSpecialists,
  refreshSpecialist,
  registerSpecialist,
  type SpecialistSummary,
} from '@/lib/orchestrator-api'
import { cn } from '@/lib/utils'

type ChatRole = 'user' | 'assistant'

type Phase =
  | 'idle'
  | 'analyzing'
  | 'routing'
  | 'working'
  | 'streaming'
  | 'done'
  | 'error'

type ActivityEvent = {
  id: string
  kind: 'task' | 'status' | 'artifact' | 'message' | 'route' | 'search'
  icon: 'task' | 'status' | 'artifact' | 'message' | 'route' | 'search'
  label: string
  detail: string
  specialistName?: string
}

type ChatMessage = {
  id: string
  role: ChatRole
  content: string
  status?: 'streaming' | 'done'
  phase?: Phase
  events?: ActivityEvent[]
  specialistName?: string
  // Per-specialist accumulated raw text (for the live activity preview).
  specialistStreams?: Record<string, string>
}

const starterPrompts = [
  'Plan a 4-day Tokyo itinerary under $1,500',
  'What is a good warm destination in Europe for October?',
  'Suggest kid-friendly activities for a Singapore weekend trip',
  'Build a Bali budget trip with hotels, food, and local transport',
]

const phaseLabel: Record<Phase, string> = {
  idle: 'Idle',
  analyzing: 'Thinking',
  routing: 'Routing',
  working: 'Working',
  streaming: 'Responding',
  done: 'Done',
  error: 'Error',
}

function phaseIcon(phase: Phase) {
  switch (phase) {
    case 'routing':
      return Route
    case 'streaming':
      return CircleDot
    case 'done':
      return Check
    case 'error':
      return CircleDot
    default:
      return Brain
  }
}

function Markdown({ content }: { content: string }) {
  return (
    <div className="text-[15px] leading-7 text-zinc-100">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ node, ...props }) => <h1 className="mt-5 mb-3 text-2xl font-semibold tracking-tight" {...props} />,
          h2: ({ node, ...props }) => <h2 className="mt-5 mb-2 text-xl font-semibold tracking-tight" {...props} />,
          h3: ({ node, ...props }) => <h3 className="mt-4 mb-2 text-lg font-semibold" {...props} />,
          h4: ({ node, ...props }) => <h4 className="mt-3 mb-1 text-base font-semibold" {...props} />,
          p: ({ node, ...props }) => <p className="my-3 leading-7" {...props} />,
          ul: ({ node, ...props }) => <ul className="my-3 list-disc space-y-1 pl-6" {...props} />,
          ol: ({ node, ...props }) => <ol className="my-3 list-decimal space-y-1 pl-6" {...props} />,
          li: ({ node, ...props }) => <li className="leading-7" {...props} />,
          a: ({ node, ...props }) => <a className="text-blue-300 underline underline-offset-2" target="_blank" rel="noreferrer" {...props} />,
          strong: ({ node, ...props }) => <strong className="font-semibold text-white" {...props} />,
          em: ({ node, ...props }) => <em className="italic" {...props} />,
          hr: ({ node, ...props }) => <hr className="my-5 border-white/10" {...props} />,
          blockquote: ({ node, ...props }) => <blockquote className="my-3 border-l-2 border-white/20 pl-4 italic text-zinc-300" {...props} />,
          code: ({ node, className, children, ...props }) => (
            <code className={cn('rounded bg-white/10 px-1.5 py-0.5 font-mono text-[13px]', className)} {...props}>
              {children}
            </code>
          ),
          pre: ({ node, ...props }) => <pre className="my-3 overflow-x-auto rounded-2xl border border-white/10 bg-black/40 p-4 text-[13px]" {...props} />,
          table: ({ node, ...props }) => (
            <div className="my-4 overflow-x-auto">
              <table className="w-full border-collapse text-sm" {...props} />
            </div>
          ),
          thead: ({ node, ...props }) => <thead className="border-b border-white/15" {...props} />,
          th: ({ node, ...props }) => <th className="border-b border-white/15 px-3 py-2 text-left font-semibold" {...props} />,
          td: ({ node, ...props }) => <td className="border-b border-white/10 px-3 py-2 align-top" {...props} />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function ActivityIcon({ kind }: { kind: ActivityEvent['icon'] }) {
  const cls = 'h-3.5 w-3.5'
  switch (kind) {
    case 'task':
      return <CircleDot className={cn(cls, 'text-amber-300')} />
    case 'route':
      return <Route className={cn(cls, 'text-violet-300')} />
    case 'search':
      return <Search className={cn(cls, 'text-sky-300')} />
    case 'artifact':
      return <CircleDot className={cn(cls, 'text-blue-300')} />
    case 'message':
      return <Check className={cn(cls, 'text-emerald-300')} />
    default:
      return <Brain className={cn(cls, 'text-zinc-300')} />
  }
}

function ActivityTrail({ message }: { message: ChatMessage }) {
  const events = message.events ?? []
  const phase = message.phase ?? 'idle'
  const specialistName = message.specialistName
  const active = message.status === 'streaming' && phase !== 'done' && phase !== 'error'
  const [expanded, setExpanded] = useState(
    phase === 'analyzing' || phase === 'routing' || phase === 'working',
  )

  // keep expanded while thinking, auto-collapse once responding
  useEffect(() => {
    if (phase === 'streaming' || phase === 'done' || phase === 'error') {
      setExpanded(false)
    } else if (phase === 'analyzing' || phase === 'routing' || phase === 'working') {
      setExpanded(true)
    }
  }, [phase])

  if (!events.length && phase === 'idle') return null

  const PhaseIcon = phaseIcon(phase)
  const lastEvent = events[events.length - 1]

  const summary = (() => {
    if (phase === 'routing') return specialistName ? `Delegated to ${specialistName}` : 'Routed to specialist'
    if (phase === 'streaming')
      return specialistName
        ? `${specialistName} is responding${events.filter((e) => e.kind === 'artifact').length ? ` · ${events.filter((e) => e.kind === 'artifact').length} chunks` : ''}`
        : `Streaming response${events.filter((e) => e.kind === 'artifact').length ? ` · ${events.filter((e) => e.kind === 'artifact').length} chunks` : ''}`
    if (phase === 'done') return specialistName ? `${specialistName} completed · ${events.length} events` : `Completed · ${events.length} events`
    if (phase === 'working') return specialistName ? `${specialistName} is working` : 'Specialist is working'
    if (phase === 'analyzing') return 'Analyzing request'
    return lastEvent?.label ?? 'Working'
  })()

  return (
    <div className="mb-3 rounded-2xl border border-white/10 bg-white/[0.03]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left"
      >
        <span
          className={cn(
            'flex h-6 w-6 shrink-0 items-center justify-center rounded-lg',
            active ? 'bg-blue-500/15 text-blue-300' : 'bg-white/5 text-zinc-300',
          )}
        >
          {active ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <PhaseIcon className="h-3.5 w-3.5" />
          )}
        </span>
        <span className="text-[13px] font-medium text-zinc-200">{phaseLabel[phase]}</span>
        {specialistName && (phase === 'routing' || phase === 'working' || phase === 'streaming' || phase === 'done') ? (
          <span className="flex items-center gap-1.5">
            <Badge variant="outline" className="border-violet-400/30 bg-violet-500/10 text-[10px] text-violet-200">
              {specialistName}
            </Badge>
          </span>
        ) : null}
        <span className="truncate text-[12px] text-muted-foreground">{summary}</span>
        <span className="ml-auto flex items-center gap-2">
          {active ? (
            <span className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-400" />
              <span className="text-[11px] text-blue-300">live</span>
            </span>
          ) : null}
          <ChevronDown
            className={cn('h-4 w-4 text-muted-foreground transition-transform', expanded && 'rotate-180')}
          />
        </span>
      </button>

      <AnimatePresence initial={false}>
        {expanded ? (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="space-y-1 border-t border-white/5 px-3.5 py-2.5">
              {events.map((event) => (
                <div key={event.id} className="flex items-start gap-2.5 py-1">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-white/[0.04]">
                    <ActivityIcon kind={event.kind} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12px] font-medium text-zinc-300">{event.label}</div>
                    {event.detail ? (
                      <div className="truncate text-[11px] text-muted-foreground">{event.detail}</div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}

type Conversation = {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
  updatedAt: number
}

const STORAGE_KEY = 'nimbus-conversations'

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as Conversation[]
    if (!Array.isArray(parsed)) return []
    // Normalize any in-flight streaming messages to done on reload.
    return parsed.map((c) => ({
      ...c,
      messages: c.messages.map((m) =>
        m.status === 'streaming' ? { ...m, status: 'done' as const } : m,
      ),
    }))
  } catch {
    return []
  }
}

function titleFromMessage(text: string) {
  const trimmed = text.trim().replace(/\s+/g, ' ')
  return trimmed.length > 42 ? `${trimmed.slice(0, 42)}…` : trimmed || 'New conversation'
}

export function HomePage() {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [clientStatus, setClientStatus] = useState<'connecting' | 'ready' | 'error'>('connecting')
  const [clientError, setClientError] = useState<string>('')
  const [conversations, setConversations] = useState<Conversation[]>(() => {
    const loaded = loadConversations()
    if (loaded.length) return loaded
    return [{ id: uuidv4(), title: 'New conversation', messages: [], createdAt: Date.now(), updatedAt: Date.now() }]
  })
  const [activeConversationId, setActiveConversationId] = useState<string>(() => {
    const loaded = loadConversations()
    return loaded[0]?.id ?? uuidv4()
  })
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [specialists, setSpecialists] = useState<SpecialistSummary[]>([])
  const [isRegistering, setIsRegistering] = useState(false)
  const [refreshingSpecialistId, setRefreshingSpecialistId] = useState<string | null>(null)
  const [registrationError, setRegistrationError] = useState('')
  const [registrationSuccess, setRegistrationSuccess] = useState('')
  const [form, setForm] = useState({
    name: 'Nimbus Travel Planner',
    url: 'http://localhost:8001',
    description: 'Travel planning specialist for destinations, itineraries, budgets, and activities.',
    tags: 'travel, itinerary, budget',
    notes: '',
  })
  const [showSpecialistModal, setShowSpecialistModal] = useState(false)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? conversations[0]
  const messages = activeConversation?.messages ?? []

  // Persist conversations to localStorage.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations))
    } catch {
      // storage may be full or unavailable; ignore.
    }
  }, [conversations])

  function setMessages(updater: (current: ChatMessage[]) => ChatMessage[]) {
    setConversations((convs) =>
      convs.map((c) =>
        c.id === activeConversationId
          ? { ...c, messages: updater(c.messages), updatedAt: Date.now() }
          : c,
      ),
    )
  }

  useEffect(() => {
    let cancelled = false

    async function bootstrap() {
      try {
        const registry = await listSpecialists(env.orchestratorBaseUrl)
        if (!cancelled) {
          setSpecialists(registry)
          setClientStatus('ready')
        }
      } catch (error) {
        if (!cancelled) {
          setClientStatus('error')
          setClientError(error instanceof Error ? error.message : 'Failed to reach the orchestrator.')
        }
      }
    }

    void bootstrap()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  const activeSpecialistCount = specialists.length

  function createConversation() {
    const newConv: Conversation = {
      id: uuidv4(),
      title: 'New conversation',
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    setConversations((convs) => [newConv, ...convs])
    setActiveConversationId(newConv.id)
    setInput('')
  }

  function selectConversation(id: string) {
    if (isStreaming) return
    setActiveConversationId(id)
  }

  function deleteConversation(id: string) {
    if (isStreaming) return
    setConversations((convs) => {
      const filtered = convs.filter((c) => c.id !== id)
      if (filtered.length === 0) {
        const fresh: Conversation = {
          id: uuidv4(),
          title: 'New conversation',
          messages: [],
          createdAt: Date.now(),
          updatedAt: Date.now(),
        }
        setActiveConversationId(fresh.id)
        return [fresh]
      }
      if (id === activeConversationId) {
        setActiveConversationId(filtered[0].id)
      }
      return filtered
    })
  }

  function updateLastAssistant(updater: (message: ChatMessage) => ChatMessage) {
    setMessages((current) => {
      const idx = [...current]
        .map((message, index) => ({ message, index }))
        .reverse()
        .find((entry) => entry.message.role === 'assistant')?.index
      if (idx === undefined) return current
      const next = [...current]
      next[idx] = updater(next[idx])
      return next
    })
  }

  function pushEvent(event: Omit<ActivityEvent, 'id'>) {
    updateLastAssistant((message) => ({
      ...message,
      events: [...(message.events ?? []), { id: uuidv4(), ...event }],
    }))
  }

  async function refreshSpecialists() {
    const registry = await listSpecialists(env.orchestratorBaseUrl)
    setSpecialists(registry)
  }

  async function handleRegisterSpecialist() {
    setIsRegistering(true)
    setRegistrationError('')
    setRegistrationSuccess('')

    try {
      const response = await registerSpecialist(env.orchestratorBaseUrl, {
        name: form.name,
        url: form.url,
        description: form.description,
        notes: form.notes,
        tags: form.tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      })
      await refreshSpecialists()
      setRegistrationSuccess(`Registered ${response.specialist.name} successfully.`)
    } catch (error) {
      setRegistrationError(error instanceof Error ? error.message : 'Registration failed.')
    } finally {
      setIsRegistering(false)
    }
  }

  async function handleRefreshSpecialist(specialist: SpecialistSummary) {
    setRefreshingSpecialistId(specialist.id)
    setRegistrationError('')
    setRegistrationSuccess('')

    try {
      const response = await refreshSpecialist(env.orchestratorBaseUrl, specialist.id)
      await refreshSpecialists()
      setRegistrationSuccess(`Refreshed ${response.specialist.name} agent card.`)
    } catch (error) {
      setRegistrationError(error instanceof Error ? error.message : 'Refresh failed.')
    } finally {
      setRefreshingSpecialistId(null)
    }
  }

  async function handleSendMessage(prompt = input) {
    const content = prompt.trim()
    if (!content || isStreaming || !activeConversation) return

    const contextId = activeConversation.id

    setInput('')
    setIsStreaming(true)
    setConversations((convs) =>
      convs.map((c) =>
        c.id === contextId
          ? {
              ...c,
              title: c.messages.length === 0 ? titleFromMessage(content) : c.title,
              messages: [
                ...c.messages,
                { id: uuidv4(), role: 'user', content },
                { id: uuidv4(), role: 'assistant', content: '', status: 'streaming', phase: 'idle', events: [], specialistStreams: {} },
              ],
              updatedAt: Date.now(),
            }
          : c,
      ),
    )

    try {
      for await (const event of streamChat(env.orchestratorBaseUrl, content, contextId)) {
        processStreamEvent(event)
      }

      updateLastAssistant((message) => ({
        ...message,
        status: 'done',
        phase: message.phase === 'error' ? 'error' : message.phase === 'streaming' ? 'done' : message.phase ?? 'done',
      }))
    } catch (error) {
      pushEvent({
        kind: 'status',
        icon: 'status',
        label: 'Stream failed',
        detail: error instanceof Error ? error.message : 'Unknown streaming error.',
      })
      updateLastAssistant((message) => ({
        ...message,
        status: 'done',
        phase: 'error',
        content:
          message.content ||
          'Sorry — I could not reach the orchestrator stream. Make sure the backend is running and configured.',
      }))
    } finally {
      setIsStreaming(false)
    }
  }

  function processStreamEvent(event: ChatStreamEvent) {
    switch (event.type) {
      case 'status': {
        const phase = event.phase
        const text = event.text || ''
        const specialistName = event.specialist_name
        const specs = event.specialists ?? []

        if (phase === 'routing') {
          updateLastAssistant((message) => ({ ...message, phase: 'analyzing' }))
          pushEvent({ kind: 'status', icon: 'status', label: 'Analyzing', detail: text })
          return
        }

        if (phase === 'route_decision') {
          const count = specs.length
          updateLastAssistant((message) => ({
            ...message,
            phase: count === 0 ? 'analyzing' : 'routing',
            specialistName: count === 1 ? specs[0]?.name : message.specialistName,
          }))
          pushEvent({
            kind: 'route',
            icon: 'route',
            label: count === 0 ? 'Direct response' : `Routed to ${count} specialist${count === 1 ? '' : 's'}`,
            detail: text,
          })
          return
        }

        if (phase === 'specialist_working') {
          updateLastAssistant((message) => ({
            ...message,
            phase: 'working',
            specialistName: specialistName ?? message.specialistName,
          }))
          pushEvent({
            kind: 'status',
            icon: 'search',
            label: specialistName ? `${specialistName} is working` : 'Specialist working',
            detail: text,
          })
          return
        }

        if (phase === 'specialist_done') {
          // Flip the specialist's live-stream event to "completed".
          if (specialistName) {
            updateLastAssistant((message) => ({
              ...message,
              events: (message.events ?? []).map((e) =>
                e.kind === 'artifact' && e.specialistName === specialistName
                  ? { ...e, label: `${specialistName} responded`, detail: 'Response complete.' }
                  : e,
              ),
            }))
          }
          return
        }

        // responding / synthesizing / assembling → streaming
        if (phase === 'responding' || phase === 'synthesizing' || phase === 'assembling') {
          updateLastAssistant((message) => ({ ...message, phase: 'streaming' }))
          pushEvent({
            kind: 'status',
            icon: 'status',
            label:
              phase === 'synthesizing' ? 'Synthesizing' : phase === 'assembling' ? 'Assembling' : 'Responding',
            detail: text,
          })
          return
        }

        pushEvent({ kind: 'status', icon: 'status', label: 'Status', detail: text })
        return
      }

      case 'token': {
        if (event.text) {
          updateLastAssistant((message) => ({
            ...message,
            phase: 'streaming',
            content: message.content + event.text,
          }))
        }
        return
      }

      case 'specialist_chunk': {
        // Accumulate per-specialist text into a single rolling "live" activity
        // event (rather than one event per chunk). The event's detail shows a
        // truncated, growing preview so the user sees progress without noise.
        const sname = event.specialist_name
        updateLastAssistant((message) => {
          const streams = { ...(message.specialistStreams ?? {}) }
          streams[sname] = (streams[sname] ?? '') + event.text
          const preview = streams[sname].replace(/\s+/g, ' ').slice(0, 140)
          const events = [...(message.events ?? [])]
          // Find or create the single live-stream event for this specialist.
          let idx = events.findIndex(
            (e) => e.kind === 'artifact' && e.specialistName === sname,
          )
          if (idx === -1) {
            events.push({
              id: uuidv4(),
              kind: 'artifact' as const,
              icon: 'artifact' as const,
              label: `${sname} is responding`,
              detail: preview,
              specialistName: sname,
            })
          } else {
            events[idx] = { ...events[idx], detail: preview }
          }
          return {
            ...message,
            phase: message.phase === 'streaming' ? message.phase : 'working',
            specialistName: sname ?? message.specialistName,
            specialistStreams: streams,
            events,
          }
        })
        return
      }

      case 'done': {
        // Mark any specialist live-stream events as completed.
        updateLastAssistant((message) => ({
          ...message,
          status: 'done',
          phase: 'done',
          content: event.final_response || message.content,
          events: (message.events ?? []).map((e) =>
            e.kind === 'artifact'
              ? { ...e, label: `${e.specialistName ?? 'Specialist'} responded`, detail: 'Response complete.' }
              : e,
          ),
        }))
        pushEvent({
          kind: 'message',
          icon: 'message',
          label: 'Completed',
          detail: 'Response complete.',
        })
        return
      }

      case 'error': {
        updateLastAssistant((message) => ({
          ...message,
          status: 'done',
          phase: 'error',
          content: message.content || event.text || 'An error occurred.',
        }))
        pushEvent({
          kind: 'status',
          icon: 'status',
          label: 'Error',
          detail: event.text,
        })
        return
      }
    }
  }

  return (
    <div className="h-screen overflow-hidden bg-[#0a0b0d] text-foreground">
      <div className="flex h-full">
        <aside
          className={cn(
            'border-r border-white/10 bg-[#111214]/95 transition-all duration-300',
            sidebarOpen ? 'w-[320px]' : 'w-0 overflow-hidden',
          )}
        >
          <div className="flex h-full flex-col">
            <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
              <div className="flex items-center gap-3">
                <div className="rounded-2xl bg-white/5 p-2 text-blue-300">
                  <Waves className="h-5 w-5" />
                </div>
                <div>
                  <div className="text-sm font-semibold">Nimbus Chat</div>
                  <div className="text-xs text-muted-foreground">A2A orchestrated workspace</div>
                </div>
              </div>
              <Badge variant="outline" className="border-white/10 bg-white/5 text-[10px] uppercase tracking-[0.2em] text-zinc-300">
                Beta
              </Badge>
            </div>

            <div className="space-y-5 overflow-y-auto p-5">
              <div>
                <Button onClick={createConversation} className="w-full gap-2" variant="default">
                  <Plus className="h-4 w-4" />
                  New chat
                </Button>
              </div>

              {conversations.length > 1 || (conversations[0]?.messages.length ?? 0) > 0 ? (
                <div className="space-y-1">
                  <div className="px-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    Conversations
                  </div>
                  {conversations.map((conv) => (
                    <div
                      key={conv.id}
                      className={cn(
                        'group flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-left transition',
                        conv.id === activeConversationId
                          ? 'border border-white/10 bg-white/[0.06]'
                          : 'border border-transparent hover:bg-white/[0.03]',
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => selectConversation(conv.id)}
                        className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
                      >
                        <MessageSquare className={cn('h-4 w-4 shrink-0', conv.id === activeConversationId ? 'text-blue-300' : 'text-muted-foreground')} />
                        <span className={cn('truncate text-[13px]', conv.id === activeConversationId ? 'font-medium text-zinc-100' : 'text-zinc-300')}>
                          {conv.title}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteConversation(conv.id)}
                        className="shrink-0 rounded-md p-1 text-muted-foreground opacity-0 transition hover:bg-white/10 hover:text-red-300 group-hover:opacity-100"
                        title="Delete conversation"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col bg-[radial-gradient(circle_at_top,_rgba(58,91,191,0.18),_transparent_28%),linear-gradient(180deg,#0a0b0d_0%,#111827_100%)]">
          <header className="flex items-center justify-between border-b border-white/10 px-4 py-3 md:px-6">
            <div className="flex items-center gap-3">
              <Button variant="ghost" size="icon" className="rounded-2xl border border-white/10 bg-white/5" onClick={() => setSidebarOpen((current) => !current)}>
                <PanelLeft className="h-4 w-4" />
              </Button>
              <div>
                <div className="text-sm font-semibold">Nimbus Chat</div>
                <div className="text-xs text-muted-foreground">
                  {clientStatus === 'ready' ? 'Connected to orchestrator' : clientStatus === 'connecting' ? 'Connecting…' : 'Connection error'}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="border-white/10 bg-white/5 text-zinc-200">
                {env.orchestratorBaseUrl}
              </Badge>
              <button
                type="button"
                onClick={() => setShowSpecialistModal(true)}
                className="flex items-center gap-1.5 rounded-full border border-emerald-400/20 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-200 transition hover:bg-emerald-500/20"
              >
                <Briefcase className="h-3.5 w-3.5" />
                {activeSpecialistCount} specialist{activeSpecialistCount === 1 ? '' : 's'}
              </button>
            </div>
          </header>

          <section className="flex min-h-0 flex-1 flex-col">
            <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
              {!messages.length ? (
                <div className="mx-auto flex max-w-3xl flex-col items-center gap-8 pt-16 text-center">
                  <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="space-y-5">
                    <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-3xl border border-white/10 bg-white/5 text-blue-300 shadow-2xl shadow-blue-500/10">
                      <Sparkles className="h-8 w-8" />
                    </div>
                    <div className="space-y-3">
                      <h1 className="text-4xl font-semibold tracking-tight md:text-5xl">Ask anything. Route intelligently.</h1>
                      <p className="max-w-2xl text-sm text-muted-foreground md:text-base">
                        A ChatGPT-like workspace powered by an A2A orchestrator that can stream directly or route to specialist agents.
                      </p>
                    </div>
                  </motion.div>

                  <div className="grid w-full max-w-3xl gap-3 md:grid-cols-2">
                    {starterPrompts.map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        onClick={() => void handleSendMessage(prompt)}
                        className="rounded-3xl border border-white/10 bg-white/[0.04] p-5 text-left transition hover:border-blue-400/30 hover:bg-white/[0.06]"
                      >
                        <div className="flex items-start gap-4">
                          <div className="rounded-2xl bg-white/5 p-2 text-blue-300">
                            <Globe className="h-4 w-4" />
                          </div>
                          <div>
                            <div className="text-sm font-medium text-foreground">{prompt}</div>
                            <p className="mt-1 text-xs text-muted-foreground">Click to send this prompt to the orchestrator.</p>
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="mx-auto flex w-full max-w-3xl flex-col gap-8">
                  {messages.map((message) => (
                    <motion.div
                      key={message.id}
                      initial={{ opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      className={cn('flex gap-4', message.role === 'assistant' ? 'justify-start' : 'justify-end')}
                    >
                      {message.role === 'assistant' ? (
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-blue-500/15 text-blue-200">
                          <Bot className="h-5 w-5" />
                        </div>
                      ) : null}

                      <div className={cn('min-w-0', message.role === 'assistant' ? 'flex-1' : 'max-w-[85%]')}>
                        {message.role === 'assistant' ? (
                          <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                            <Bot className="h-3.5 w-3.5" />
                            <span className="font-medium text-zinc-300">Nimbus</span>
                          </div>
                        ) : null}

                        {message.role === 'assistant' && (message.events?.length || message.phase !== 'idle') ? (
                          <ActivityTrail message={message} />
                        ) : null}

                        {message.role === 'assistant' ? (
                          <div className="min-h-[1.5rem]">
                            {message.content ? (
                              <Markdown content={message.content} />
                            ) : message.status === 'streaming' ? (
                              <div className="flex items-center gap-2 py-1 text-sm text-muted-foreground">
                                <span className="flex gap-1">
                                  <span className="h-2 w-2 animate-bounce rounded-full bg-blue-400 [animation-delay:-0.3s]" />
                                  <span className="h-2 w-2 animate-bounce rounded-full bg-blue-400 [animation-delay:-0.15s]" />
                                  <span className="h-2 w-2 animate-bounce rounded-full bg-blue-400" />
                                </span>
                                <span className="text-[13px]">Thinking…</span>
                              </div>
                            ) : null}
                            {message.status === 'streaming' && message.content ? (
                              <span className="ml-0.5 inline-block h-4 w-[3px] animate-pulse rounded-full bg-blue-400 align-middle" />
                            ) : null}
                          </div>
                        ) : (
                          <div className="rounded-[24px] bg-blue-500 px-5 py-3.5 text-sm leading-7 text-white shadow-xl">
                            <div className="mb-1.5 flex items-center justify-end gap-2 text-xs opacity-80">
                              <User className="h-3.5 w-3.5" />
                              <span>You</span>
                            </div>
                            <div className="whitespace-pre-wrap">{message.content}</div>
                          </div>
                        )}
                      </div>

                      {message.role === 'user' ? (
                        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-white/10 text-zinc-100">
                          <User className="h-5 w-5" />
                        </div>
                      ) : null}
                    </motion.div>
                  ))}
                </div>
              )}
            </div>

            <div className="px-4 py-4 md:px-8">
              <div className="mx-auto flex max-w-3xl flex-col gap-4">
                <div className="rounded-[28px] border border-white/10 bg-white/[0.04] p-3 shadow-2xl shadow-black/20">
                  <Textarea
                    value={input}
                    onChange={(event) => setInput(event.target.value)}
                    placeholder="Message Nimbus Chat…"
                    className="min-h-[84px] resize-none border-0 bg-transparent px-3 py-2 text-base focus-visible:ring-0"
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault()
                        void handleSendMessage()
                      }
                    }}
                  />
                  <div className="mt-3 flex items-center justify-between gap-3 px-2 pb-1">
                    <div className="text-xs text-muted-foreground">
                      {clientStatus === 'error'
                        ? clientError || 'Unable to reach the orchestrator.'
                        : 'Responses can stream directly from the orchestrator or a routed specialist.'}
                    </div>
                    <Button
                      onClick={() => void handleSendMessage()}
                      disabled={!input.trim() || isStreaming || clientStatus !== 'ready'}
                      className="rounded-2xl px-4"
                    >
                      {isStreaming ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : <SendHorizonal className="mr-2 h-4 w-4" />}
                      Send
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </main>
      </div>

      {/* Specialist management modal */}
      <AnimatePresence>
        {showSpecialistModal ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
            onClick={() => setShowSpecialistModal(false)}
          >
            <motion.div
              initial={{ scale: 0.96, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.96, opacity: 0 }}
              transition={{ duration: 0.15 }}
              onClick={(e) => e.stopPropagation()}
              className="flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-3xl border border-white/10 bg-[#131418] shadow-2xl"
            >
              <div className="flex items-center justify-between border-b border-white/10 px-6 py-4">
                <div className="flex items-center gap-3">
                  <div className="rounded-xl bg-blue-500/10 p-2 text-blue-300">
                    <Briefcase className="h-4 w-4" />
                  </div>
                  <div>
                    <div className="text-sm font-semibold">Manage specialists</div>
                    <div className="text-xs text-muted-foreground">Register and refresh specialist agents</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setShowSpecialistModal(false)}
                  className="rounded-lg p-1.5 text-muted-foreground transition hover:bg-white/10 hover:text-zinc-100"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="flex-1 space-y-5 overflow-y-auto p-6">
                {/* Registration form */}
                <div>
                  <div className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    Register new specialist
                  </div>
                  <div className="space-y-3">
                    <Input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="Specialist name" />
                    <Input value={form.url} onChange={(event) => setForm((current) => ({ ...current, url: event.target.value }))} placeholder="http://localhost:8001" />
                    <Textarea value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} placeholder="Describe the specialist" className="min-h-20" />
                    <Input value={form.tags} onChange={(event) => setForm((current) => ({ ...current, tags: event.target.value }))} placeholder="travel, itinerary, budget" />
                    <Textarea value={form.notes} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} placeholder="Optional notes" className="min-h-16" />

                    <Button className="w-full gap-2" onClick={handleRegisterSpecialist} disabled={isRegistering}>
                      {isRegistering ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                      Register specialist
                    </Button>

                    {registrationError ? <p className="text-xs text-red-300">{registrationError}</p> : null}
                    {registrationSuccess ? <p className="text-xs text-emerald-300">{registrationSuccess}</p> : null}
                  </div>
                </div>

                {/* Registered specialists */}
                <div>
                  <div className="mb-3 flex items-center justify-between">
                    <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Registered
                    </div>
                    <Badge variant="secondary" className="bg-white/5 text-zinc-200">
                      {activeSpecialistCount}
                    </Badge>
                  </div>

                  <div className="space-y-3">
                    {specialists.map((specialist) => (
                      <Card key={specialist.id} className="border-white/10 bg-white/[0.03] p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <div className="rounded-lg bg-white/5 p-1.5 text-blue-300">
                                <Plane className="h-3.5 w-3.5" />
                              </div>
                              <div className="text-sm font-medium">{specialist.name}</div>
                            </div>
                            <p className="mt-1.5 text-xs text-muted-foreground">{specialist.description}</p>
                          </div>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 shrink-0 rounded-xl border border-white/10 bg-white/5 text-zinc-300"
                            onClick={() => void handleRefreshSpecialist(specialist)}
                            disabled={refreshingSpecialistId === specialist.id}
                            title="Refresh specialist agent card"
                          >
                            <RefreshCw className={cn('h-3.5 w-3.5', refreshingSpecialistId === specialist.id && 'animate-spin')} />
                          </Button>
                        </div>
                        <div className="mt-2 text-[11px] text-muted-foreground">
                          Card refreshed {new Date(specialist.card_refreshed_at).toLocaleString()}
                        </div>
                        {specialist.tags.length ? (
                          <div className="mt-2.5 flex flex-wrap gap-1.5">
                            {specialist.tags.map((tag) => (
                              <Badge key={tag} variant="outline" className="border-white/10 bg-white/5 text-zinc-300">
                                {tag}
                              </Badge>
                            ))}
                          </div>
                        ) : null}
                      </Card>
                    ))}

                    {!specialists.length ? (
                      <Card className="border-dashed border-white/10 bg-white/[0.02] p-6 text-center text-sm text-muted-foreground">
                        No specialists registered yet.
                      </Card>
                    ) : null}
                  </div>
                </div>
              </div>
            </motion.div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}
