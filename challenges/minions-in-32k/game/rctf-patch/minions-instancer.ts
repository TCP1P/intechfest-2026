import { ExposeKind } from '@rctf/types'
import * as z from 'zod/mini'
import {
  InstanceStatus,
  type CreateInstanceOptions,
  type ExtendInstanceOptions,
  type instanceDetailsOrError,
  type InstanceQueryOptions,
  type instancerActionOutcome,
  type InstancerProvider,
  type ProviderConfig,
} from './base'

const statusSchema = z.object({
  state: z.string(),
  connection: z.optional(z.array(z.string())),
  ends_in_secs: z.optional(z.number()),
})

type QueueStatus = z.output<typeof statusSchema>

const stoppedDetails = (): instanceDetailsOrError => ({
  kind: 'instancerInstanceDetails',
  status: InstanceStatus.STOPPED,
  timeLeftMilliseconds: 0,
  endpoints: [],
})

const misconfigured = () => ({
  kind: 'instancerError' as const,
  message: 'Instancer is missing a valid apiUrl in the challenge config',
})

const unreachable = () => ({
  kind: 'instancerError' as const,
  message: 'Failed to reach the instancer',
})

const raw = (title: string, text: string) => ({
  kind: ExposeKind.RAW,
  host: '',
  port: 0,
  title,
  text,
  bypassExpose: true,
})

export default class MinionsInstancerProvider implements InstancerProvider {
  readonly configSchema = z.object({
    apiUrl: z.string().check(z.regex(/^https?:\/\//, 'Must be an http(s) URL')),
    udprouterAddr: z.string(),
    pods: z.array(z.unknown()),
    queueSecret: z.optional(z.string()),
    // Event window forwarded to the backend, which refuses to serve any
    // instance that wouldn't finish before endDate (and won't serve before
    // startDate). RFC3339 (`2026-07-01T00:00:00Z`) or unix seconds; absent →
    // unconstrained on that side.
    startDate: z.optional(z.string()),
    endDate: z.optional(z.string()),
  })
  readonly capabilities = { canStop: false, canExtend: false }
  readonly actions = [
    {
      id: 'leave',
      label: 'Leave',
      rateLimit: { burst: 5, intervalMilliseconds: 10_000 },
    },
  ]

  constructor(_options: unknown) {}

  getDefaults = (): ProviderConfig => ({
    apiUrl: '',
    udprouterAddr: '',
    pods: [],
    queueSecret: '',
    startDate: '',
    endDate: '',
  })

  private resolveApiUrl(config: Record<string, unknown>): string | null {
    const apiUrl = config.apiUrl
    if (typeof apiUrl !== 'string' || apiUrl.length === 0) {
      return null
    }
    return apiUrl.replace(/\/$/, '')
  }

  private authHeaders(config: Record<string, unknown>): Record<string, string> {
    const queueSecret = config.queueSecret
    if (typeof queueSecret !== 'string' || queueSecret.length === 0) {
      return {}
    }
    return { Authorization: `Bearer ${queueSecret}` }
  }

  private async request(
    apiUrl: string,
    path: string,
    init: RequestInit
  ): Promise<{ status: number; body: unknown } | null> {
    try {
      const response = await fetch(`${apiUrl}${path}`, init)
      const body = await response.json().catch(() => undefined)
      return { status: response.status, body }
    } catch (err) {
      console.error('Failed to reach the minions instancer', err)
      return null
    }
  }

  private toDetails(status: QueueStatus): instanceDetailsOrError {
    if (status.state === 'matched') {
      const connections = status.connection ?? []
      return {
        kind: 'instancerInstanceDetails',
        status: InstanceStatus.RUNNING,
        timeLeftMilliseconds: Math.max(
          0,
          Math.round((status.ends_in_secs ?? 0) * 1000)
        ),
        // A team can hold up to cap(t) concurrent matches; the status may
        // report several connection endpoints, one per live instance.
        endpoints: connections.map((cmd, i) =>
          raw(connections.length > 1 ? `Connect #${i + 1}` : 'Connect', cmd)
        ),
      }
    }
    if (status.state === 'queued') {
      return {
        kind: 'instancerInstanceDetails',
        status: InstanceStatus.STARTING,
        timeLeftMilliseconds: null,
        endpoints: [],
      }
    }
    return stoppedDetails()
  }

  createInstance = async (
    options: CreateInstanceOptions
  ): Promise<instanceDetailsOrError> => {
    const apiUrl = this.resolveApiUrl(options.config)
    if (!apiUrl) {
      return misconfigured()
    }

    const result = await this.request(apiUrl, '/api/queue/join', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...this.authHeaders(options.config),
      },
      body: JSON.stringify({
        team_uuid: options.user.id,
        name: options.user.name,
        config: options.config,
        challenge_integration_id: options.challengeIntegrationId,
      }),
    })
    if (!result) {
      return unreachable()
    }
    // 200 = newly queued/matched, 409 = already queued or already in a match.
    // With cap(t) concurrency a repeated join while matched is expected, so
    // 409 is not an error: fall through and surface the team's current state.
    if (result.status !== 200 && result.status !== 409) {
      const error = (result.body as { error?: unknown } | undefined)?.error
      return {
        kind: 'instancerError',
        message: typeof error === 'string' ? error : 'Failed to join the queue',
      }
    }

    return this.getInstance({
      teamId: options.user.id,
      challengeIntegrationId: options.challengeIntegrationId,
      config: options.config,
    })
  }

  getInstance = async (
    options: InstanceQueryOptions
  ): Promise<instanceDetailsOrError> => {
    const apiUrl = this.resolveApiUrl(options.config)
    if (!apiUrl) {
      return misconfigured()
    }

    const result = await this.request(
      apiUrl,
      `/api/queue/status?team_uuid=${encodeURIComponent(options.teamId)}`,
      { method: 'GET', headers: this.authHeaders(options.config) }
    )
    if (!result) {
      return unreachable()
    }
    if (result.status !== 200) {
      return { kind: 'instancerError', message: 'Instancer request failed' }
    }

    const parsed = statusSchema.safeParse(result.body)
    if (!parsed.success) {
      return {
        kind: 'instancerError',
        message: 'Received an invalid response from the instancer',
      }
    }
    return this.toDetails(parsed.data)
  }

  deleteInstance = async (
    _options: InstanceQueryOptions
  ): Promise<instanceDetailsOrError> => stoppedDetails()

  extendInstance = async (
    _options: ExtendInstanceOptions
  ): Promise<instanceDetailsOrError> => ({
    kind: 'instancerError',
    message: 'This instancer does not support extending instances',
  })

  runAction = async (
    actionId: string,
    options: InstanceQueryOptions
  ): Promise<instancerActionOutcome> => {
    if (actionId !== 'leave') {
      return { kind: 'instancerError', message: 'Unknown action' }
    }

    const apiUrl = this.resolveApiUrl(options.config)
    if (!apiUrl) {
      return misconfigured()
    }

    const result = await this.request(apiUrl, '/api/queue/leave', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...this.authHeaders(options.config),
      },
      body: JSON.stringify({ team_uuid: options.teamId }),
    })
    if (!result) {
      return unreachable()
    }
    return { kind: 'instancerActionResult', message: 'Left the queue' }
  }
}
