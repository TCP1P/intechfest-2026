package arena

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"time"
)

const (
	frameVersion      byte = 1
	maximumFrameBytes      = 64 << 10
)

func (a *Arena) HandleFrame(token string, raw []byte, now time.Time) ([]byte, error) {
	reader, action, err := openFrame(raw)
	if err != nil {
		return nil, fmt.Errorf("%w: invalid arena frame", ErrBadRequest)
	}
	writer := newFrameWriter(action)

	switch action {
	case frameEnroll:
		if !reader.done() {
			return nil, fmt.Errorf("%w: invalid enrollment frame", ErrBadRequest)
		}
		value, err := a.Enroll(token, now)
		if err != nil {
			return nil, err
		}
		writer.text(value.HeatID)
		writer.text(value.HeatTicketID)

	case frameReady:
		ticket, err := reader.text(512)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid ready frame", ErrBadRequest)
		}
		envelope, err := reader.blob(maximumEnvelopeBytes)
		if err != nil || !reader.done() {
			return nil, fmt.Errorf("%w: invalid ready frame", ErrBadRequest)
		}
		value, err := a.Ready(token, ticket, base64.StdEncoding.EncodeToString(envelope), now)
		if err != nil {
			return nil, err
		}
		capsule, err := base64.StdEncoding.Strict().DecodeString(value.PolicyCapsule)
		if err != nil {
			return nil, errors.New("internal ready encoding failed")
		}
		writer.u8(1)
		writer.profile(value.LockedMods)
		writer.blob(capsule)

	case frameCues:
		ticket, err := reader.text(512)
		if err != nil || !reader.done() {
			return nil, fmt.Errorf("%w: invalid cue frame", ErrBadRequest)
		}
		value, err := a.Cues(token, ticket, now)
		if err != nil {
			return nil, err
		}
		writer.text(value.HeatID)
		writer.i64(value.ServerNowUnixMS)
		writer.u16(len(value.Cues))
		for _, cue := range value.Cues {
			writer.i32(cue.NoteIndex)
			writer.i64(cue.OpensUnixMS)
			writer.i64(cue.ExpiresUnixMS)
			writer.text(cue.Token)
		}

	case frameInput:
		ticket, err := reader.text(512)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid input frame", ErrBadRequest)
		}
		rulesetByte, err := reader.u8()
		if err != nil {
			return nil, fmt.Errorf("%w: invalid input frame", ErrBadRequest)
		}
		ruleset, ok := rulesetFromWire(rulesetByte)
		if !ok {
			return nil, fmt.Errorf("%w: invalid input frame", ErrBadRequest)
		}
		count, err := reader.u16()
		if err != nil || count < 1 || count > 64 {
			return nil, fmt.Errorf("%w: invalid input frame", ErrBadRequest)
		}
		frames := make([]InputFrame, 0, count)
		for index := 0; index < count; index++ {
			sequence, sequenceErr := reader.u64()
			noteIndex, indexErr := reader.i32()
			cue, cueErr := reader.text(512)
			actionBits, actionErr := reader.u16()
			pointer, pointerErr := reader.u8()
			if sequenceErr != nil || indexErr != nil || cueErr != nil || actionErr != nil || pointerErr != nil || pointer > 1 {
				return nil, fmt.Errorf("%w: invalid input frame", ErrBadRequest)
			}
			frame := InputFrame{
				HeatTicketID: ticket, Sequence: sequence, Ruleset: ruleset,
				NoteIndex: noteIndex, CueToken: cue, ActionBits: uint16(actionBits),
			}
			if pointer == 1 {
				x, xErr := reader.u16()
				y, yErr := reader.u16()
				if xErr != nil || yErr != nil {
					return nil, fmt.Errorf("%w: invalid input frame", ErrBadRequest)
				}
				xValue, yValue := uint16(x), uint16(y)
				frame.PointerX, frame.PointerY = &xValue, &yValue
			}
			frames = append(frames, frame)
		}
		if !reader.done() {
			return nil, fmt.Errorf("%w: invalid input frame", ErrBadRequest)
		}
		value, err := a.SubmitFrames(token, frames, now)
		if err != nil {
			return nil, err
		}
		writer.i32(value.Accepted)
		writer.text(value.Digest)

	case frameResult:
		ticket, err := reader.text(512)
		if err != nil || !reader.done() {
			return nil, fmt.Errorf("%w: invalid result frame", ErrBadRequest)
		}
		value, _, err := a.Result(token, ticket, now)
		if err != nil {
			return nil, err
		}
		capsule, err := base64.StdEncoding.Strict().DecodeString(value.JudgeCapsule)
		if err != nil {
			return nil, errors.New("internal result encoding failed")
		}
		writer.judge(value)
		writer.blob(capsule)

	case frameFinish:
		readyRaw, readyErr := reader.blob(maximumCapsuleBytes)
		judgeRaw, judgeErr := reader.blob(maximumCapsuleBytes)
		acronym, acronymErr := reader.text(64)
		rate, rateErr := reader.i32()
		settings, settingsErr := reader.text(128)
		proof, proofErr := reader.fixed(32)
		if readyErr != nil || judgeErr != nil || acronymErr != nil || rateErr != nil || settingsErr != nil || proofErr != nil || !reader.done() {
			return nil, fmt.Errorf("%w: invalid completion frame", ErrBadRequest)
		}
		readyEncoded := base64.StdEncoding.EncodeToString(readyRaw)
		judgedEncoded := base64.StdEncoding.EncodeToString(judgeRaw)
		ready, _, err := openReadyCapsule(readyEncoded)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid completion frame", ErrBadRequest)
		}
		judged, _, err := openJudgeCapsule(judgedEncoded)
		if err != nil {
			return nil, fmt.Errorf("%w: invalid completion frame", ErrBadRequest)
		}
		manifest := RunManifest{
			Schema: manifestSchema, TeamID: ready.TeamID, HeatID: ready.HeatID,
			HeatTicketID: ready.Ticket, SongID: ready.SongID, Ruleset: ready.Ruleset,
			ChartDigest: ready.ChartDigest, StartAtUnixMS: ready.StartAtUnixMS,
			InputDigest: judged.InputDigest, ClientBuild: clientBuild,
			AttestedMods: ModClaim{Acronym: acronym, RateMilli: rate, SettingsHash: settings},
			ReadyCapsule: readyEncoded, JudgeCapsule: judgedEncoded, NativeProof: hex.EncodeToString(proof),
		}
		value, err := a.SubmitManifest(token, manifest, now)
		if err != nil {
			return nil, err
		}
		writer.settlement(value.Settlement)
		writer.u8(boolByte(value.TookCrown))

	default:
		return nil, fmt.Errorf("%w: invalid arena frame", ErrBadRequest)
	}

	if writer.err != nil || len(writer.Bytes()) > maximumFrameBytes {
		return nil, errors.New("internal frame encoding failed")
	}
	return writer.Bytes(), nil
}

