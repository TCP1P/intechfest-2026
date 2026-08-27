package arena

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
)

func Chart(song Song) []Note {
	return append([]Note(nil), song.Notes...)
}

func ChartDigest(song Song) string {
	payload := struct {
		Schema    string  `json:"schema"`
		SongID    string  `json:"song_id"`
		BeatmapID int64   `json:"beatmap_id"`
		Ruleset   Ruleset `json:"ruleset"`
		Notes     []Note  `json:"notes"`
	}{
		Schema:    "rythme-chart-v2",
		SongID:    song.ID,
		BeatmapID: song.BeatmapID,
		Ruleset:   song.Ruleset,
		Notes:     song.Notes,
	}
	encoded, _ := json.Marshal(payload)
	sum := sha256.Sum256(encoded)
	return hex.EncodeToString(sum[:])
}

func ControlProfile(ruleset Ruleset) string {
	switch ruleset {
	case RulesetOsu:
		return "mouse cursor · hit Z/X"
	case RulesetTaiko:
		return "kat D/K · don F/J"
	case RulesetFruits:
		return "catch left/right or Z/X · dash Shift"
	case RulesetMania:
		return "4K D/F/J/K"
	default:
		return "unsupported"
	}
}

func ValidActionMask(ruleset Ruleset) uint16 {
	switch ruleset {
	case RulesetOsu:
		return 0x3
	case RulesetTaiko:
		return 0xf
	case RulesetFruits:
		return 0x7
	case RulesetMania:
		return 0xf
	default:
		return 0
	}
}

func validateFrameShape(frame InputFrame) error {
	if frame.ActionBits&^ValidActionMask(frame.Ruleset) != 0 {
		return fmt.Errorf("action mask contains unsupported bits")
	}

	if frame.Ruleset == RulesetOsu {
		if frame.PointerX == nil || frame.PointerY == nil {
			return fmt.Errorf("osu frames require pointer coordinates")
		}
	} else if frame.PointerX != nil || frame.PointerY != nil {
		return fmt.Errorf("pointer coordinates are only valid for osu frames")
	}

	return nil
}
