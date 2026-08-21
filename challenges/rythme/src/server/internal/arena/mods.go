package arena

import (
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
)

// nativeMod describes the default, non-customised profile exposed by the
// pinned lazer Mod Select overlay. The values mirror osu! commit
// 0f3d3c3be19b134779879b3f18fe315da035c1ac.
type nativeMod struct {
	scoreMultiplier float64
	rateMilli       int
}

func fixedMod(scoreMultiplier float64) nativeMod {
	return nativeMod{scoreMultiplier: scoreMultiplier, rateMilli: 1000}
}

func rateMod(scoreMultiplier float64, rateMilli int) nativeMod {
	return nativeMod{scoreMultiplier: scoreMultiplier, rateMilli: rateMilli}
}

var nativeModsByRuleset = map[Ruleset]map[string]nativeMod{
	RulesetOsu: {
		"AC": fixedMod(1.00),
		"AD": fixedMod(0.70),
		"AL": fixedMod(1.00),
		"BL": fixedMod(1.24),
		"BM": fixedMod(1.00),
		"BR": fixedMod(1.00),
		"BU": fixedMod(1.00),
		"CL": fixedMod(0.985),
		"DA": fixedMod(1.00),
		"DC": rateMod(0.55, 750),
		"DF": fixedMod(1.00),
		"DP": fixedMod(1.00),
		"DT": rateMod(1.23, 1500),
		"EZ": fixedMod(0.80),
		"FL": fixedMod(1.20),
		"FR": fixedMod(1.00),
		"GR": fixedMod(1.00),
		"HD": fixedMod(1.04),
		"HR": fixedMod(1.09),
		"HT": rateMod(0.55, 750),
		"MG": fixedMod(0.40),
		"MR": fixedMod(1.00),
		"MU": fixedMod(1.00),
		"NC": rateMod(1.23, 1500),
		"NF": fixedMod(0.50),
		"NS": fixedMod(1.00),
		"PF": fixedMod(1.00),
		"RD": fixedMod(0.70),
		"RP": fixedMod(1.00),
		"SD": fixedMod(1.00),
		"SG": fixedMod(1.00),
		"SI": fixedMod(1.00),
		"ST": fixedMod(1.00),
		"SY": fixedMod(0.99),
		"TC": fixedMod(1.02),
		"TD": fixedMod(1.00),
		"TP": fixedMod(0.01),
		"TR": fixedMod(1.00),
		"WG": fixedMod(1.00),
	},
	RulesetTaiko: {
		"AC": fixedMod(1.00),
		"CL": fixedMod(1.00),
		"CS": fixedMod(0.90),
		"DA": fixedMod(0.50),
		"DC": rateMod(0.30, 750),
		"DT": rateMod(1.10, 1500),
		"EZ": fixedMod(0.50),
		"FL": fixedMod(1.12),
		"HD": fixedMod(1.06),
		"HR": fixedMod(1.06),
		"HT": rateMod(0.30, 750),
		"MU": fixedMod(1.00),
		"NC": rateMod(1.10, 1500),
		"NF": fixedMod(0.50),
		"PF": fixedMod(1.00),
		"RD": fixedMod(1.00),
		"SD": fixedMod(1.00),
		"SG": fixedMod(1.00),
		"SR": fixedMod(0.60),
		"SW": fixedMod(1.00),
	},
	RulesetFruits: {
		"AC": fixedMod(1.00),
		"DA": fixedMod(0.50),
		"DC": rateMod(0.30, 750),
		"DT": rateMod(1.10, 1500),
		"EZ": fixedMod(0.50),
		"FF": fixedMod(1.00),
		"FL": fixedMod(1.12),
		"HD": fixedMod(1.06),
		"HR": fixedMod(1.12),
		"HT": rateMod(0.30, 750),
		"MF": fixedMod(1.00),
		"MR": fixedMod(1.00),
		"MU": fixedMod(1.00),
		"NC": rateMod(1.10, 1500),
		"NF": fixedMod(0.50),
		"NS": fixedMod(1.00),
		"PF": fixedMod(1.00),
		"SD": fixedMod(1.00),
		"SY": fixedMod(0.80),
	},
	RulesetMania: {
		"10K": fixedMod(0.90),
		"1K":  fixedMod(0.90),
		"2K":  fixedMod(0.90),
		"3K":  fixedMod(0.90),
		"4K":  fixedMod(0.90),
		"5K":  fixedMod(0.90),
		"6K":  fixedMod(0.90),
		"7K":  fixedMod(0.90),
		"8K":  fixedMod(0.90),
		"9K":  fixedMod(0.90),
		"AC":  fixedMod(1.00),
		"CL":  fixedMod(1.00),
		"CO":  fixedMod(1.00),
		"CS":  fixedMod(0.90),
		"DA":  fixedMod(0.50),
		"DC":  rateMod(0.30, 750),
		"DS":  fixedMod(1.00),
		"DT":  rateMod(1.00, 1500),
		"EZ":  fixedMod(0.50),
		"FI":  fixedMod(1.00),
		"FL":  fixedMod(1.00),
		"HD":  fixedMod(1.00),
		"HO":  fixedMod(0.90),
		"HR":  fixedMod(1.00),
		"HT":  rateMod(0.30, 750),
		"IN":  fixedMod(1.00),
		"MR":  fixedMod(1.00),
		"MU":  fixedMod(1.00),
		"NC":  rateMod(1.00, 1500),
		"NF":  fixedMod(0.50),
		"NR":  fixedMod(0.90),
		"PF":  fixedMod(1.00),
		"RD":  fixedMod(1.00),
		"SD":  fixedMod(1.00),
	},
}

