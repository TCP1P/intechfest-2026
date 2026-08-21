package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type sourceManifest struct {
	Schema           string        `json:"schema"`
	VerifiedAt       string        `json:"verified_at"`
	SeedLedger       string        `json:"seed_ledger"`
	SeedLedgerSHA256 string        `json:"seed_ledger_sha256"`
	Entries          []sourceEntry `json:"entries"`
}

type sourceEntry struct {
	ID            string  `json:"id"`
	SeedIndex     int     `json:"seed_index"`
	SeedTitle     string  `json:"seed_title"`
	SeedArtist    string  `json:"seed_artist"`
	Ruleset       string  `json:"ruleset"`
	BeatmapsetID  int64   `json:"beatmapset_id"`
	BeatmapID     int64   `json:"beatmap_id"`
	StarRating    float64 `json:"star_rating"`
	Status        string  `json:"status"`
	BPM           int     `json:"bpm"`
	SourceURL     string  `json:"source_url"`
	OfficialURL   string  `json:"official_url"`
	ArchiveSHA256 string  `json:"archive_sha256"`
}

type seedManifest struct {
	Schema          string      `json:"schema"`
	ReceivedAt      string      `json:"received_at"`
	SelectionPolicy string      `json:"selection_policy"`
	Tracks          []seedTrack `json:"tracks"`
}

type seedTrack struct {
	Index     int    `json:"index"`
	Title     string `json:"title"`
	Artist    string `json:"artist"`
	LocalOnly bool   `json:"local_only,omitempty"`
}

type pool struct {
	Schema     string `json:"schema"`
	Source     string `json:"source"`
	VerifiedAt string `json:"verified_at"`
	Songs      []song `json:"songs"`
}

type song struct {
	ID            string  `json:"id"`
	Title         string  `json:"title"`
	Artist        string  `json:"artist"`
	Ruleset       string  `json:"ruleset"`
	BPM           int     `json:"bpm"`
	DurationMS    int64   `json:"duration_ms"`
	LeadInMS      int64   `json:"lead_in_ms"`
	NoteCount     int     `json:"note_count"`
	Origin        string  `json:"origin"`
	BeatmapsetID  int64   `json:"beatmapset_id"`
	BeatmapID     int64   `json:"beatmap_id"`
	Difficulty    string  `json:"difficulty"`
	StarRating    float64 `json:"star_rating"`
	Status        string  `json:"status"`
	KeyCount      int     `json:"key_count,omitempty"`
	Mapper        string  `json:"mapper"`
	SourceURL     string  `json:"source_url"`
	OfficialURL   string  `json:"official_url"`
	License       string  `json:"license"`
	ArchiveSHA256 string  `json:"archive_sha256"`
	Notes         []note  `json:"notes"`
}

type note struct {
	AtMS   int64  `json:"at_ms"`
	Action uint16 `json:"action"`
	X      uint16 `json:"x,omitempty"`
	Y      uint16 `json:"y,omitempty"`
}

type parsedBeatmap struct {
	General    map[string]string
	Metadata   map[string]string
	Difficulty map[string]string
	Objects    []string
}

var modeNumbers = map[string]string{
	"osu":    "0",
	"taiko":  "1",
	"fruits": "2",
	"mania":  "3",
}

func main() {
	sourcesPath := flag.String("sources", "src/shared/pool.sources.json", "curated source manifest")
	cacheDir := flag.String("cache", "src/shared/beatmap-cache", "download/cache directory")
	outputPath := flag.String("out", "src/shared/pool.json", "validated output pool")
	flag.Parse()

	if err := run(*sourcesPath, *cacheDir, *outputPath); err != nil {
		fmt.Fprintln(os.Stderr, "poolimport:", err)
		os.Exit(1)
	}
}

