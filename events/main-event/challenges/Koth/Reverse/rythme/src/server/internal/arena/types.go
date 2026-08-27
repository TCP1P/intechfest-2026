package arena

import "time"

type Ruleset string

const (
	RulesetOsu    Ruleset = "osu"
	RulesetTaiko  Ruleset = "taiko"
	RulesetFruits Ruleset = "fruits"
	RulesetMania  Ruleset = "mania"
)

var AllRulesets = []Ruleset{RulesetOsu, RulesetTaiko, RulesetFruits, RulesetMania}

type Phase string

const (
	PhaseEnrollment Phase = "enrollment"
	PhaseReveal     Phase = "reveal"
	PhaseRunning    Phase = "running"
	PhaseSubmission Phase = "submission"
	PhaseResults    Phase = "results"
)

type Config struct {
	EnrollmentDuration time.Duration
	ReadyDuration      time.Duration
	SubmissionDuration time.Duration
	ResultsDuration    time.Duration
	CrownThreshold     int64
	RawScoreFloor      int64
	Seed               int64
}

type Pool struct {
	Schema     string `json:"schema"`
	Source     string `json:"source,omitempty"`
	VerifiedAt string `json:"verified_at,omitempty"`
	Songs      []Song `json:"songs"`
}

type Song struct {
	ID            string  `json:"id"`
	Title         string  `json:"title"`
	Artist        string  `json:"artist"`
	Ruleset       Ruleset `json:"ruleset"`
	BPM           int     `json:"bpm"`
	DurationMS    int64   `json:"duration_ms"`
	LeadInMS      int64   `json:"lead_in_ms"`
	NoteCount     int     `json:"note_count"`
	Origin        string  `json:"origin,omitempty"`
	BeatmapsetID  int64   `json:"beatmapset_id"`
	BeatmapID     int64   `json:"beatmap_id"`
	Difficulty    string  `json:"difficulty"`
	StarRating    float64 `json:"star_rating"`
	Status        string  `json:"status"`
	KeyCount      int     `json:"key_count,omitempty"`
	Mapper        string  `json:"mapper"`
	SourceURL     string  `json:"source_url"`
	OfficialURL   string  `json:"official_url"`
	License       string  `json:"license,omitempty"`
	ArchiveSHA256 string  `json:"archive_sha256"`
	Notes         []Note  `json:"notes"`
}

type Note struct {
	AtMS   int64  `json:"at_ms"`
	Action uint16 `json:"action"`
	X      uint16 `json:"x,omitempty"`
	Y      uint16 `json:"y,omitempty"`
}

type Selection struct {
	SongID         string  `json:"song_id"`
	SongTitle      string  `json:"song_title"`
	SongArtist     string  `json:"song_artist"`
	Ruleset        Ruleset `json:"ruleset"`
	ChartDigest    string  `json:"chart_digest"`
	ControlProfile string  `json:"control_profile"`
	BPM            int     `json:"bpm"`
	DurationMS     int64   `json:"duration_ms"`
	LeadInMS       int64   `json:"lead_in_ms"`
	NoteCount      int     `json:"note_count"`
	Origin         string  `json:"origin,omitempty"`
	BeatmapsetID   int64   `json:"beatmapset_id"`
	BeatmapID      int64   `json:"beatmap_id"`
	Difficulty     string  `json:"difficulty"`
	StarRating     float64 `json:"star_rating"`
	Status         string  `json:"status"`
	KeyCount       int     `json:"key_count,omitempty"`
	Mapper         string  `json:"mapper"`
	SourceURL      string  `json:"source_url"`
	OfficialURL    string  `json:"official_url"`
	License        string  `json:"license,omitempty"`
	ArchiveSHA256  string  `json:"archive_sha256"`
	Nonce          string  `json:"nonce"`
	SeriesIndex    int64   `json:"series_index"`
	SeriesLeg      int     `json:"series_leg"`
}

