// Scale test: register N teams through rCTF, run many matchmade rounds via the
// rCTF instancer endpoint (real path), publish scores live, until TARGET rounds.
// Caps concurrent active teams so match load stays ~4 servers at a time.
import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'

const RCTF = 'http://127.0.0.1:3000/api'
const BACKEND = 'http://127.0.0.1:8080'
const HEADLESS = 'target/release/headless-client'
const N = Number(process.env.N || 60)
const ELITE = Number(process.env.ELITE || 20) // first ELITE teams are elite
const TARGET = Number(process.env.TARGET || 60) // finished matches
const CAP = Number(process.env.CAP || 32) // max teams queued/in-match at once
const SNAP = 'replays/rctf-stress-snapshots.json'

const sleep = ms => new Promise(r => setTimeout(r, ms))
const teams = Array.from({ length: N }, (_, i) => ({
  name: `bot${String(i).padStart(2, '0')}`,
  email: `bot${String(i).padStart(2, '0')}@example.com`,
  elite: i < ELITE,
  token: null,
  lastKey: null,
  games: 0,
}))

const api = async (path, { token, method = 'GET', body } = {}) => {
  try {
    const headers = { 'Content-Type': 'application/json' }
    if (token) headers.Authorization = `Bearer ${token}`
    const r = await fetch(`${RCTF}${path}`, {
      method, headers, body: body ? JSON.stringify(body) : undefined,
    })
    return await r.json().catch(() => null)
  } catch { return null }
}
const backend = async path => {
  try { return await (await fetch(`${BACKEND}${path}`)).json() } catch { return null }
}
const INST = '/v2/integrations/challs/minions/instance'

async function register() {
  for (let i = 0; i < teams.length; i += 12) {
    const chunk = teams.slice(i, i + 12)
    await Promise.all(chunk.map(async t => {
      const r = await api('/v2/auth/register', {
        method: 'POST', body: { name: t.name, email: t.email },
      })
      if (r?.kind === 'goodRegisterV2') t.token = r.data.authToken
    }))
  }
  const ok = teams.filter(t => t.token).length
  console.log(`registered ${ok}/${N} teams`)
}

function spawnBot(t, remote, key) {
  const args = ['--remote', remote, '--name', t.name, '--count', '1', '--keys', key]
  if (t.elite) args.push('--elite-count', '1', '--elite-name', t.name)
  spawn(HEADLESS, args, { stdio: 'ignore' }).unref()
}

let lastFinished = -1
const snapshots = []
async function snapshot(tag) {
  const lb = await backend('/api/leaderboard')
  if (!lb) return 0
  if (lb.finished_matches !== lastFinished) {
    lastFinished = lb.finished_matches
    snapshots.push({ tag, finished: lb.finished_matches, voided: lb.voided_matches, rows: lb.rows })
    writeFileSync(SNAP, JSON.stringify(snapshots, null, 2))
    console.log(`[${tag}] finished=${lb.finished_matches} voided=${lb.voided_matches} rated=${lb.rows.length}`)
  }
  return lb.finished_matches
}

async function main() {
  const live = teams.filter(t => t.token)
  await register()
  const pool = teams.filter(t => t.token)
  if (pool.length < 2) { console.log('too few registrations'); return }

  let finished = 0
  let tick = 0
  while (finished < TARGET) {
    tick++
    // read all statuses in parallel
    const states = await Promise.all(pool.map(t => api(INST, { token: t.token })))
    let active = 0
    const idle = []
    for (let i = 0; i < pool.length; i++) {
      const t = pool[i]
      const st = states[i]?.data?.status
      if (st === 'running') {
        active++
        const ep = (states[i].data.endpoints || []).find(e => (e.text || '').startsWith('client --remote'))
        const m = ep && ep.text.match(/--remote (\S+) --key (\S+)/)
        if (m && t.lastKey !== m[2]) { t.lastKey = m[2]; t.games++; spawnBot(t, m[1], m[2]) }
      } else if (st === 'starting') {
        active++
      } else {
        idle.push(t)
      }
    }
    // queue idle teams up to the concurrency cap, fewest-played first so every
    // team gets matched evenly (only while more rounds wanted)
    if (finished < TARGET) {
      idle.sort((a, b) => a.games - b.games)
      for (const t of idle) {
        if (active >= CAP) break
        await api(INST, { token: t.token, method: 'PUT', body: {} })
        active++
      }
    }
    finished = await snapshot(`t${tick}`)
    await sleep(1500)
  }
  console.log('target reached; draining...')
  for (let i = 0; i < 10; i++) { await snapshot(`drain${i}`); await sleep(2500) }
  console.log('DONE')
}
main()