func openFrame(raw []byte) (*frameReader, byte, error) {
	if len(raw) < 6 || len(raw) > maximumFrameBytes || !bytes.Equal(raw[:4], frameMagic[:]) || raw[4] != frameVersion {
		return nil, 0, errors.New("invalid frame")
	}
	return &frameReader{value: raw, offset: 6}, raw[5], nil
}

func rulesetFromWire(value byte) (Ruleset, bool) {
	switch value {
	case 0:
		return RulesetOsu, true
	case 1:
		return RulesetTaiko, true
	case 2:
		return RulesetFruits, true
	case 3:
		return RulesetMania, true
	default:
		return "", false
	}
}

type frameReader struct {
	value  []byte
	offset int
}

func (r *frameReader) fixed(length int) ([]byte, error) {
	if length < 0 || r.offset > len(r.value)-length {
		return nil, errors.New("truncated field")
	}
	value := append([]byte(nil), r.value[r.offset:r.offset+length]...)
	r.offset += length
	return value, nil
}

func (r *frameReader) u8() (byte, error) {
	value, err := r.fixed(1)
	if err != nil {
		return 0, err
	}
	return value[0], nil
}

func (r *frameReader) u16() (int, error) {
	value, err := r.fixed(2)
	if err != nil {
		return 0, err
	}
	return int(binary.LittleEndian.Uint16(value)), nil
}