func run(sourcesPath, cacheDir, outputPath string) error {
	raw, err := os.ReadFile(sourcesPath)
	if err != nil {
		return err
	}
	var sources sourceManifest
	if err := json.Unmarshal(raw, &sources); err != nil {
		return fmt.Errorf("decode sources: %w", err)
	}
	if sources.Schema != "rythme-pool-sources-v1" {
		return fmt.Errorf("unsupported source schema %q", sources.Schema)
	}
	if _, err := time.Parse("2006-01-02", sources.VerifiedAt); err != nil {
		return fmt.Errorf("verified_at: %w", err)
	}
	if len(sources.Entries) < 4 {
		return errors.New("at least four curated difficulties are required")
	}
	seeds, err := loadSeedLedger(filepath.Dir(sourcesPath), sources)
	if err != nil {
		return err
	}

	result := pool{
		Schema:     "rythme-pool-v2",
		Source:     "organizer-curated-real-osu-beatmaps",
		VerifiedAt: sources.VerifiedAt + "T00:00:00Z",
		Songs:      make([]song, 0, len(sources.Entries)),
	}
	counts := map[string]int{}
	seenIDs := map[string]bool{}

	for _, entry := range sources.Entries {
		if seenIDs[entry.ID] {
			return fmt.Errorf("duplicate entry id %q", entry.ID)
		}
		seenIDs[entry.ID] = true
		seed, ok := seeds[entry.SeedIndex]
		if !ok {
			return fmt.Errorf("%s: seed index %d is not in the organizer ledger", entry.ID, entry.SeedIndex)
		}
		if entry.SeedTitle != seed.Title || entry.SeedArtist != seed.Artist {
			return fmt.Errorf("%s: seed metadata does not match ledger index %d", entry.ID, entry.SeedIndex)
		}
		if modeNumbers[entry.Ruleset] == "" && entry.Ruleset != "osu" {
			return fmt.Errorf("%s: unsupported ruleset %q", entry.ID, entry.Ruleset)
		}
		if entry.StarRating < 3.5 {
			return fmt.Errorf("%s: %.2f stars is below the hard-pool floor", entry.ID, entry.StarRating)
		}
		archive, err := loadArchive(cacheDir, entry)
		if err != nil {
			return fmt.Errorf("%s: %w", entry.ID, err)
		}
		sum := sha256.Sum256(archive)
		actualHash := hex.EncodeToString(sum[:])
		if !strings.EqualFold(actualHash, entry.ArchiveSHA256) {
			return fmt.Errorf("%s: archive hash %s does not match pin %s", entry.ID, actualHash, entry.ArchiveSHA256)
		}

		parsed, err := selectBeatmap(archive, entry)
		if err != nil {
			return fmt.Errorf("%s: %w", entry.ID, err)
		}
		notes, maximumObjectMS, err := extractNotes(entry.Ruleset, parsed)
		if err != nil {
			return fmt.Errorf("%s: %w", entry.ID, err)
		}
		if len(notes) < 100 {
			return fmt.Errorf("%s: only %d scoreable objects", entry.ID, len(notes))
		}
		keyCount := 0
		if entry.Ruleset == "mania" {
			keyCount, err = parseKeyCount(parsed.Difficulty["CircleSize"])
			if err != nil {
				return fmt.Errorf("%s: %w", entry.ID, err)
			}
			if keyCount != 4 {
				return fmt.Errorf("%s: only native 4K mania maps are allowed, got %dK", entry.ID, keyCount)
			}
		}
		leadIn := notes[0].AtMS
		duration := maximumObjectMS + 5000
		result.Songs = append(result.Songs, song{
			ID:            entry.ID,
			Title:         parsed.Metadata["Title"],
			Artist:        parsed.Metadata["Artist"],
			Ruleset:       entry.Ruleset,
			BPM:           entry.BPM,
			DurationMS:    duration,
			LeadInMS:      leadIn,
			NoteCount:     len(notes),
			Origin:        "osu-community-beatmap",
			BeatmapsetID:  entry.BeatmapsetID,
			BeatmapID:     entry.BeatmapID,
			Difficulty:    parsed.Metadata["Version"],
			StarRating:    entry.StarRating,
			Status:        entry.Status,
			KeyCount:      keyCount,
			Mapper:        parsed.Metadata["Creator"],
			SourceURL:     entry.SourceURL,
			OfficialURL:   entry.OfficialURL,
			License:       "community-beatmap; rights remain with the respective creators",
			ArchiveSHA256: actualHash,
			Notes:         notes,
		})
		counts[entry.Ruleset]++
	}

	for ruleset := range modeNumbers {
		if counts[ruleset] < 4 {
			return fmt.Errorf("ruleset %s has only %d maps; four are required", ruleset, counts[ruleset])
		}
	}
	sort.SliceStable(result.Songs, func(i, j int) bool {
		if result.Songs[i].Ruleset != result.Songs[j].Ruleset {
			return modeNumbers[result.Songs[i].Ruleset] < modeNumbers[result.Songs[j].Ruleset]
		}
		return result.Songs[i].ID < result.Songs[j].ID
	})

	encoded, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	encoded = append(encoded, '\n')
	if err := os.WriteFile(outputPath, encoded, 0o644); err != nil {
		return err
	}
	fmt.Printf("wrote %s with %d exact difficulties (%d notes)\n",
		outputPath, len(result.Songs), countNotes(result.Songs))
	return nil
}

