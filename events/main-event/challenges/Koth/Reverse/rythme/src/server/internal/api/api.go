package api

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"rythme.local/server/internal/arena"
	"rythme.local/server/internal/rctf"
)

//go:embed web/*
var webFiles embed.FS

type Server struct {
	Arena         *arena.Arena
	ServerBuild   string
	BeatmapDir    string
	BeatmapDirs   []string
	BeatmapProxy  bool
	BeatmapClient *http.Client
	LocalAuth     bool
	RCTFAuth      *rctf.TeamTokenAuthenticator
	IdentityKey   []byte
	AllowedOrigin string
	Logger        *log.Logger
	beatmaps      *beatmapStore
	secureReplays secureReplayCache
}

type localSessionRequest struct {
	TeamID          string `json:"team_id"`
	TeamName        string `json:"team_name"`
	ClientPublicKey string `json:"client_public_key"`
	ClientNonce     string `json:"client_nonce"`
}

type rctfSessionRequest struct {
	TeamToken       string `json:"team_token"`
	ClientPublicKey string `json:"client_public_key"`
	ClientNonce     string `json:"client_nonce"`
}

type assertionRequest struct {
	TeamID          string `json:"team_id"`
	TeamName        string `json:"team_name"`
	IssuedAtUnixMS  int64  `json:"issued_at_unix_ms"`
	Nonce           string `json:"nonce"`
	Signature       string `json:"signature"`
	ClientPublicKey string `json:"client_public_key"`
	ClientNonce     string `json:"client_nonce"`
}

type inputRequest struct {
	Frames []arena.InputFrame `json:"frames"`
}

type readyRequest struct {
	HeatTicketID     string `json:"heat_ticket_id"`
	ModifierEnvelope string `json:"modifier_envelope"`
}

func (s *Server) Handler() http.Handler {
	if s.beatmaps == nil {
		directories := append([]string(nil), s.BeatmapDirs...)
		if len(directories) == 0 && s.BeatmapDir != "" {
			directories = append(directories, s.BeatmapDir)
		}
		s.beatmaps = newBeatmapStore(
			directories,
			s.BeatmapDir,
			s.BeatmapProxy,
			s.BeatmapClient,
			s.Logger,
		)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.health)
	mux.HandleFunc("GET /api/v1/config", s.config)
	mux.HandleFunc("GET /api/v1/state", s.state)
	mux.HandleFunc("GET /api/v1/scoring", s.scoring)
	mux.HandleFunc("GET /api/v1/events", s.events)
	mux.HandleFunc("GET /api/v1/beatmaps/{name}", s.beatmap)
	mux.HandleFunc("POST /api/v2/session/local", s.localSession)
	mux.HandleFunc("POST /api/v2/session/rctf", s.rctfSession)
	mux.HandleFunc("POST /api/v2/session/exchange", s.assertedSession)
	mux.Handle("GET /api/v2/state", s.secure(http.HandlerFunc(s.state)))
	mux.Handle("POST /api/v3/frame", s.secure(http.HandlerFunc(s.frame)))

	static, _ := fs.Sub(webFiles, "web")
	mux.Handle("/", http.FileServer(http.FS(static)))
	return s.securityHeaders(s.requestLog(mux))
}

