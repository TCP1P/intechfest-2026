// Report the rCTF scoreboard after the stress run. Reads "name<TAB>score"
// lines on stdin (from psql) and summarizes elite vs mediocre separation.
import { readFileSync } from 'node:fs'

const ELITE = Number(process.env.ELITE || 20)
const isElite = name => {
  const m = name.match(/^bot(\d+)$/)
  return m && Number(m[1]) < ELITE
}
const rows = readFileSync(0, 'utf8')
  .split('\n')
  .map(l => l.trim())
  .filter(Boolean)
  .map(l => {
    const [name, score] = l.split('|').map(s => s.trim())
    return { name, score: Number(score) }
  })
  .filter(r => /^bot\d+$/.test(r.name))
  .sort((a, b) => b.score - a.score)

console.log(`\n=== rCTF SCOREBOARD (bot teams) — ${rows.length} teams ===`)
console.log('rank  team    tier      score')
console.log('----  ------  --------  -----')
const show = [...rows.slice(0, 10), null, ...rows.slice(-5)]
let prev = 0
for (const r of show) {
  if (!r) { console.log('  ..'); continue }
  prev++
  const rank = rows.indexOf(r) + 1
  console.log(`${String(rank).padStart(3)}.  ${r.name.padEnd(6)}  ${(isElite(r.name) ? 'ELITE' : 'mediocre').padEnd(8)}  ${String(r.score).padStart(5)}`)
}

const e = rows.filter(r => isElite(r.name))
const m = rows.filter(r => !isElite(r.name))
const avg = a => a.length ? (a.reduce((s, r) => s + r.score, 0) / a.length).toFixed(1) : 'n/a'
console.log(`\nELITE   (${e.length}): avg score ${avg(e)}`)
console.log(`mediocre(${m.length}): avg score ${avg(m)}`)
console.log(`separation: ${(avg(e) - avg(m)).toFixed(1)} pts`)
const top10 = rows.slice(0, 10).filter(r => isElite(r.name)).length
console.log(`elite teams in top 10: ${top10}/10`)