type InputFrame struct {
	HeatTicketID string  `json:"heat_ticket_id"`
	Sequence     uint64  `json:"sequence"`
	Ruleset      Ruleset `json:"ruleset_id"`
	NoteIndex    int     `json:"note_index"`
	CueToken     string  `json:"cue_token"`
	ActionBits   uint16  `json:"action_bits"`
	PointerX     *uint16 `json:"pointer_x_u16,omitempty"`
	PointerY     *uint16 `json:"pointer_y_u16,omitempty"`
}

type StampedFrame struct {
	InputFrame
	ReceivedAt time.Time
}

type ModClaim struct {
	Acronym      string `json:"acronym"`
	RateMilli    int    `json:"rate_milli"`
	SettingsHash string `json:"settings_hash"`
}

type AppliedModProfile struct {
	Acronym              string `json:"acronym"`
	RateMilli            int    `json:"rate_milli"`
	SettingsHash         string `json:"settings_hash"`
	ScoreMultiplierMilli int    `json:"score_multiplier_milli"`
}

type RunManifest struct {
	Schema        string   `json:"schema"`
	TeamID        string   `json:"team_id"`
	HeatID        string   `json:"heat_id"`
	HeatTicketID  string   `json:"heat_ticket_id"`
	SongID        string   `json:"song_id"`
	Ruleset       Ruleset  `json:"ruleset_id"`
	ChartDigest   string   `json:"chart_digest"`
	StartAtUnixMS int64    `json:"start_at_unix_ms"`
	InputDigest   string   `json:"input_digest"`
	AttestedMods  ModClaim `json:"final_mods"`
	ClientBuild   string   `json:"client_build"`
	ReadyCapsule  string   `json:"ready_capsule"`
	JudgeCapsule  string   `json:"judge_capsule"`
	NativeProof   string   `json:"native_proof"`
}

type Session struct {
	Token          string
	TeamID         string
	TeamName       string
	ChannelID      string
	RequestKey     []byte
	ResponseKey    []byte
	StateKey       []byte
	LastRequestSeq uint64
	LastRequestMAC []byte
	IssuedAt       time.Time
	ExpiresAt      time.Time
}

type SessionHandshake struct {
	Session
	ServerPublicKey string
	ServerNonce     string
	ServerProof     string
}

type JudgeResult struct {
	HitCount              int     `json:"hit_count"`
	NoteCount             int     `json:"note_count"`
	TotalScoreWithoutMods int64   `json:"total_score_without_mods"`
	PerfectRaw            int64   `json:"perfect_raw"`
	RawBasis              int64   `json:"raw_basis"`
	Accuracy              float64 `json:"accuracy"`
	MaxCombo              int     `json:"max_combo"`
	InputDigest           string  `json:"input_digest"`
	JudgeCapsule          string  `json:"judge_capsule,omitempty"`
}

type Settlement struct {
	TeamID             string             `json:"team_id"`
	TeamName           string             `json:"team_name"`
	Eligible           bool               `json:"eligible"`
	Reason             string             `json:"reason,omitempty"`
	AppliedMods        *AppliedModProfile `json:"applied_mods,omitempty"`
	AttestedMods       *ModClaim          `json:"final_mods,omitempty"`
	AppliedMultiplier  float64            `json:"applied_multiplier"`
	AttestedMultiplier float64            `json:"final_multiplier"`
	Multiplier         float64            `json:"multiplier"`
	SettledScore       int64              `json:"settled_score"`
	Judge              JudgeResult        `json:"judge"`
	SeriesIndex        int64              `json:"series_index"`
	SeriesLeg          int                `json:"series_leg"`
}

type PerformerView struct {
	TeamID       string             `json:"team_id"`
	TeamName     string             `json:"team_name"`
	Ready        bool               `json:"ready"`
	AppliedMods  *AppliedModProfile `json:"applied_mods,omitempty"`
	Frames       int                `json:"frames"`
	ManifestSent bool               `json:"completion_sent"`
	RawBasis     int64              `json:"raw_basis,omitempty"`
	SettledScore int64              `json:"settled_score,omitempty"`
	Eligible     bool               `json:"eligible,omitempty"`
}