func (s *Server) beatmap(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if !strings.HasSuffix(name, ".osz") {
		writeProblem(w, http.StatusNotFound, "beatmap not found")
		return
	}
	songID := strings.TrimSuffix(name, ".osz")
	song, exists := s.Arena.Song(songID)
	if !exists {
		writeProblem(w, http.StatusNotFound, "beatmap not found")
		return
	}
	archive, err := s.beatmaps.open(r.Context(), song)
	if err != nil {
		if s.Logger != nil {
			s.Logger.Printf("open beatmap %s: %v", songID, err)
		}
		writeProblem(w, http.StatusServiceUnavailable, "beatmap is unavailable")
		return
	}
	defer archive.Close()

	w.Header().Set("Content-Type", "application/x-osu-beatmap-archive")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s.osz"`, songID))
	w.Header().Set("Cache-Control", "public, max-age=86400, immutable")
	http.ServeContent(w, r, name, archive.modTime, archive)
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	if !s.Arena.Healthy(time.Now().UTC()) {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "unhealthy"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) config(w http.ResponseWriter, _ *http.Request) {
	pool := s.Arena.Pool()
	writeJSON(w, http.StatusOK, map[string]any{
		"server_build":     s.ServerBuild,
		"protocol":         arena.SecureChannelProtocol,
		"client_build":     "r6-0f3d3c3-8f21",
		"pool_schema":      pool.Schema,
		"pool_source":      pool.Source,
		"pool_verified_at": pool.VerifiedAt,
		"pool_song_count":  len(pool.Songs),
		"transport":        "authenticated arena channel",
		"local_auth":       s.LocalAuth,
		"rctf_auth":        s.RCTFAuth != nil && s.RCTFAuth.Enabled(),
		"rulesets":         arena.AllRulesets,
		"arena_mods":       "implemented lazer modifiers selected after the shared reveal",
	})
}

func (s *Server) frame(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(io.LimitReader(r.Body, (64<<10)+1))
	if err != nil || len(raw) > 64<<10 {
		writeProblem(w, http.StatusBadRequest, "invalid arena request")
		return
	}
	response, err := s.Arena.HandleFrame(bearerToken(r), raw, time.Now().UTC())
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(response)
}

func (s *Server) rctfSession(w http.ResponseWriter, r *http.Request) {
	if s.RCTFAuth == nil || !s.RCTFAuth.Enabled() {
		writeProblem(w, http.StatusNotFound, "RSCTF onboarding is not configured")
		return
	}
	var request rctfSessionRequest
	if !decodeJSON(w, r, &request) {
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 8*time.Second)
	defer cancel()
	identity, err := s.RCTFAuth.Authenticate(ctx, request.TeamToken)
	if err != nil {
		if errors.Is(err, rctf.ErrAuthenticationRejected) {
			writeProblem(w, http.StatusUnauthorized, "RSCTF KotH token was rejected")
			return
		}
		if s.Logger != nil {
			s.Logger.Printf("RSCTF authentication: %v", err)
		}
		writeProblem(w, http.StatusBadGateway, "RSCTF authentication is temporarily unavailable")
		return
	}

	session, err := s.Arena.CreateSecureSession(
		identity.ID,
		identity.Name,
		request.ClientPublicKey,
		request.ClientNonce,
		time.Now().UTC(),
	)
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeSession(w, session)
}

func (s *Server) state(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.Arena.View(time.Now().UTC()))
}

func (s *Server) scoring(w http.ResponseWriter, r *http.Request) {
	fromMS, fromErr := strconv.ParseInt(r.URL.Query().Get("from"), 10, 64)
	toMS, toErr := strconv.ParseInt(r.URL.Query().Get("to"), 10, 64)
	from := time.UnixMilli(fromMS).UTC()
	to := time.UnixMilli(toMS).UTC()
	if fromErr != nil || toErr != nil || fromMS <= 0 || toMS <= 0 ||
		!to.After(from) || to.Sub(from) > 2*time.Hour {
		writeProblem(w, http.StatusBadRequest, "invalid scoring window")
		return
	}
	writeJSON(w, http.StatusOK, s.Arena.ScoringWindow(from, to, time.Now().UTC()))
}

func (s *Server) events(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeProblem(w, http.StatusInternalServerError, "streaming unavailable")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	updates, cancel, err := s.Arena.Subscribe()
	if err != nil {
		writeProblem(w, http.StatusServiceUnavailable, "event stream capacity reached")
		return
	}
	defer cancel()
	send := func() {
		fmt.Fprintf(w, "event: state\ndata: %s\n\n", arena.MarshalState(s.Arena.View(time.Now().UTC())))
		flusher.Flush()
	}
	send()

	heartbeat := time.NewTicker(10 * time.Second)
	defer heartbeat.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case _, ok := <-updates:
			if !ok {
				return
			}
			send()
		case <-heartbeat.C:
			fmt.Fprint(w, ": keepalive\n\n")
			flusher.Flush()
		}
	}
}

func (s *Server) localSession(w http.ResponseWriter, r *http.Request) {
	if !s.LocalAuth {
		writeProblem(w, http.StatusNotFound, "local onboarding is disabled")
		return
	}
	var request localSessionRequest
	if !decodeJSON(w, r, &request) {
		return
	}
	session, err := s.Arena.CreateSecureSession(
		request.TeamID,
		request.TeamName,
		request.ClientPublicKey,
		request.ClientNonce,
		time.Now().UTC(),
	)
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeSession(w, session)
}

