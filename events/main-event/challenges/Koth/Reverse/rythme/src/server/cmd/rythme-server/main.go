package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"rythme.local/server/internal/api"
	"rythme.local/server/internal/arena"
	"rythme.local/server/internal/rctf"
	"rythme.local/server/internal/reporter"
)

const defaultReadyDuration = 30 * time.Second

var serverBuild = "dev"

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		if err := coordinatorHealthcheck(env("RYTHME_HEALTHCHECK_URL", "http://127.0.0.1:1337/healthz")); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}

	logger := log.New(os.Stdout, "rythme: ", log.LstdFlags|log.LUTC)
	managedReporting, err := managedReporterEnvironment()
	if err != nil {
		logger.Fatalf("managed KotH reporter: %v", err)
	}
	pool, err := loadPool(env("RYTHME_POOL_PATH", "../shared/pool.json"))
	if err != nil {
		logger.Fatalf("load pool: %v", err)
	}
	beatmapDir := env("RYTHME_BEATMAP_DIR", "../shared/beatmap-cache")
	beatmapProxy := boolEnv("RYTHME_BEATMAP_PROXY", true)
	if err := validateBeatmapCache(pool, beatmapDir, !beatmapProxy); err != nil {
		logger.Fatalf("validate beatmap cache: %v", err)
	}

	cfg := arena.Config{
		EnrollmentDuration: durationEnv("RYTHME_ENROLLMENT_DURATION", 45*time.Second),
		ReadyDuration:      durationEnv("RYTHME_READY_DURATION", defaultReadyDuration),
		SubmissionDuration: durationEnv("RYTHME_SUBMISSION_DURATION", 12*time.Second),
		ResultsDuration:    durationEnv("RYTHME_RESULTS_DURATION", 20*time.Second),
		CrownThreshold:     int64Env("RYTHME_CROWN_THRESHOLD", 2_100_000),
		RawScoreFloor:      int64Env("RYTHME_RAW_SCORE_FLOOR", 700_000),
		Seed:               int64Env("RYTHME_ARENA_SEED", time.Now().UnixNano()),
	}
	rctfBaseURL := firstEnv("RSCTF_KOTH_PLATFORM_URL", "RSCTF_BASE_URL")
	gameID := int(firstInt64Env("RSCTF_KOTH_GAME_ID", "RSCTF_GAME_ID"))
	challengeID := int(firstInt64Env("RSCTF_KOTH_CHALLENGE_ID", "RSCTF_CHALLENGE_ID"))
	statePath := strings.TrimSpace(os.Getenv("RYTHME_STATE_PATH"))
	if rctfBaseURL != "" && statePath == "" {
		logger.Fatal("RYTHME_STATE_PATH is required when RSCTF authentication is enabled")
	}
	var game *arena.Arena
	if statePath == "" {
		game, err = arena.New(cfg, pool)
	} else {
		game, err = arena.NewPersistent(cfg, pool, statePath)
	}
	if err != nil {
		logger.Fatalf("create arena: %v", err)
	}

	var rctfAuthenticator *rctf.TeamTokenAuthenticator
	if rctfBaseURL != "" {
		rctfAuthenticator = &rctf.TeamTokenAuthenticator{
			BaseURL:     rctfBaseURL,
			GameID:      gameID,
			ChallengeID: challengeID,
			Client:      &http.Client{Timeout: 6 * time.Second},
		}
		if !rctfAuthenticator.Enabled() {
			logger.Fatal("RSCTF_GAME_ID and RSCTF_CHALLENGE_ID must be positive when RSCTF_BASE_URL is set")
		}
		logger.Printf("RSCTF KotH capability onboarding enabled")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if managedReporting {
		managedReporter, reporterErr := reporter.New(reporter.Config{
			GameID: gameID, ChallengeID: challengeID,
			ContextURL:     strings.TrimSpace(os.Getenv("RSCTF_KOTH_CONTEXT_URL")),
			ObservationURL: strings.TrimSpace(os.Getenv("RSCTF_KOTH_OBSERVATION_URL")),
			Secret:         strings.TrimSpace(os.Getenv("RSCTF_KOTH_REPORTER_SECRET")),
			Client:         &http.Client{Timeout: 6 * time.Second}, Logger: logger,
		}, game)
		if reporterErr != nil {
			logger.Fatalf("managed KotH reporter: %v", reporterErr)
		}
		go managedReporter.Run(ctx)
		logger.Printf("managed KotH evidence reporting enabled")
	}
	go func() {
		if err := game.Run(ctx); err != nil {
			logger.Fatalf("arena scheduler stopped: %v", err)
		}
	}()

	server := &http.Server{
		Addr: env("RYTHME_LISTEN", ":1337"),
		Handler: (&api.Server{
			Arena:         game,
			ServerBuild:   serverBuild,
			BeatmapDir:    beatmapDir,
			BeatmapProxy:  beatmapProxy,
			LocalAuth:     boolEnv("RYTHME_LOCAL_AUTH", false),
			RCTFAuth:      rctfAuthenticator,
			IdentityKey:   []byte(os.Getenv("RYTHME_IDENTITY_BRIDGE_KEY")),
			AllowedOrigin: os.Getenv("RYTHME_ALLOWED_ORIGIN"),
			Logger:        logger,
		}).Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      0,
		IdleTimeout:       60 * time.Second,
	}

	go func() {
		<-ctx.Done()
		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer shutdownCancel()
		_ = server.Shutdown(shutdownCtx)
	}()

	if statePath != "" {
		logger.Printf("durable arena state enabled at %s", statePath)
	}
	logger.Printf("arena %s listening on %s with pool %s (%s, %d songs)", serverBuild, server.Addr, pool.Schema, pool.Source, len(pool.Songs))
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Fatalf("serve: %v", err)
	}
}

