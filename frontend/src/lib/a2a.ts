import { ClientFactory, type Client } from '@a2a-js/sdk/client'

import { env } from '@/lib/env'

const clientFactory = new ClientFactory()

let orchestratorClientPromise: Promise<Client> | null = null

export function getOrchestratorClient() {
  if (!orchestratorClientPromise) {
    orchestratorClientPromise = clientFactory.createFromUrl(env.orchestratorBaseUrl)
  }

  return orchestratorClientPromise
}
