/**
 * Plain SSE client for the orchestrator chat endpoint.
 *
 * The orchestrator is no longer an A2A server — the frontend is a normal React
 * app that POSTs `{ message, context_id }` to `/api/chat` and consumes a
 * Server-Sent Events stream of JSON events.
 *
 * Event types (emitted by the orchestrator's LangGraph driver):
 *  - status          : phase / lifecycle updates (routing, working, …)
 *  - token           : main-response token chunks (streamed markdown)
 *  - specialist_chunk: raw specialist artifact chunk (live, via push notification)
 *  - done            : terminal, carries the full final_response
 *  - error           : terminal failure
 */

export type ChatStreamEvent =
  | {
      type: 'status'
      phase: string
      text: string
      specialist_name?: string
      specialists?: Array<{ name: string; url: string }>
      needs_synthesis?: boolean
    }
  | { type: 'token'; text: string }
  | { type: 'specialist_chunk'; specialist_name: string; text: string }
  | { type: 'done'; final_response: string }
  | { type: 'error'; text: string }

export async function* streamChat(
  baseUrl: string,
  message: string,
  contextId: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const response = await fetch(`${baseUrl}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, context_id: contextId }),
    signal,
  })

  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => '')
    throw new Error(text || `Chat request failed: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      // Keep the last (possibly partial) line in the buffer.
      buffer = lines.pop() ?? ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed.startsWith('data: ')) continue
        const payload = trimmed.slice(6)
        if (!payload) continue
        try {
          yield JSON.parse(payload) as ChatStreamEvent
        } catch {
          // Ignore malformed lines (keep-alive comments, etc.).
        }
      }
    }
    // Flush any trailing line.
    const trimmed = buffer.trim()
    if (trimmed.startsWith('data: ')) {
      try {
        yield JSON.parse(trimmed.slice(6)) as ChatStreamEvent
      } catch {
        /* ignore */
      }
    }
  } finally {
    reader.releaseLock()
  }
}
