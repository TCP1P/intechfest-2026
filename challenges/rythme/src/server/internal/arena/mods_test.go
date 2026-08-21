package arena

import (
	"sort"
	"testing"
)

func TestPinnedNativeModProfiles(t *testing.T) {
	tests := []struct {
		name       string
		ruleset    Ruleset
		acronym    string
		rateMilli  int
		scoreMilli int
	}{
		{name: "normal", ruleset: RulesetOsu, acronym: "NM", rateMilli: 1000, scoreMilli: 1000},
		{name: "osu half time", ruleset: RulesetOsu, acronym: "HT", rateMilli: 750, scoreMilli: 550},
		{name: "osu difficulty stack", ruleset: RulesetOsu, acronym: "DT,FL,HD,HR", rateMilli: 1500, scoreMilli: 1673},
		{name: "osu blinds hidden override", ruleset: RulesetOsu, acronym: "BL,HD", rateMilli: 1000, scoreMilli: 1240},
		{name: "osu flashlight freeze override", ruleset: RulesetOsu, acronym: "FL,FR", rateMilli: 1000, scoreMilli: 1100},
		{name: "taiko difficulty stack", ruleset: RulesetTaiko, acronym: "DT,FL,HD,HR", rateMilli: 1500, scoreMilli: 1384},
		{name: "fruits difficulty stack", ruleset: RulesetFruits, acronym: "DT,FL,HD,HR", rateMilli: 1500, scoreMilli: 1463},
		{name: "mania double time", ruleset: RulesetMania, acronym: "DT", rateMilli: 1500, scoreMilli: 1000},
		{name: "mania four key double time", ruleset: RulesetMania, acronym: "4K,DT", rateMilli: 1500, scoreMilli: 900},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var requested []string
			if test.acronym != "NM" {
				requested = splitAcronyms(test.acronym)
			}
			applied, err := canonicalAppliedMods(test.ruleset, requested)
			if err != nil {
				t.Fatalf("valid pinned profile was rejected: %v", err)
			}
			if applied.Acronym != test.acronym ||
				applied.RateMilli != test.rateMilli ||
				applied.SettingsHash != defaultSettingsHash ||
				applied.ScoreMultiplierMilli != test.scoreMilli {
				t.Fatalf("server derived the wrong profile: %+v", applied)
			}
		})
	}
}

func TestNativeModSelectionRejectsInvalidRequests(t *testing.T) {
	tests := []struct {
		name      string
		ruleset   Ruleset
		requested []string
	}{
		{
			name:      "unknown mod",
			ruleset:   RulesetTaiko,
			requested: []string{"ZZ"},
		},
		{
			name:      "automation mod",
			ruleset:   RulesetOsu,
			requested: []string{"RX"},
		},
		{
			name:      "incompatible mods",
			ruleset:   RulesetOsu,
			requested: []string{"BL", "FL"},
		},
		{
			name:      "multiple mania key conversions",
			ruleset:   RulesetMania,
			requested: []string{"4K", "7K"},
		},
		{
			name:      "multiple rate mods",
			ruleset:   RulesetFruits,
			requested: []string{"DT", "HT"},
		},
		{
			name:      "duplicate mod",
			ruleset:   RulesetTaiko,
			requested: []string{"HD", "HD"},
		},
		{
			name:      "normal combination",
			ruleset:   RulesetOsu,
			requested: []string{"NM", "HD"},
		},
		{
			name:      "empty acronym",
			ruleset:   RulesetOsu,
			requested: []string{""},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := canonicalAppliedMods(test.ruleset, test.requested); err == nil {
				t.Fatal("invalid modifier request was accepted")
			}
		})
	}
}

func TestMaximumLegitimateNativeMultiplierStaysBelowCrownThreshold(t *testing.T) {
	const crownThresholdMultiplier = 2100
	expectedMaximum := map[Ruleset]int{
		RulesetOsu:    1673,
		RulesetTaiko:  1384,
		RulesetFruits: 1463,
		RulesetMania:  1000,
	}

	for _, ruleset := range AllRulesets {
		t.Run(string(ruleset), func(t *testing.T) {
			policy := nativeModsByRuleset[ruleset]
			positive := make([]string, 0)
			for acronym, mod := range policy {
				if mod.scoreMultiplier > 1 {
					positive = append(positive, acronym)
				}
			}
			if len(positive) > 20 {
				t.Fatalf("positive-mod search unexpectedly grew to %d entries", len(positive))
			}

			maximum := 1000
			maximumProfile := "NM"
			for selection := 1; selection < 1<<len(positive); selection++ {
				acronyms := make([]string, 0, len(positive))
				for index, acronym := range positive {
					if selection&(1<<index) != 0 {
						acronyms = append(acronyms, acronym)
					}
				}
				sort.Strings(acronyms)
				profile, err := canonicalAppliedMods(ruleset, acronyms)
				if err != nil {
					continue
				}
				if profile.ScoreMultiplierMilli > maximum {
					maximum = profile.ScoreMultiplierMilli
					maximumProfile = profile.Acronym
				}
			}

			if maximum >= crownThresholdMultiplier {
				t.Fatalf("%s reaches %0.3fx with legal profile %s", ruleset, float64(maximum)/1000, maximumProfile)
			}
			if maximum != expectedMaximum[ruleset] {
				t.Fatalf(
					"%s maximum changed: got %0.3fx with %s, expected %0.3fx",
					ruleset,
					float64(maximum)/1000,
					maximumProfile,
					float64(expectedMaximum[ruleset])/1000,
				)
			}
		})
	}
}

func splitAcronyms(value string) []string {
	var result []string
	start := 0
	for index := 0; index <= len(value); index++ {
		if index == len(value) || value[index] == ',' {
			result = append(result, value[start:index])
			start = index + 1
		}
	}
	return result
}