func (s *Server) assertedSession(w http.ResponseWriter, r *http.Request) {
	if len(s.IdentityKey) < 16 {
		writeProblem(w, http.StatusNotFound, "identity bridge is not configured")
		return
	}
	var request assertionRequest
	if !decodeJSON(w, r, &request) {
		return
	}
	if delta := time.Since(time.UnixMilli(request.IssuedAtUnixMS)); delta < -2*time.Minute || delta > 2*time.Minute {
		writeProblem(w, http.StatusUnauthorized, "identity assertion expired")
		return
	}
	provided, err := hex.DecodeString(request.Signature)
	if err != nil {
		writeProblem(w, http.StatusUnauthorized, "invalid identity assertion")
		return
	}
	canonical := strings.Join([]string{
		"rythme-rctf-identity-v1",
		request.TeamID,
		request.TeamName,
		strconv.FormatInt(request.IssuedAtUnixMS, 10),
		request.Nonce,
		"",
	}, "\n")
	mac := hmac.New(sha256.New, s.IdentityKey)
	mac.Write([]byte(canonical))
	if !hmac.Equal(provided, mac.Sum(nil)) {
		writeProblem(w, http.StatusUnauthorized, "invalid identity assertion")
		return
	}
	session, err := s.Arena.CreateSecureSession(
		request.TeamID,
		request.TeamName,
		request.ClientPublicKey,
		request.ClientNonce,
		time.Now().UTC(),
	)
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeSession(w, session)
}

func (s *Server) enroll(w http.ResponseWriter, r *http.Request) {
	response, err := s.Arena.Enroll(bearerToken(r), time.Now().UTC())
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, response)
}

func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	var request readyRequest
	if !decodeJSON(w, r, &request) {
		return
	}
	response, err := s.Arena.Ready(
		bearerToken(r),
		request.HeatTicketID,
		request.ModifierEnvelope,
		time.Now().UTC(),
	)
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) input(w http.ResponseWriter, r *http.Request) {
	var request inputRequest
	if !decodeJSON(w, r, &request) {
		return
	}
	response, err := s.Arena.SubmitFrames(bearerToken(r), request.Frames, time.Now().UTC())
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeJSON(w, http.StatusAccepted, response)
}

func (s *Server) cues(w http.ResponseWriter, r *http.Request) {
	ticket := r.URL.Query().Get("ticket")
	response, err := s.Arena.Cues(bearerToken(r), ticket, time.Now().UTC())
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) result(w http.ResponseWriter, r *http.Request) {
	ticket := r.URL.Query().Get("ticket")
	result, settlement, err := s.Arena.Result(bearerToken(r), ticket, time.Now().UTC())
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"judge":      result,
		"settlement": settlement,
	})
}

func (s *Server) manifest(w http.ResponseWriter, r *http.Request) {
	var manifest arena.RunManifest
	if !decodeJSON(w, r, &manifest) {
		return
	}
	response, err := s.Arena.SubmitManifest(bearerToken(r), manifest, time.Now().UTC())
	if err != nil {
		s.writeArenaError(w, err)
		return
	}
	writeJSON(w, http.StatusAccepted, response)
}

func (s *Server) requestLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		next.ServeHTTP(w, r)
		if s.Logger != nil && r.URL.Path != "/healthz" {
			s.Logger.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(started).Round(time.Millisecond))
		}
	})
}