type HeatView struct {
	ID             string          `json:"id"`
	Index          int64           `json:"index"`
	Phase          Phase           `json:"phase"`
	Commitment     string          `json:"commitment"`
	EnrollmentEnds int64           `json:"enrollment_ends_unix_ms"`
	StartAt        int64           `json:"start_at_unix_ms,omitempty"`
	EndAt          int64           `json:"end_at_unix_ms,omitempty"`
	SubmissionEnds int64           `json:"submission_ends_unix_ms,omitempty"`
	NextHeatAt     int64           `json:"next_heat_at_unix_ms,omitempty"`
	Selection      *Selection      `json:"selection,omitempty"`
	Performers     []PerformerView `json:"performers"`
}

type SeriesStandingView struct {
	TeamID         string `json:"team_id"`
	TeamName       string `json:"team_name"`
	CompletedLegs  int    `json:"completed_legs"`
	EligibleLegs   int    `json:"eligible_legs"`
	WeakestSettled int64  `json:"weakest_settled,omitempty"`
	AverageRaw     int64  `json:"average_raw,omitempty"`
	AverageSettled int64  `json:"average_settled,omitempty"`
	Complete       bool   `json:"complete"`
}

type SeriesView struct {
	Index      int64                `json:"index"`
	CurrentLeg int                  `json:"current_leg"`
	TotalLegs  int                  `json:"total_legs"`
	Standings  []SeriesStandingView `json:"standings"`
}

type ScoringResultView struct {
	TeamID   string `json:"team_id"`
	TeamName string `json:"team_name"`
	Score    int64  `json:"score"`
	IsCrown  bool   `json:"is_crown"`
}

type ScoringWaveView struct {
	WaveID         string              `json:"wave_id"`
	EndedAtUnixMS  int64               `json:"ended_at_unix_ms"`
	Ruleset        Ruleset             `json:"ruleset"`
	CrownOwnerID   string              `json:"crown_owner_id,omitempty"`
	CrownOwnerName string              `json:"crown_owner_name,omitempty"`
	Results        []ScoringResultView `json:"results"`
}

type ScoringWindowView struct {
	Waves []ScoringWaveView `json:"waves"`
}

type ArenaView struct {
	ServerNowUnixMS  int64      `json:"server_now_unix_ms"`
	PoolSchema       string     `json:"pool_schema"`
	CrownOwnerID     string     `json:"crown_owner_id,omitempty"`
	CrownOwnerName   string     `json:"crown_owner_name,omitempty"`
	CrownSinceUnixMS int64      `json:"crown_since_unix_ms,omitempty"`
	CrownThreshold   int64      `json:"crown_threshold"`
	RawScoreFloor    int64      `json:"raw_score_floor"`
	Heat             HeatView   `json:"heat"`
	Series           SeriesView `json:"series"`
	RulesetBagRule   string     `json:"ruleset_bag_rule"`
	SeriesRule       string     `json:"series_rule"`
}

type EnrollResponse struct {
	HeatID       string `json:"heat_id"`
	HeatTicketID string `json:"heat_ticket_id"`
}

type ReadyResponse struct {
	Ready         bool              `json:"ready"`
	LockedMods    AppliedModProfile `json:"locked_mods"`
	PolicyCapsule string            `json:"policy_capsule"`
}

type InputResponse struct {
	Accepted int    `json:"accepted"`
	Digest   string `json:"input_digest"`
}

type Cue struct {
	NoteIndex     int    `json:"note_index"`
	OpensUnixMS   int64  `json:"opens_unix_ms"`
	ExpiresUnixMS int64  `json:"expires_unix_ms"`
	Token         string `json:"token"`
}

type CueResponse struct {
	HeatID          string `json:"heat_id"`
	ServerNowUnixMS int64  `json:"server_now_unix_ms"`
	Cues            []Cue  `json:"cues"`
}

type ManifestResponse struct {
	Settlement Settlement `json:"settlement"`
	TookCrown  bool       `json:"took_crown"`
}
