package main

import "errors"

const (
	integrityClean           = "clean"
	integrityAutomatedCombat = "automated_combat"
)

func validIntegrityStatus(value string) bool {
	return value == integrityClean || value == integrityAutomatedCombat
}

func scoringIntegrity(value string) bool {
	// Empty belongs only to persisted waves written before integrity evidence
	// was introduced. New reports must carry an explicit status.
	return value == "" || value == integrityClean
}

func mergeIntegrityStatus(current, next string) string {
	if current == integrityAutomatedCombat || next == integrityAutomatedCombat {
		return integrityAutomatedCombat
	}
	return integrityClean
}

func (b *bridge) validateLegPrefixLocked(state *matchSequence, report matchReport) error {
	type counters struct {
		kills         uint64
		confirmations uint64
	}
	totals := make(map[string]counters, len(report.Players))
	for sequence := uint32(1); sequence < report.Sequence; sequence++ {
		key, exists := state.LegKeys[sequence]
		if !exists {
			return errors.New("match reports must arrive in sequence")
		}
		for _, player := range b.waves[key].Result.Players {
			value := totals[player.TeamUUID]
			value.kills += uint64(player.Kills)
			value.confirmations += uint64(player.ConfirmedKills)
			totals[player.TeamUUID] = value
		}
	}
	for _, player := range report.Players {
		value := totals[player.TeamUUID]
		value.kills += uint64(player.Kills)
		value.confirmations += uint64(player.ConfirmedKills)
		if value.confirmations > value.kills {
			return errors.New("confirmed-kill prefix exceeds authoritative frags")
		}
		totals[player.TeamUUID] = value
	}
	return nil
}