func (s *Server) securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'")
		if s.AllowedOrigin != "" {
			w.Header().Set("Access-Control-Allow-Origin", s.AllowedOrigin)
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) secure(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		const maximumBodyBytes = 64 << 10

		raw, err := io.ReadAll(io.LimitReader(r.Body, maximumBodyBytes+1))
		if err != nil {
			writeProblem(w, http.StatusBadRequest, "could not read secure request body")
			return
		}
		if len(raw) > maximumBodyBytes {
			writeProblem(w, http.StatusRequestEntityTooLarge, "secure request body is too large")
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(raw))

		sequence, sequenceErr := strconv.ParseUint(r.Header.Get("X-Rythme-Sequence"), 10, 64)
		sentAt, sentAtErr := strconv.ParseInt(r.Header.Get("X-Rythme-Timestamp"), 10, 64)
		if sequenceErr != nil || sentAtErr != nil {
			writeProblem(w, http.StatusUnauthorized, "secure channel headers are incomplete")
			return
		}
		token := bearerToken(r)
		proof := arena.ChannelRequestProof{
			ChannelID: r.Header.Get("X-Rythme-Channel"),
			Sequence:  sequence,
			SentAtMS:  sentAt,
			Previous:  r.Header.Get("X-Rythme-Previous"),
			Signature: r.Header.Get("X-Rythme-Signature"),
		}
		identity := newSecureRequestIdentity(proof, r.Method, r.URL.RequestURI(), raw)
		replay, owner, err := s.secureReplays.begin(
			r.Context(),
			token,
			identity,
			func() (arena.ChannelReceipt, error) {
				return s.Arena.VerifyChannelRequest(
					token,
					proof,
					r.Method,
					r.URL.RequestURI(),
					raw,
					time.Now().UTC(),
				)
			},
		)
		if err != nil {
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
				return
			}
			s.writeArenaError(w, err)
			return
		}
		if !owner {
			writeSecureResponse(w, replay.response)
			return
		}

		buffered := newBufferedResponse()
		func() {
			defer func() {
				if recovered := recover(); recovered != nil {
					if s.Logger != nil {
						s.Logger.Printf("secure handler panic: %v", recovered)
					}
					buffered = newBufferedResponse()
					writeProblem(buffered, http.StatusInternalServerError, "internal server error")
				}
			}()
			next.ServeHTTP(buffered, r)
		}()

		body := bytes.Clone(buffered.body.Bytes())
		header := buffered.header.Clone()
		header.Set("Cache-Control", "no-store")
		header.Set("X-Rythme-Ack", strconv.FormatUint(replay.receipt.Sequence, 10))
		header.Set(
			"X-Rythme-Response-Signature",
			arena.SignChannelResponse(replay.receipt, buffered.status, body),
		)
		response := secureResponse{header: header, body: body, status: buffered.status}
		s.secureReplays.complete(replay, response)
		writeSecureResponse(w, response)
	})
}

func (s *Server) writeArenaError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, arena.ErrUnauthorized):
		writeProblem(w, http.StatusUnauthorized, err.Error())
	case errors.Is(err, arena.ErrConflict), errors.Is(err, arena.ErrWrongPhase):
		writeProblem(w, http.StatusConflict, err.Error())
	case errors.Is(err, arena.ErrBadRequest):
		writeProblem(w, http.StatusBadRequest, err.Error())
	default:
		if s.Logger != nil {
			s.Logger.Printf("internal error: %v", err)
		}
		writeProblem(w, http.StatusInternalServerError, "internal server error")
	}
}

func bearerToken(r *http.Request) string {
	const prefix = "Bearer "
	value := r.Header.Get("Authorization")
	if !strings.HasPrefix(value, prefix) {
		return ""
	}
	return strings.TrimSpace(strings.TrimPrefix(value, prefix))
}

func decodeJSON(w http.ResponseWriter, r *http.Request, destination any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		writeProblem(w, http.StatusBadRequest, "invalid JSON body")
		return false
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		writeProblem(w, http.StatusBadRequest, "request must contain one JSON value")
		return false
	}
	return true
}

func writeProblem(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]any{
		"error":  http.StatusText(status),
		"detail": message,
		"status": status,
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeSession(w http.ResponseWriter, handshake arena.SessionHandshake) {
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusCreated, map[string]any{
		"access_token":               handshake.Token,
		"token_type":                 "bearer",
		"team_id":                    handshake.TeamID,
		"team_name":                  handshake.TeamName,
		"channel_id":                 handshake.ChannelID,
		"server_public_key":          handshake.ServerPublicKey,
		"server_nonce":               handshake.ServerNonce,
		"server_proof":               handshake.ServerProof,
		"channel_expires_at_unix_ms": handshake.ExpiresAt.UnixMilli(),
	})
}

type bufferedResponse struct {
	header http.Header
	body   bytes.Buffer
	status int
}

func newBufferedResponse() *bufferedResponse {
	return &bufferedResponse{
		header: make(http.Header),
		status: http.StatusOK,
	}
}

func (w *bufferedResponse) Header() http.Header {
	return w.header
}

func (w *bufferedResponse) WriteHeader(status int) {
	w.status = status
}

func (w *bufferedResponse) Write(value []byte) (int, error) {
	return w.body.Write(value)
}
