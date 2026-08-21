// Elo/scoring demo orchestrator.
// Drives N synthetic teams against the matchmaking backend, spawns a headless
// client per matched team (elite or mediocre profile), re-queues teams as
// matches finish, and snapshots the leaderboard until TARGET games complete.
import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'

const BACKEND = 'http://127.0.0.1:8080'
const HEADLESS = 'target/release/headless-client'
const N_TEAMS = 30
const N_ELITE = 10 // teams 0..9 run the elite aimbot profile
const TARGET = 50 // finished matches to reach
const SNAP = 'replays/elo-demo-snapshots.json'

const teams = Array.from({ length: N_TEAMS }, (_, i) => ({
  id: `team-${String(i).padStart(2, '0')}`,
  elite: i < N_ELITE,
  lastMatch: null, // match_id we've already spawned a bot for
}))

const sleep = ms => new Promise(r => setTimeout(r, ms))
const j = async (path, opts) => {
  try {
    const r = await fetch(`${BACKEND}${path}`, opts)
    return await r.json()
  } catch {
    return null
  }
}
const status = id => j(`/api/queue/status?team_uuid=${encodeURIComponent(id)}`)
const join = (id, name) =>
  j('/api/queue/join', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      team_uuid: id,
      name,
      challenge_integration_id: 'minions',
    }),
  })
const leaderboard = () => j('/api/leaderboard')

function spawnBot(team, remote, key) {
  const args = [
    '--remote', remote,
    '--name', team.id,
    '--count', '1',
    '--keys', key,
  ]
  if (team.elite) args.push('--elite-count', '1', '--elite-name', team.id)
  const p = spawn(HEADLESS, args, { stdio: 'ignore' })
  p.on('error', () => {})
  p.unref()
}

const snapshots = []
let lastFinished = -1

async function snapshot(tag) {
  const lb = await leaderboard()
  if (!lb) return 0
  if (lb.finished_matches !== lastFinished) {
    lastFinished = lb.finished_matches
    snapshots.push({ tag, finished: lb.finished_matches, voided: lb.voided_matches, rows: lb.rows })
    writeFileSync(SNAP, JSON.stringify(snapshots, null, 2))
    const top = (lb.rows[0] || {})
    console.log(
      `[${tag}] finished=${lb.finished_matches} voided=${lb.voided_matches} ` +
        `rated_teams=${lb.rows.length} top=${top.team_uuid || '-'}@${top.rating || '-'}`
    )
  }
  return lb.finished_matches
}

async function main() {
  console.log(`orchestrating ${N_TEAMS} teams (${N_ELITE} elite) -> ${TARGET} games`)
  let finished = 0
  let tick = 0
  while (finished < TARGET) {
    tick++
    for (const t of teams) {
      const s = await status(t.id)
      if (!s) continue
      if (s.state === 'idle') {
        // re-queue while we still want more games
        if (finished < TARGET) await join(t.id, t.id)
      } else if (s.state === 'matched') {
        if (t.lastMatch !== s.match_id) {
          t.lastMatch = s.match_id
          const native = (s.connection || []).find(
            c => c.startsWith('client --remote')
          )
          const m = native && native.match(/--remote (\S+) --key (\S+)/)
          if (m) spawnBot(t, m[1], m[2])
        }
      }
    }
    finished = await snapshot(`t${tick}`)
    await sleep(1500)
  }
  console.log('target reached; draining in-flight matches...')
  // let any in-flight matches report
  for (let i = 0; i < 12; i++) {
    await snapshot(`drain${i}`)
    await sleep(2500)
  }
  const lb = await leaderboard()
  writeFileSync(SNAP, JSON.stringify(snapshots, null, 2))
  console.log('DONE. final finished=', lb.finished_matches, 'voided=', lb.voided_matches)
}

main()
