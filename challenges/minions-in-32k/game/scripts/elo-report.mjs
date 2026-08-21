// Render the Elo/scoring demo results from the snapshot file.
import { readFileSync } from 'node:fs'

const N_ELITE = 10
const snaps = JSON.parse(readFileSync('replays/elo-demo-snapshots.json', 'utf8'))
const last = snaps[snaps.length - 1]
const eliteId = id => Number(id.split('-')[1]) < N_ELITE

const rows = [...last.rows].sort((a, b) => b.rating - a.rating)
const w = s => String(s)

console.log(`\n=== FINAL LEADERBOARD — ${last.finished} games, ${last.voided} voided ===`)
console.log('rank  team      tier      rating   score   Δrating')
console.log('----  --------  --------  -------  ------  -------')
rows.forEach((r, i) => {
  const d = (r.rating - 1500).toFixed(1)
  const dd = d >= 0 ? `+${d}` : d
  console.log(
    `${w(i + 1).padStart(3)}.  ${r.team_uuid.padEnd(8)}  ` +
      `${(eliteId(r.team_uuid) ? 'ELITE' : 'mediocre').padEnd(8)}  ` +
      `${r.rating.toFixed(1).padStart(7)}  ${w(r.score).padStart(5)}   ${dd.padStart(7)}`
  )
})

const elite = rows.filter(r => eliteId(r.team_uuid))
const med = rows.filter(r => !eliteId(r.team_uuid))
const avg = a => (a.reduce((s, r) => s + r.rating, 0) / a.length).toFixed(1)
const avgS = a => (a.reduce((s, r) => s + r.score, 0) / a.length).toFixed(0)
console.log(`\n=== TIER AVERAGES ===`)
console.log(`ELITE   (${elite.length}): avg rating ${avg(elite)}  avg score ${avgS(elite)}`)
console.log(`mediocre(${med.length}): avg rating ${avg(med)}  avg score ${avgS(med)}`)
console.log(`separation: ${(avg(elite) - avg(med)).toFixed(1)} rating pts`)

// zero-sum check: pairwise Elo conserves total rating around 1500*N
const total = rows.reduce((s, r) => s + r.rating, 0)
console.log(`\nsum(rating)=${total.toFixed(1)} vs baseline ${1500 * rows.length} (Elo is ~zero-sum)`)

// progression: rating trajectory for the top elite and bottom mediocre team
const track = [rows[0].team_uuid, rows[rows.length - 1].team_uuid]
console.log(`\n=== RATING PROGRESSION (every ~5th snapshot) ===`)
const xs = snaps.filter((_, i) => i % 5 === 0 || i === snaps.length - 1)
for (const id of track) {
  const series = xs.map(s => {
    const row = s.rows.find(r => r.team_uuid === id)
    return row ? Math.round(row.rating) : 1500
  })
  console.log(`${id.padEnd(8)} ${eliteId(id) ? '(ELITE)   ' : '(mediocre)'} ${series.join(' -> ')}`)
}
console.log()
