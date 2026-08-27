// End-to-end test: backend match -> Elo -> signed publish -> rCTF score.
// Drives the backend directly with the real rCTF user UUIDs as team ids.
import { spawn } from 'node:child_process'

const BACKEND = 'http://127.0.0.1:8080'
const HEADLESS = 'target/release/headless-client'
// real rCTF users (id = team_uuid the backend will publish as userId)
const teams = [
  { id: '72424c70-bbb3-47be-aae3-f1160d7d1d3c', name: 'minion1', elite: true },
  { id: 'f39c92b9-b669-45fd-abc3-238d2ec634ca', name: 'minion2', elite: false },
]
const sleep = ms => new Promise(r => setTimeout(r, ms))
const j = async (p, o) => {
  try { return await (await fetch(`${BACKEND}${p}`, o)).json() } catch { return null }
}
const status = id => j(`/api/queue/status?team_uuid=${encodeURIComponent(id)}`)

async function main() {
  for (const t of teams) {
    await j('/api/queue/join', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ team_uuid: t.id, name: t.name, challenge_integration_id: 'minions' }),
    })
    console.log('queued', t.name)
  }
  // poll until both matched, spawn a bot each
  const spawned = new Set()
  for (let i = 0; i < 30; i++) {
    let matched = 0
    for (const t of teams) {
      const s = await status(t.id)
      if (s && s.state === 'matched') {
        matched++
        if (!spawned.has(t.id)) {
          spawned.add(t.id)
          const native = (s.connection || []).find(c => c.startsWith('client --remote'))
          const m = native && native.match(/--remote (\S+) --key (\S+)/)
          if (m) {
            const args = ['--remote', m[1], '--name', t.name, '--count', '1', '--keys', m[2]]
            if (t.elite) args.push('--elite-count', '1', '--elite-name', t.name)
            spawn(HEADLESS, args, { stdio: 'ignore' }).unref()
            console.log('spawned bot', t.name, t.elite ? '(elite)' : '(mediocre)', '->', m[1])
          }
        }
      }
    }
    if (matched === teams.length && spawned.size === teams.length) break
    await sleep(1000)
  }
  console.log('waiting for match to finish + report + publish...')
  await sleep(30000)
  const lb = await j('/api/leaderboard')
  console.log('\nbackend ratings:', JSON.stringify(lb))
}
main()