func (r *frameReader) u64() (uint64, error) {
	value, err := r.fixed(8)
	if err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint64(value), nil
}

func (r *frameReader) i32() (int, error) {
	value, err := r.fixed(4)
	if err != nil {
		return 0, err
	}
	return int(int32(binary.LittleEndian.Uint32(value))), nil
}

func (r *frameReader) blob(maximum int) ([]byte, error) {
	length, err := r.u16()
	if err != nil || length > maximum {
		return nil, errors.New("invalid blob")
	}
	return r.fixed(length)
}

func (r *frameReader) text(maximum int) (string, error) {
	value, err := r.blob(maximum)
	if err != nil {
		return "", err
	}
	return string(value), nil
}

func (r *frameReader) done() bool { return r.offset == len(r.value) }

type frameWriter struct {
	bytes.Buffer
	err error
}

func newFrameWriter(action byte) *frameWriter {
	w := &frameWriter{}
	w.Write(frameMagic[:])
	w.WriteByte(frameVersion)
	w.WriteByte(action)
	return w
}

func (w *frameWriter) u8(value byte) { _ = w.WriteByte(value) }

func (w *frameWriter) u16(value int) {
	if w.err != nil {
		return
	}
	if value < 0 || value > math.MaxUint16 {
		w.err = errors.New("integer overflow")
		return
	}
	var raw [2]byte
	binary.LittleEndian.PutUint16(raw[:], uint16(value))
	_, w.err = w.Write(raw[:])
}

func (w *frameWriter) i32(value int) {
	var raw [4]byte
	binary.LittleEndian.PutUint32(raw[:], uint32(int32(value)))
	_, w.err = w.Write(raw[:])
}

func (w *frameWriter) i64(value int64) {
	var raw [8]byte
	binary.LittleEndian.PutUint64(raw[:], uint64(value))
	_, w.err = w.Write(raw[:])
}

func (w *frameWriter) f64(value float64) { w.i64(int64(math.Float64bits(value))) }

func (w *frameWriter) blob(value []byte) {
	w.u16(len(value))
	if w.err == nil {
		_, w.err = w.Write(value)
	}
}

func (w *frameWriter) text(value string) { w.blob([]byte(value)) }

func (w *frameWriter) profile(value AppliedModProfile) {
	w.text(value.Acronym)
	w.i32(value.RateMilli)
	w.text(value.SettingsHash)
	w.i32(value.ScoreMultiplierMilli)
}

func (w *frameWriter) claim(value ModClaim) {
	w.text(value.Acronym)
	w.i32(value.RateMilli)
	w.text(value.SettingsHash)
}

func (w *frameWriter) judge(value JudgeResult) {
	w.i32(value.HitCount)
	w.i32(value.NoteCount)
	w.i64(value.TotalScoreWithoutMods)
	w.i64(value.PerfectRaw)
	w.i64(value.RawBasis)
	w.f64(value.Accuracy)
	w.i32(value.MaxCombo)
	w.text(value.InputDigest)
}

func (w *frameWriter) settlement(value Settlement) {
	w.text(value.TeamID)
	w.text(value.TeamName)
	w.u8(boolByte(value.Eligible))
	w.text(value.Reason)
	if value.AppliedMods == nil {
		w.u8(0)
	} else {
		w.u8(1)
		w.profile(*value.AppliedMods)
	}
	if value.AttestedMods == nil {
		w.u8(0)
	} else {
		w.u8(1)
		w.claim(*value.AttestedMods)
	}
	w.f64(value.AppliedMultiplier)
	w.f64(value.AttestedMultiplier)
	w.f64(value.Multiplier)
	w.i64(value.SettledScore)
	w.judge(value.Judge)
	w.i64(value.SeriesIndex)
	w.i32(value.SeriesLeg)
}

func boolByte(value bool) byte {
	if value {
		return 1
	}
	return 0
}
