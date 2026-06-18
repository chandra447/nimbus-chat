import type {
  Message,
  Part,
  StreamResponse,
  TaskState,
} from '@a2a-js/sdk'

export function partToText(part: Part) {
  if (!part.content) return ''

  switch (part.content.$case) {
    case 'text':
      return part.content.value
    case 'data':
      return JSON.stringify(part.content.value)
    case 'url':
      return part.content.value
    default:
      return ''
  }
}

export function messageToText(message?: Message) {
  if (!message) return ''
  return message.parts.map(partToText).join('')
}

export function streamEventType(event: StreamResponse) {
  return event.payload?.$case ?? 'unknown'
}

export function taskStateLabel(state?: TaskState) {
  const labels: Record<number, string> = {
    0: 'Unknown',
    1: 'Submitted',
    2: 'Working',
    3: 'Completed',
    4: 'Failed',
    5: 'Canceled',
    6: 'Input required',
    7: 'Rejected',
    8: 'Auth required',
  }

  if (typeof state !== 'number') return 'Unknown'
  return labels[state] ?? 'Unknown'
}
