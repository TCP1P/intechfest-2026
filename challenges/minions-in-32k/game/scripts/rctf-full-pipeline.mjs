// Full pipeline through rCTF's own API surface:
//   register -> rCTF instancer (PUT/GET instance) -> backend queue -> match
//   -> Elo -> signed publish -> rCTF scoreboard.
// No direct backend calls; everything goes through rCTF like a real player.
import { spawn } from 'node:child_process'

const RCTF = 'http://127.0.0.1:3000/api'
const HEADLESS = 'target/release/headless-client'
const sleep = ms => new Promise(r => setTimeout(r, ms))

const players = [
  { name: 'alice', email: 'alice@example.com', elite: true },
  { name: 'bob', email: 'bob@example.com', elite: false },
]

const api = async (path, { token, method = 'GET', body } = {}) => {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const r = await fetch(`${RCTF}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, json: await r.json().catch(() => null) }
}

async function main() {
  // 1) register each player through rCTF
  for (const p of players) {
    const r = await api('/v2/auth/register', {
      method: 'POST',
      body: { name: p.name, email: p.email },
    })
    if (r.json?.kind === 'goodRegisterV2') {
      p.token = r.json.data.authToken
      console.log(`registered ${p.name}`)
    } else {
      // already exists from a prior run — recover the login token via... skip: fail loud
      console.log(`register ${p.name}: ${r.json?.kind} ${r.json?.message || ''}`)
    }
  }
  const live = players.filter(p => p.token)
  if (live.length < 2) {
    console.log('need 2 fresh registrations; some names may already be taken. aborting.')
    return
  }

  // 2) start an instance (join queue) via the rCTF instancer endpoint
  for (const p of live) {
    const r = await api('/v2/integrations/challs/minions/instance', {
      token: p.token,
      method: 'PUT',
      body: {},
    })
    console.log(`${p.name} queue: ${r.json?.data?.status ?? r.json?.kind}`)
  }

  // 3) poll instance status; when running, spawn a bot from the connect string
  const spawned = new Set()
  for (let i = 0; i < 40; i++) {
    let running = 0
    for (const p of live) {
      const r = await api('/v2/integrations/challs/minions/instance', { token: p.token })
      const d = r.json?.data
      if (d?.status === 'running') {
        running++
        if (!spawned.has(p.name)) {
          spawned.add(p.name)
          const ep = (d.endpoints || []).find(e => (e.text || '').startsWith('client --remote'))
          const m = ep && ep.text.match(/--remote (\S+) --key (\S+)/)
          if (m) {
            const args = ['--remote', m[1], '--name', p.name, '--count', '1', '--keys', m[2]]
            if (p.elite) args.push('--elite-count', '1', '--elite-name', p.name)
            spawn(HEADLESS, args, { stdio: 'ignore' }).unref()
            console.log(`spawned bot ${p.name} ${p.elite ? '(elite)' : '(mediocre)'} -> ${m[1]}`)
          }
        }
      }
    }
    if (running === live.length && spawned.size === live.length) break
    await sleep(1000)
  }

  // 4) wait for match end -> report -> publish
  console.log('match running; waiting for finish + publish...')
  await sleep(32000)

  // 5) read scores back through rCTF (the player-facing self endpoint)
  for (const p of live) {
    const r = await api('/v2/users/me', { token: p.token })
    console.log(`${p.name}: rCTF score = ${r.json?.data?.score ?? '?'}`)
  }
}
main()