func pairSet(values ...string) map[string]struct{} {
	result := make(map[string]struct{}, len(values))
	for _, value := range values {
		result[value] = struct{}{}
	}
	return result
}

// These pairs come from ModUtils.CheckCompatibleSet() in the pinned ruleset
// assemblies after applying the same arena filter as RythmeModSelectOverlay.
var incompatibleNativeModPairs = map[Ruleset]map[string]struct{}{
	RulesetOsu: pairSet(
		"AC,EZ", "AC,NF", "AC,PF", "AD,DF", "AD,FR", "AD,GR", "AD,HD",
		"AD,SI", "AD,TP", "AL,SG", "BL,FL", "BM,FL", "BM,NS", "BM,TD",
		"BR,BU", "BU,MG", "BU,RP", "CL,ST", "DA,EZ", "DA,HR", "DA,TP",
		"DC,DT", "DC,HT", "DC,NC", "DF,DP", "DF,GR", "DF,SI", "DF,TC",
		"DP,FR", "DP,GR", "DP,HD", "DP,MG", "DP,RP", "DP,SI", "DP,TC",
		"DP,TP", "DP,TR", "DP,WG", "DT,HT", "DT,NC", "EZ,HR", "FR,HD",
		"FR,TR", "GR,SI", "GR,TC", "HD,SI", "HD,TC", "HR,MR", "HT,NC",
		"MG,RP", "MG,TR", "MG,WG", "NF,PF", "NF,SD", "PF,SD", "RD,TP",
		"RP,TR", "RP,WG", "SD,TP", "SI,TC", "ST,TP", "TC,TP", "TR,WG",
	),
	RulesetTaiko: pairSet(
		"AC,NF", "AC,PF", "DA,EZ", "DA,HR", "DC,DT", "DC,HT", "DC,NC",
		"DT,HT", "DT,NC", "EZ,HR", "HT,NC", "NF,PF", "NF,SD", "PF,SD",
		"RD,SW",
	),
	RulesetFruits: pairSet(
		"AC,EZ", "AC,NF", "AC,PF", "DA,EZ", "DA,HR", "DC,DT", "DC,HT",
		"DC,NC", "DT,HT", "DT,NC", "EZ,HR", "HT,NC", "NF,PF", "NF,SD",
		"PF,SD",
	),
	RulesetMania: pairSet(
		"10K,1K", "10K,2K", "10K,3K", "10K,4K", "10K,5K", "10K,6K",
		"10K,7K", "10K,8K", "10K,9K", "1K,2K", "1K,3K", "1K,4K",
		"1K,5K", "1K,6K", "1K,7K", "1K,8K", "1K,9K", "2K,3K",
		"2K,4K", "2K,5K", "2K,6K", "2K,7K", "2K,8K", "2K,9K",
		"3K,4K", "3K,5K", "3K,6K", "3K,7K", "3K,8K", "3K,9K",
		"4K,5K", "4K,6K", "4K,7K", "4K,8K", "4K,9K", "5K,6K",
		"5K,7K", "5K,8K", "5K,9K", "6K,7K", "6K,8K", "6K,9K",
		"7K,8K", "7K,9K", "8K,9K", "AC,EZ", "AC,NF", "AC,PF",
		"CO,FI", "CO,FL", "CO,HD", "DA,EZ", "DA,HR", "DC,DT", "DC,HT",
		"DC,NC", "DT,HT", "DT,NC", "EZ,HR", "FI,FL", "FI,HD", "FL,HD",
		"HO,IN", "HO,NR", "HT,NC", "IN,NR", "NF,PF", "NF,SD", "PF,SD",
	),
}