func managedReporterEnvironment() (bool, error) {
	names := []string{
		"RSCTF_KOTH_GAME_ID",
		"RSCTF_KOTH_CHALLENGE_ID",
		"RSCTF_KOTH_PLATFORM_URL",
		"RSCTF_KOTH_CONTEXT_URL",
		"RSCTF_KOTH_OBSERVATION_URL",
		"RSCTF_KOTH_REPORTER_SECRET",
	}
	present := 0
	for _, name := range names {
		if strings.TrimSpace(os.Getenv(name)) != "" {
			present++
		}
	}
	if present == 0 {
		return false, nil
	}
	if present != len(names) {
		return false, errors.New("managed reporter environment is incomplete")
	}
	return true, nil
}

func coordinatorHealthcheck(url string) error {
	client := &http.Client{Timeout: time.Second}
	response, err := client.Get(url)
	if err != nil {
		return fmt.Errorf("coordinator health request: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 1024))
		return fmt.Errorf("coordinator health returned HTTP %d", response.StatusCode)
	}
	var status struct {
		Status string `json:"status"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1024)).Decode(&status); err != nil {
		return fmt.Errorf("decode coordinator health: %w", err)
	}
	if status.Status != "ok" {
		return fmt.Errorf("coordinator health status is %q", status.Status)
	}
	return nil
}

func validateBeatmapCache(pool arena.Pool, beatmapDir string, requireComplete bool) error {
	for _, song := range pool.Songs {
		if song.ArchiveSHA256 == "" {
			continue
		}
		if filepath.Base(song.ID) != song.ID {
			return fmt.Errorf("unsafe song id %q", song.ID)
		}
		var raw []byte
		var err error
		for _, archiveName := range []string{
			song.ID + ".osz",
			strconv.FormatInt(song.BeatmapsetID, 10) + ".osz",
		} {
			raw, err = os.ReadFile(filepath.Join(beatmapDir, archiveName))
			if err == nil {
				break
			}
		}
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				if requireComplete {
					return fmt.Errorf("%s: beatmap archive is missing", song.ID)
				}
				continue
			}
			return fmt.Errorf("%s: %w", song.ID, err)
		}
		actual := fmt.Sprintf("%x", sha256.Sum256(raw))
		if !strings.EqualFold(actual, song.ArchiveSHA256) {
			return fmt.Errorf("%s: archive checksum is %s, want %s", song.ID, actual, song.ArchiveSHA256)
		}
	}
	return nil
}

func loadPool(path string) (arena.Pool, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return arena.Pool{}, err
	}
	var pool arena.Pool
	if err := json.Unmarshal(raw, &pool); err != nil {
		return arena.Pool{}, err
	}
	if pool.Schema != "rythme-pool-v2" {
		return arena.Pool{}, fmt.Errorf("unsupported pool schema %q", pool.Schema)
	}
	if err := arena.ValidatePool(pool); err != nil {
		return arena.Pool{}, err
	}
	return pool, nil
}

func durationEnv(name string, fallback time.Duration) time.Duration {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := time.ParseDuration(value)
	if err != nil {
		log.Fatalf("%s must be a Go duration: %v", name, err)
	}
	return parsed
}

func int64Env(name string, fallback int64) int64 {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseInt(value, 10, 64)
	if err != nil {
		log.Fatalf("%s must be an integer: %v", name, err)
	}
	return parsed
}

func firstEnv(names ...string) string {
	for _, name := range names {
		if value := strings.TrimSpace(os.Getenv(name)); value != "" {
			return value
		}
	}
	return ""
}

func firstInt64Env(names ...string) int64 {
	for _, name := range names {
		if strings.TrimSpace(os.Getenv(name)) != "" {
			return int64Env(name, 0)
		}
	}
	return 0
}

func boolEnv(name string, fallback bool) bool {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(value)
	if err != nil {
		log.Fatalf("%s must be a boolean: %v", name, err)
	}
	return parsed
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
