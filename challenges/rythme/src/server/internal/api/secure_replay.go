package api

import (
	"context"
	"crypto/sha256"
	"net/http"
	"sync"
	"time"

	"rythme.local/server/internal/arena"
)

const (
	secureReplayShardCount = 64
	secureReplayWindow     = 30 * time.Second
	secureReplayRetention  = 2 * time.Minute
	secureReplayPruneEvery = 15 * time.Second
)

type secureRequestIdentity struct {
	channelID string
	method    string
	target    string
	previous  string
	signature string
	sequence  uint64
	sentAtMS  int64
	body      [sha256.Size]byte
}

type secureResponse struct {
	header http.Header
	body   []byte
	status int
}

type secureReplay struct {
	identity  secureRequestIdentity
	receipt   arena.ChannelReceipt
	createdAt time.Time
	done      chan struct{}
	response  secureResponse
	shard     *secureReplayShard
}

type secureReplayShard struct {
	mu         sync.Mutex
	entries    map[string]*secureReplay
	lastPruned time.Time
}

type secureReplayCache struct {
	shards [secureReplayShardCount]secureReplayShard
}

func newSecureRequestIdentity(
	proof arena.ChannelRequestProof,
	method string,
	target string,
	body []byte,
) secureRequestIdentity {
	return secureRequestIdentity{
		channelID: proof.ChannelID,
		method:    method,
		target:    target,
		previous:  proof.Previous,
		signature: proof.Signature,
		sequence:  proof.Sequence,
		sentAtMS:  proof.SentAtMS,
		body:      sha256.Sum256(body),
	}
}

func (c *secureReplayCache) begin(
	ctx context.Context,
	token string,
	identity secureRequestIdentity,
	verify func() (arena.ChannelReceipt, error),
) (*secureReplay, bool, error) {
	shard := c.shard(token)
	for {
		now := time.Now()
		shard.mu.Lock()
		shard.prune(now)

		if current := shard.entries[token]; current != nil {
			if current.identity == identity && now.Sub(current.createdAt) <= secureReplayWindow {
				done := current.done
				shard.mu.Unlock()
				select {
				case <-done:
					return current, false, nil
				case <-ctx.Done():
					return nil, false, ctx.Err()
				}
			}

			select {
			case <-current.done:
			default:
				done := current.done
				shard.mu.Unlock()
				select {
				case <-done:
					continue
				case <-ctx.Done():
					return nil, false, ctx.Err()
				}
			}
		}

		receipt, err := verify()
		if err != nil {
			shard.mu.Unlock()
			return nil, false, err
		}
		replay := &secureReplay{
			identity:  identity,
			receipt:   receipt,
			createdAt: now,
			done:      make(chan struct{}),
			shard:     shard,
		}
		if shard.entries == nil {
			shard.entries = make(map[string]*secureReplay)
		}
		shard.entries[token] = replay
		shard.mu.Unlock()
		return replay, true, nil
	}
}

func (c *secureReplayCache) complete(replay *secureReplay, response secureResponse) {
	replay.shard.mu.Lock()
	replay.response = response
	close(replay.done)
	replay.shard.mu.Unlock()
}

func (c *secureReplayCache) shard(value string) *secureReplayShard {
	const (
		fnvOffset = uint64(14695981039346656037)
		fnvPrime  = uint64(1099511628211)
	)
	hash := fnvOffset
	for index := range len(value) {
		hash ^= uint64(value[index])
		hash *= fnvPrime
	}
	return &c.shards[hash&(secureReplayShardCount-1)]
}

func (s *secureReplayShard) prune(now time.Time) {
	if !s.lastPruned.IsZero() && now.Sub(s.lastPruned) < secureReplayPruneEvery {
		return
	}
	s.lastPruned = now
	for token, replay := range s.entries {
		if now.Sub(replay.createdAt) <= secureReplayRetention {
			continue
		}
		select {
		case <-replay.done:
			delete(s.entries, token)
		default:
		}
	}
}

func writeSecureResponse(w http.ResponseWriter, response secureResponse) {
	for key, values := range response.header {
		for _, value := range values {
			w.Header().Add(key, value)
		}
	}
	w.WriteHeader(response.status)
	_, _ = w.Write(response.body)
}