type nativeCombinationMultiplier struct {
	left       string
	right      string
	multiplier float64
}

// ScoreMultiplierCalculator consumes combination rules in declaration order.
// Only these compatible default-setting pairs differ from the product of their
// individual osu! multipliers.
var osuNativeCombinationMultipliers = []nativeCombinationMultiplier{
	{left: "BL", right: "HD", multiplier: 1.24},
	{left: "HD", right: "WG", multiplier: 1.02},
	{left: "GR", right: "HD", multiplier: 1.02},
	{left: "DF", right: "HD", multiplier: 1.02},
	{left: "HD", right: "RP", multiplier: 1.02},
	{left: "BL", right: "TC", multiplier: 1.24},
	{left: "FL", right: "FR", multiplier: 1.10},
}

func canonicalAppliedMods(ruleset Ruleset, requested []string) (AppliedModProfile, error) {
	policy, ok := nativeModsByRuleset[ruleset]
	if !ok {
		return AppliedModProfile{}, errors.New("unsupported ruleset")
	}
	if len(requested) == 0 {
		return AppliedModProfile{
			Acronym:              "NM",
			RateMilli:            1000,
			SettingsHash:         defaultSettingsHash,
			ScoreMultiplierMilli: 1000,
		}, nil
	}
	if len(requested) > len(policy) {
		return AppliedModProfile{}, errors.New("invalid modifier count")
	}

	parts := make([]string, len(requested))
	for index, raw := range requested {
		acronym := strings.ToUpper(strings.TrimSpace(raw))
		if acronym == "" {
			return AppliedModProfile{}, errors.New("modifier acronyms cannot be empty")
		}
		parts[index] = acronym
	}
	sort.Strings(parts)
	if len(parts) == 1 && parts[0] == "NM" {
		return canonicalAppliedMods(ruleset, nil)
	}

	expectedRate := 1000
	previous := ""
	for index, acronym := range parts {
		if acronym == "NM" {
			return AppliedModProfile{}, errors.New("Normal cannot be combined with another modifier")
		}
		mod, exists := policy[acronym]
		if !exists {
			return AppliedModProfile{}, fmt.Errorf(
				"modifier %q is not available for synchronized %s play",
				acronym,
				ruleset,
			)
		}
		if previous == acronym {
			return AppliedModProfile{}, errors.New("modifier acronyms must be unique")
		}
		previous = acronym

		for prior := 0; prior < index; prior++ {
			if _, incompatible := incompatibleNativeModPairs[ruleset][parts[prior]+","+acronym]; incompatible {
				return AppliedModProfile{}, fmt.Errorf(
					"modifiers %s and %s are incompatible",
					parts[prior],
					acronym,
				)
			}
		}

		if mod.rateMilli != 1000 {
			if expectedRate != 1000 {
				return AppliedModProfile{}, errors.New("select only one playback-rate modifier")
			}
			expectedRate = mod.rateMilli
		}
	}

	expectedMultiplier := nativeScoreMultiplierMilli(ruleset, parts)
	return AppliedModProfile{
		Acronym:              strings.Join(parts, ","),
		RateMilli:            expectedRate,
		SettingsHash:         defaultSettingsHash,
		ScoreMultiplierMilli: expectedMultiplier,
	}, nil
}

func nativeScoreMultiplierMilli(ruleset Ruleset, acronyms []string) int {
	remaining := make(map[string]bool, len(acronyms))
	for _, acronym := range acronyms {
		remaining[acronym] = true
	}

	multiplier := 1.0
	if ruleset == RulesetOsu {
		for _, combination := range osuNativeCombinationMultipliers {
			if remaining[combination.left] && remaining[combination.right] {
				multiplier *= combination.multiplier
				delete(remaining, combination.left)
				delete(remaining, combination.right)
			}
		}
	}

	for _, acronym := range acronyms {
		if remaining[acronym] {
			multiplier *= nativeModsByRuleset[ruleset][acronym].scoreMultiplier
		}
	}
	return int(math.RoundToEven(multiplier * 1000))
}