func loadSeedLedger(directory string, sources sourceManifest) (map[int]seedTrack, error) {
	if sources.SeedLedger == "" || filepath.Base(sources.SeedLedger) != sources.SeedLedger {
		return nil, errors.New("seed_ledger must name one file beside the source manifest")
	}
	if len(sources.SeedLedgerSHA256) != sha256.Size*2 {
		return nil, errors.New("seed_ledger_sha256 must contain 64 hexadecimal characters")
	}
	expected, err := hex.DecodeString(sources.SeedLedgerSHA256)
	if err != nil {
		return nil, fmt.Errorf("seed_ledger_sha256: %w", err)
	}
	raw, err := os.ReadFile(filepath.Join(directory, sources.SeedLedger))
	if err != nil {
		return nil, fmt.Errorf("read seed ledger: %w", err)
	}
	actual := sha256.Sum256(raw)
	if !bytes.Equal(actual[:], expected) {
		return nil, fmt.Errorf("seed ledger hash %x does not match pin %s", actual, sources.SeedLedgerSHA256)
	}

	var manifest seedManifest
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return nil, fmt.Errorf("decode seed ledger: %w", err)
	}
	if manifest.Schema != "rythme-supplied-song-seeds-v1" {
		return nil, fmt.Errorf("unsupported seed schema %q", manifest.Schema)
	}
	if manifest.SelectionPolicy != "allowlist-only" {
		return nil, fmt.Errorf("unsupported seed selection policy %q", manifest.SelectionPolicy)
	}
	if _, err := time.Parse("2006-01-02", manifest.ReceivedAt); err != nil {
		return nil, fmt.Errorf("seed received_at: %w", err)
	}
	if len(manifest.Tracks) != 29 {
		return nil, fmt.Errorf("seed ledger has %d tracks; expected 29", len(manifest.Tracks))
	}

	result := make(map[int]seedTrack, len(manifest.Tracks))
	for _, track := range manifest.Tracks {
		if track.Index < 1 || track.Index > len(manifest.Tracks) ||
			track.Title == "" ||
			(track.Artist == "" && !track.LocalOnly) {
			return nil, fmt.Errorf("invalid seed track at index %d", track.Index)
		}
		if _, exists := result[track.Index]; exists {
			return nil, fmt.Errorf("duplicate seed track index %d", track.Index)
		}
		result[track.Index] = track
	}
	for index := 1; index <= len(manifest.Tracks); index++ {
		if _, exists := result[index]; !exists {
			return nil, fmt.Errorf("seed ledger is missing index %d", index)
		}
	}
	return result, nil
}

func loadArchive(cacheDir string, entry sourceEntry) ([]byte, error) {
	if err := os.MkdirAll(cacheDir, 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(cacheDir, strconv.FormatInt(entry.BeatmapsetID, 10)+".osz")
	if raw, err := os.ReadFile(path); err == nil {
		return raw, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}

	client := &http.Client{Timeout: 2 * time.Minute}
	response, err := client.Get(entry.SourceURL)
	if err != nil {
		return nil, fmt.Errorf("download %s: %w", entry.SourceURL, err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download %s: HTTP %d", entry.SourceURL, response.StatusCode)
	}
	const maximumArchiveSize = 96 << 20
	raw, err := io.ReadAll(io.LimitReader(response.Body, maximumArchiveSize+1))
	if err != nil {
		return nil, err
	}
	if len(raw) > maximumArchiveSize {
		return nil, errors.New("archive exceeds 96 MiB")
	}
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		return nil, err
	}
	return raw, nil
}

func selectBeatmap(archive []byte, entry sourceEntry) (parsedBeatmap, error) {
	reader, err := zip.NewReader(bytes.NewReader(archive), int64(len(archive)))
	if err != nil {
		return parsedBeatmap{}, fmt.Errorf("open osz: %w", err)
	}
	var matches []parsedBeatmap
	for _, file := range reader.File {
		if !strings.HasSuffix(strings.ToLower(file.Name), ".osu") {
			continue
		}
		handle, err := file.Open()
		if err != nil {
			return parsedBeatmap{}, err
		}
		raw, readErr := io.ReadAll(io.LimitReader(handle, 8<<20))
		closeErr := handle.Close()
		if readErr != nil {
			return parsedBeatmap{}, readErr
		}
		if closeErr != nil {
			return parsedBeatmap{}, closeErr
		}
		parsed := parseBeatmap(raw)
		if parsed.Metadata["BeatmapID"] == strconv.FormatInt(entry.BeatmapID, 10) {
			matches = append(matches, parsed)
		}
	}
	if len(matches) != 1 {
		return parsedBeatmap{}, fmt.Errorf("expected one .osu with BeatmapID %d, found %d", entry.BeatmapID, len(matches))
	}
	result := matches[0]
	if result.Metadata["BeatmapSetID"] != strconv.FormatInt(entry.BeatmapsetID, 10) {
		return parsedBeatmap{}, fmt.Errorf("BeatmapSetID is %q", result.Metadata["BeatmapSetID"])
	}
	if result.General["Mode"] != modeNumbers[entry.Ruleset] {
		return parsedBeatmap{}, fmt.Errorf("native mode is %q, expected %s", result.General["Mode"], entry.Ruleset)
	}
	for _, key := range []string{"Title", "Artist", "Creator", "Version"} {
		if strings.TrimSpace(result.Metadata[key]) == "" {
			return parsedBeatmap{}, fmt.Errorf("metadata %s is empty", key)
		}
	}
	return result, nil
}

func parseBeatmap(raw []byte) parsedBeatmap {
	result := parsedBeatmap{
		General:    map[string]string{},
		Metadata:   map[string]string{},
		Difficulty: map[string]string{},
	}
	section := ""
	scanner := bufio.NewScanner(bytes.NewReader(bytes.TrimPrefix(raw, []byte{0xef, 0xbb, 0xbf})))
	scanner.Buffer(make([]byte, 64*1024), 8<<20)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.TrimSuffix(strings.TrimPrefix(line, "["), "]")
			continue
		}
		if line == "" || strings.HasPrefix(line, "//") {
			continue
		}
		if section == "HitObjects" {
			result.Objects = append(result.Objects, line)
			continue
		}
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		switch section {
		case "General":
			result.General[strings.TrimSpace(key)] = strings.TrimSpace(value)
		case "Metadata":
			result.Metadata[strings.TrimSpace(key)] = strings.TrimSpace(value)
		case "Difficulty":
			result.Difficulty[strings.TrimSpace(key)] = strings.TrimSpace(value)
		}
	}
	return result
}

