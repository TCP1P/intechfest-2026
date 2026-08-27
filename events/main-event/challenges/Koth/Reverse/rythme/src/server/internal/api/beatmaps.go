package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"rythme.local/server/internal/arena"
)

const maximumBeatmapArchiveSize = 96 << 20

var errBeatmapUnavailable = errors.New("beatmap archive is unavailable")

type beatmapArchive struct {
	io.ReadSeeker
	closer  io.Closer
	modTime time.Time
}

func (a *beatmapArchive) Close() error {
	return a.closer.Close()
}

type beatmapFetch struct {
	done chan struct{}
	raw  []byte
	err  error
}

type beatmapStore struct {
	directories []string
	writeDir    string
	proxy       bool
	client      *http.Client
	logger      *log.Logger

	mu       sync.Mutex
	inflight map[string]*beatmapFetch
}

func newBeatmapStore(
	directories []string,
	writeDir string,
	proxy bool,
	client *http.Client,
	logger *log.Logger,
) *beatmapStore {
	if writeDir == "" && len(directories) > 0 {
		writeDir = directories[0]
	}
	if client == nil {
		client = &http.Client{
			Timeout: 2 * time.Minute,
			CheckRedirect: func(request *http.Request, via []*http.Request) error {
				if len(via) >= 4 {
					return errors.New("too many beatmap source redirects")
				}
				if request.URL.Scheme != "https" || request.URL.Host == "" || request.URL.User != nil {
					return errors.New("beatmap source redirected outside HTTPS")
				}
				return nil
			},
		}
	}
	return &beatmapStore{
		directories: append([]string(nil), directories...),
		writeDir:    writeDir,
		proxy:       proxy,
		client:      client,
		logger:      logger,
		inflight:    make(map[string]*beatmapFetch),
	}
}

func (s *beatmapStore) open(ctx context.Context, song arena.Song) (*beatmapArchive, error) {
	archive, localErr := s.openLocal(song)
	if localErr == nil {
		return archive, nil
	}
	if !s.proxy {
		return nil, localErr
	}

	raw, err := s.fetch(ctx, song)
	if err != nil {
		return nil, errors.Join(localErr, err)
	}
	if archive, err := s.openLocal(song); err == nil {
		return archive, nil
	}
	reader := bytes.NewReader(raw)
	return &beatmapArchive{
		ReadSeeker: reader,
		closer:     io.NopCloser(reader),
	}, nil
}

func (s *beatmapStore) openLocal(song arena.Song) (*beatmapArchive, error) {
	var openErrors []error
	for _, directory := range s.directories {
		for _, archiveName := range archiveNames(song) {
			path := filepath.Join(directory, archiveName)
			file, err := os.Open(path)
			if err != nil {
				if !errors.Is(err, os.ErrNotExist) {
					openErrors = append(openErrors, fmt.Errorf("%s: %w", path, err))
				}
				continue
			}
			info, err := verifyBeatmapFile(file, song.ArchiveSHA256)
			if err != nil {
				_ = file.Close()
				openErrors = append(openErrors, fmt.Errorf("%s: %w", path, err))
				continue
			}
			return &beatmapArchive{
				ReadSeeker: file,
				closer:     file,
				modTime:    info.ModTime(),
			}, nil
		}
	}
	if len(openErrors) == 0 {
		return nil, errBeatmapUnavailable
	}
	return nil, errors.Join(append([]error{errBeatmapUnavailable}, openErrors...)...)
}

func verifyBeatmapFile(file *os.File, expectedHash string) (os.FileInfo, error) {
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if info.Size() <= 0 || info.Size() > maximumBeatmapArchiveSize {
		return nil, fmt.Errorf("archive size %d is outside the allowed range", info.Size())
	}
	hash := sha256.New()
	read, err := io.Copy(hash, io.LimitReader(file, maximumBeatmapArchiveSize+1))
	if err != nil {
		return nil, err
	}
	if read > maximumBeatmapArchiveSize {
		return nil, errors.New("archive exceeds 96 MiB")
	}
	actualHash := fmt.Sprintf("%x", hash.Sum(nil))
	if !strings.EqualFold(actualHash, expectedHash) {
		return nil, fmt.Errorf("archive checksum is %s, want %s", actualHash, expectedHash)
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return nil, err
	}
	return info, nil
}

func (s *beatmapStore) fetch(ctx context.Context, song arena.Song) ([]byte, error) {
	s.mu.Lock()
	if existing := s.inflight[song.ID]; existing != nil {
		s.mu.Unlock()
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-existing.done:
			return existing.raw, existing.err
		}
	}
	fetch := &beatmapFetch{done: make(chan struct{})}
	s.inflight[song.ID] = fetch
	s.mu.Unlock()

	fetch.raw, fetch.err = s.fetchRemote(ctx, song)
	if fetch.err == nil {
		if err := s.persist(song, fetch.raw); err != nil && s.logger != nil {
			s.logger.Printf("cache beatmap %s: %v", song.ID, err)
		}
	}
	close(fetch.done)

	s.mu.Lock()
	delete(s.inflight, song.ID)
	s.mu.Unlock()
	return fetch.raw, fetch.err
}

func (s *beatmapStore) fetchRemote(ctx context.Context, song arena.Song) ([]byte, error) {
	source, err := url.Parse(song.SourceURL)
	if err != nil || source.Scheme != "https" || source.Host == "" || source.User != nil {
		return nil, errors.New("beatmap source is not a valid HTTPS URL")
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, source.String(), http.NoBody)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Accept", "application/x-osu-beatmap-archive, application/octet-stream")
	request.Header.Set("User-Agent", "Rythme-Coordinator/6")

	response, err := s.client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("fetch pinned source: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("fetch pinned source: HTTP %d", response.StatusCode)
	}
	if response.ContentLength > maximumBeatmapArchiveSize {
		return nil, errors.New("pinned source archive exceeds 96 MiB")
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, maximumBeatmapArchiveSize+1))
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 || len(raw) > maximumBeatmapArchiveSize {
		return nil, errors.New("pinned source archive has an invalid size")
	}
	actualHash := fmt.Sprintf("%x", sha256.Sum256(raw))
	if !strings.EqualFold(actualHash, song.ArchiveSHA256) {
		return nil, fmt.Errorf("pinned source checksum is %s, want %s", actualHash, song.ArchiveSHA256)
	}
	return raw, nil
}

func (s *beatmapStore) persist(song arena.Song, raw []byte) error {
	if s.writeDir == "" {
		return errors.New("no writable beatmap cache is configured")
	}
	if err := os.MkdirAll(s.writeDir, 0o755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(s.writeDir, ".rythme-"+song.ID+"-*")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	removeTemporary := true
	defer func() {
		_ = temporary.Close()
		if removeTemporary {
			_ = os.Remove(temporaryPath)
		}
	}()

	if _, err := temporary.Write(raw); err != nil {
		return err
	}
	if err := temporary.Sync(); err != nil {
		return err
	}
	if err := temporary.Chmod(0o644); err != nil {
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	finalPath := filepath.Join(s.writeDir, song.ID+".osz")
	if err := os.Rename(temporaryPath, finalPath); err != nil {
		return err
	}
	removeTemporary = false
	return nil
}

func archiveNames(song arena.Song) []string {
	return []string{
		song.ID + ".osz",
		strconv.FormatInt(song.BeatmapsetID, 10) + ".osz",
	}
}
