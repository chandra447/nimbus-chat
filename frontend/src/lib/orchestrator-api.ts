export interface SpecialistSummary {
  id: string
  name: string
  url: string
  description: string
  tags: string[]
  notes: string
  skills: Array<{
    id?: string
    name?: string
    description?: string
    tags?: string[]
    examples?: string[]
  }>
  created_at: string
  updated_at: string
  card_refreshed_at: string
}

export interface SpecialistRegistrationPayload {
  name: string
  url: string
  description?: string
  tags?: string[]
  notes?: string
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `Request failed with ${response.status}`)
  }

  return response.json() as Promise<T>
}

export async function listSpecialists(baseUrl: string) {
  const response = await fetch(`${baseUrl}/api/orchestrator/specialists`)
  return parseJson<SpecialistSummary[]>(response)
}

export async function refreshSpecialist(baseUrl: string, specialistId: string) {
  const response = await fetch(
    `${baseUrl}/api/orchestrator/specialists/${specialistId}/refresh`,
    { method: 'POST' },
  )

  return parseJson<{
    specialist: SpecialistSummary
    refreshed_agent_card: Record<string, unknown>
  }>(response)
}

export async function registerSpecialist(
  baseUrl: string,
  payload: SpecialistRegistrationPayload,
) {
  const response = await fetch(`${baseUrl}/api/orchestrator/specialists`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  return parseJson<{
    specialist: SpecialistSummary
    fetched_agent_card: Record<string, unknown>
  }>(response)
}