func extractNotes(ruleset string, beatmap parsedBeatmap) ([]note, int64, error) {
	notes := make([]note, 0, len(beatmap.Objects))
	var maximumEnd int64
	previousFruitX := -1
	for _, object := range beatmap.Objects {
		fields := strings.Split(object, ",")
		if len(fields) < 5 {
			return nil, 0, fmt.Errorf("malformed hit object %q", object)
		}
		x, err := strconv.Atoi(fields[0])
		if err != nil {
			return nil, 0, err
		}
		y, err := strconv.Atoi(fields[1])
		if err != nil {
			return nil, 0, err
		}
		atMS, err := strconv.ParseInt(fields[2], 10, 64)
		if err != nil {
			return nil, 0, err
		}
		objectType, err := strconv.Atoi(fields[3])
		if err != nil {
			return nil, 0, err
		}
		hitSound, err := strconv.Atoi(fields[4])
		if err != nil {
			return nil, 0, err
		}
		endMS := atMS
		if objectType&8 != 0 && len(fields) > 5 {
			endMS, _ = strconv.ParseInt(fields[5], 10, 64)
		}
		if objectType&128 != 0 && len(fields) > 5 {
			endText, _, _ := strings.Cut(fields[5], ":")
			endMS, _ = strconv.ParseInt(endText, 10, 64)
		}
		if endMS > maximumEnd {
			maximumEnd = endMS
		}

		isCircle := objectType&1 != 0
		isSlider := objectType&2 != 0
		isHold := objectType&128 != 0
		switch ruleset {
		case "osu", "fruits":
			if !isCircle && !isSlider {
				continue
			}
		case "taiko":
			if !isCircle {
				continue
			}
		case "mania":
			if !isCircle && !isHold {
				continue
			}
		}

		item := note{AtMS: atMS}
		switch ruleset {
		case "osu":
			item.Action = 0x3
			item.X = scaleCoordinate(x, 512)
			item.Y = scaleCoordinate(y, 384)
		case "taiko":
			if hitSound&(2|8) != 0 {
				item.Action = 0x3
			} else {
				item.Action = 0xc
			}
		case "fruits":
			if previousFruitX < 0 {
				item.Action = 1
			} else if x >= previousFruitX {
				item.Action = 2
			} else {
				item.Action = 1
			}
			previousFruitX = x
		case "mania":
			keys, err := parseKeyCount(beatmap.Difficulty["CircleSize"])
			if err != nil {
				return nil, 0, err
			}
			lane := x * keys / 512
			if lane < 0 {
				lane = 0
			}
			if lane >= keys {
				lane = keys - 1
			}
			item.Action = uint16(1 << lane)
		}
		notes = append(notes, item)
	}
	sort.SliceStable(notes, func(i, j int) bool { return notes[i].AtMS < notes[j].AtMS })
	return notes, maximumEnd, nil
}

func parseKeyCount(raw string) (int, error) {
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return 0, fmt.Errorf("invalid CircleSize %q", raw)
	}
	keys := int(math.Round(value))
	if keys < 1 || keys > 16 {
		return 0, fmt.Errorf("invalid key count %d", keys)
	}
	return keys, nil
}

func scaleCoordinate(value, maximum int) uint16 {
	value = min(max(value, 0), maximum)
	return uint16(math.Round(float64(value) * float64(math.MaxUint16) / float64(maximum)))
}

func countNotes(songs []song) int {
	total := 0
	for _, item := range songs {
		total += len(item.Notes)
	}
	return total
}
