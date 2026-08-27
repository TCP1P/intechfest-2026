package arena

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"strings"
)

const (
	modifierEnvelopeVersion byte = 2
	readyCapsuleKind        byte = 0x91
	judgeCapsuleKind        byte = 0x4c
	policyCapsuleVersion    byte = 1
	maximumEnvelopeBytes         = 2048
	maximumCapsuleBytes          = 8192
)

var policyCapsuleMagic = [4]byte{0xc7, 0x51, 0xa9, 0x05}

type policySpec struct {
	magic    [4]byte
	tag      byte
	selector byte
	stride   byte
	program  []byte
}

type readyCapsule struct {
	TeamID           string
	HeatID           string
	Ticket           string
	SongID           string
	Ruleset          Ruleset
	ChartDigest      string
	Nonce            string
	StartAtUnixMS    int64
	ModifierEnvelope []byte
	Program          []byte
}

type judgeCapsule struct {
	TeamID      string
	HeatID      string
	Ticket      string
	InputDigest string
	RawBasis    int64
}

func decodeModifierEnvelope(encoded string) ([]byte, error) {
	raw, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(raw) == 0 || len(raw) > maximumEnvelopeBytes {
		return nil, errors.New("modifier envelope is malformed")
	}
	if base64.StdEncoding.EncodeToString(raw) != encoded {
		return nil, errors.New("modifier envelope is not canonically encoded")
	}
	return raw, nil
}

func derivePolicySpec(nonce string) policySpec {
	mac := hmac.New(sha256.New, policyVariantKey[:])
	_, _ = mac.Write(frameMagic[:])
	_, _ = mac.Write([]byte(nonce))
	digest := mac.Sum(nil)
	spec := policySpec{
		tag:      digest[4] ^ policyProfileTag,
		selector: digest[5] | 0x80,
		stride:   digest[6]%15 + 1,
	}
	for index := range spec.magic {
		spec.magic[index] = digest[index] ^ frameMagic[index]
	}
	if spec.tag == 0 {
		spec.tag = policyProfileTag | 1
	}
	spec.program = []byte{
		vmSelectIndex, spec.tag, spec.selector, spec.stride,
		spec.magic[0], spec.magic[1], spec.magic[2], spec.magic[3],
		vmValidate, vmEmit, vmHalt,
	}
	return spec
}

func parsePolicyProgram(program []byte) (policySpec, error) {
	if len(program) != 11 || program[0] != vmSelectIndex ||
		program[8] != vmValidate || program[9] != vmEmit || program[10] != vmHalt ||
		program[1] == 0 || program[3] == 0 {
		return policySpec{}, errors.New("policy program is malformed")
	}
	return policySpec{
		tag: program[1], selector: program[2], stride: program[3],
		magic:   [4]byte{program[4], program[5], program[6], program[7]},
		program: append([]byte(nil), program...),
	}, nil
}

// readyModifierProfile selects the coordinator view of an issued policy program.
func readyModifierProfile(envelope, program []byte) ([]string, error) {
	spec, err := parsePolicyProgram(program)
	if err != nil {
		return nil, err
	}
	profiles, err := modifierProfiles(envelope, spec)
	if err != nil {
		return nil, err
	}
	index := (int(spec.selector) + int(spec.stride)) % len(profiles)
	return profiles[index], nil
}

func modifierProfiles(envelope []byte, spec policySpec) ([][]string, error) {
	if len(envelope) < 6 || !bytes.Equal(envelope[:4], spec.magic[:]) || envelope[4] != modifierEnvelopeVersion {
		return nil, errors.New("unsupported modifier envelope")
	}
	entryCount := int(envelope[5])
	if entryCount == 0 || entryCount > 16 {
		return nil, errors.New("modifier envelope has an invalid entry count")
	}

	offset := 6
	profiles := make([][]string, 0, entryCount)
	for entry := 0; entry < entryCount; entry++ {
		if offset+3 > len(envelope) {
			return nil, errors.New("modifier envelope ended inside a TLV")
		}
		tag := envelope[offset]
		length := int(binary.LittleEndian.Uint16(envelope[offset+1 : offset+3]))
		offset += 3
		if length < 0 || offset+length > len(envelope) {
			return nil, errors.New("modifier envelope TLV length is invalid")
		}
		value := envelope[offset : offset+length]
		offset += length
		if tag != spec.tag {
			continue
		}
		profile, err := decodeProfileValue(value)
		if err != nil {
			return nil, err
		}
		profiles = append(profiles, profile)
	}
	if offset != len(envelope) {
		return nil, errors.New("modifier envelope has trailing data")
	}
	if len(profiles) == 0 {
		return nil, errors.New("modifier envelope has no profile")
	}
	return profiles, nil
}

func decodeProfileValue(value []byte) ([]string, error) {
	if len(value) < 1 {
		return nil, errors.New("modifier profile is empty")
	}
	count := int(value[0])
	if count > 12 {
		return nil, errors.New("modifier profile has too many entries")
	}
	offset := 1
	mods := make([]string, 0, count)
	for index := 0; index < count; index++ {
		if offset >= len(value) {
			return nil, errors.New("modifier profile ended before its entries")
		}
		length := int(value[offset])
		offset++
		if length == 0 || length > 8 || offset+length > len(value) {
			return nil, errors.New("modifier acronym length is invalid")
		}
		acronym := string(value[offset : offset+length])
		offset += length
		if strings.ToUpper(acronym) != acronym {
			return nil, errors.New("modifier acronyms must be uppercase")
		}
		for _, character := range acronym {
			if (character < 'A' || character > 'Z') && (character < '0' || character > '9') {
				return nil, errors.New("modifier acronym contains an invalid character")
			}
		}
		mods = append(mods, acronym)
	}
	if offset != len(value) {
		return nil, errors.New("modifier profile has trailing data")
	}
	return mods, nil
}

func executePolicyVM(ruleset Ruleset, envelope, program []byte) (ModClaim, error) {
	spec, err := parsePolicyProgram(program)
	if err != nil {
		return ModClaim{}, err
	}
	profiles, err := modifierProfiles(envelope, spec)
	if err != nil {
		return ModClaim{}, err
	}
	var selected []string
	var canonical *AppliedModProfile
	emitted := false
	for pc := 0; pc < len(program); {
		opcode := program[pc]
		pc++
		switch opcode {
		case vmSelectIndex:
			if pc+7 > len(program) {
				return ModClaim{}, errors.New("policy scan instruction is truncated")
			}
			tag := program[pc]
			selector := int(int8(program[pc+1]))
			stride := int(program[pc+2])
			magic := program[pc+3 : pc+7]
			pc += 7
			if tag != spec.tag || !bytes.Equal(magic, spec.magic[:]) {
				return ModClaim{}, errors.New("policy requested an unknown field")
			}
			index := (selector + stride) % len(profiles)
			if index < 0 {
				index += len(profiles)
			}
			selected = profiles[index]
		case vmValidate:
			if selected == nil {
				return ModClaim{}, errors.New("policy validated before scanning")
			}
			profile, err := canonicalAppliedMods(ruleset, selected)
			if err != nil {
				return ModClaim{}, err
			}
			if profile.Acronym != "NM" && profile.Acronym != "HT" {
				return ModClaim{}, errors.New("profile is outside the attestation policy")
			}
			canonical = &profile
		case vmEmit:
			if canonical == nil {
				return ModClaim{}, errors.New("policy emitted before validation")
			}
			emitted = true
		case vmHalt:
			if pc != len(program) || !emitted || canonical == nil {
				return ModClaim{}, errors.New("policy halted without a result")
			}
			return ModClaim{
				Acronym: canonical.Acronym, RateMilli: canonical.RateMilli,
				SettingsHash: canonical.SettingsHash,
			}, nil
		default:
			return ModClaim{}, fmt.Errorf("unknown policy opcode 0x%02x", opcode)
		}
	}
	return ModClaim{}, errors.New("policy program did not halt")
}

func sealReadyCapsule(value readyCapsule) string {
	var payload bytes.Buffer
	payload.Write(policyCapsuleMagic[:])
	payload.WriteByte(readyCapsuleKind)
	payload.WriteByte(policyCapsuleVersion)
	writeString(&payload, value.TeamID)
	writeString(&payload, value.HeatID)
	writeString(&payload, value.Ticket)
	writeString(&payload, value.SongID)
	writeString(&payload, string(value.Ruleset))
	writeString(&payload, value.ChartDigest)
	writeString(&payload, value.Nonce)
	_ = binary.Write(&payload, binary.LittleEndian, value.StartAtUnixMS)
	writeBytes(&payload, value.ModifierEnvelope)
	writeBytes(&payload, value.Program)
	return sealPolicyPayload(payload.Bytes())
}

func sealJudgeCapsule(value judgeCapsule) string {
	var payload bytes.Buffer
	payload.Write(policyCapsuleMagic[:])
	payload.WriteByte(judgeCapsuleKind)
	payload.WriteByte(policyCapsuleVersion)
	writeString(&payload, value.TeamID)
	writeString(&payload, value.HeatID)
	writeString(&payload, value.Ticket)
	writeString(&payload, value.InputDigest)
	_ = binary.Write(&payload, binary.LittleEndian, value.RawBasis)
	return sealPolicyPayload(payload.Bytes())
}

func sealPolicyPayload(payload []byte) string {
	mac := hmac.New(sha256.New, policyCapsuleKey[:])
	_, _ = mac.Write(payload)
	sealed := append(append([]byte(nil), payload...), mac.Sum(nil)...)
	return base64.StdEncoding.EncodeToString(sealed)
}

func openReadyCapsule(encoded string) (readyCapsule, []byte, error) {
	payload, raw, err := openPolicyPayload(encoded, readyCapsuleKind)
	if err != nil {
		return readyCapsule{}, nil, err
	}
	reader := capsuleReader{value: payload[6:]}
	value := readyCapsule{}
	if value.TeamID, err = reader.string(); err != nil {
		return readyCapsule{}, nil, err
	}
	if value.HeatID, err = reader.string(); err != nil {
		return readyCapsule{}, nil, err
	}
	if value.Ticket, err = reader.string(); err != nil {
		return readyCapsule{}, nil, err
	}
	if value.SongID, err = reader.string(); err != nil {
		return readyCapsule{}, nil, err
	}
	ruleset, err := reader.string()
	if err != nil {
		return readyCapsule{}, nil, err
	}
	value.Ruleset = Ruleset(ruleset)
	if value.ChartDigest, err = reader.string(); err != nil {
		return readyCapsule{}, nil, err
	}
	if value.Nonce, err = reader.string(); err != nil {
		return readyCapsule{}, nil, err
	}
	if value.StartAtUnixMS, err = reader.int64(); err != nil {
		return readyCapsule{}, nil, err
	}
	if value.ModifierEnvelope, err = reader.bytes(); err != nil {
		return readyCapsule{}, nil, err
	}
	if value.Program, err = reader.bytes(); err != nil {
		return readyCapsule{}, nil, err
	}
	if !reader.done() {
		return readyCapsule{}, nil, errors.New("ready capsule has trailing fields")
	}
	return value, raw, nil
}

func openJudgeCapsule(encoded string) (judgeCapsule, []byte, error) {
	payload, raw, err := openPolicyPayload(encoded, judgeCapsuleKind)
	if err != nil {
		return judgeCapsule{}, nil, err
	}
	reader := capsuleReader{value: payload[6:]}
	value := judgeCapsule{}
	if value.TeamID, err = reader.string(); err != nil {
		return judgeCapsule{}, nil, err
	}
	if value.HeatID, err = reader.string(); err != nil {
		return judgeCapsule{}, nil, err
	}
	if value.Ticket, err = reader.string(); err != nil {
		return judgeCapsule{}, nil, err
	}
	if value.InputDigest, err = reader.string(); err != nil {
		return judgeCapsule{}, nil, err
	}
	if value.RawBasis, err = reader.int64(); err != nil {
		return judgeCapsule{}, nil, err
	}
	if !reader.done() {
		return judgeCapsule{}, nil, errors.New("judge capsule has trailing fields")
	}
	return value, raw, nil
}

func openPolicyPayload(encoded string, kind byte) ([]byte, []byte, error) {
	raw, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(raw) < 6+sha256.Size || len(raw) > maximumCapsuleBytes || base64.StdEncoding.EncodeToString(raw) != encoded {
		return nil, nil, errors.New("policy capsule is malformed")
	}
	payload := raw[:len(raw)-sha256.Size]
	provided := raw[len(raw)-sha256.Size:]
	if !bytes.Equal(payload[:4], policyCapsuleMagic[:]) || payload[4] != kind || payload[5] != policyCapsuleVersion {
		return nil, nil, errors.New("policy capsule kind or version is invalid")
	}
	mac := hmac.New(sha256.New, policyCapsuleKey[:])
	_, _ = mac.Write(payload)
	if !hmac.Equal(provided, mac.Sum(nil)) {
		return nil, nil, errors.New("policy capsule signature did not verify")
	}
	return payload, raw, nil
}

func nativeProof(readyRaw, judgeRaw []byte, claim ModClaim) []byte {
	readyDigest := sha256.Sum256(readyRaw)
	judgeDigest := sha256.Sum256(judgeRaw)
	var payload bytes.Buffer
	payload.Write(frameMagic[:])
	payload.WriteByte(frameFinish)
	payload.Write(readyDigest[:])
	payload.Write(judgeDigest[:])
	payload.WriteByte(byte(len(claim.Acronym)))
	payload.WriteString(claim.Acronym)
	_ = binary.Write(&payload, binary.LittleEndian, int32(claim.RateMilli))
	payload.WriteString(claim.SettingsHash)
	mac := hmac.New(sha256.New, policyProofKey[:])
	_, _ = mac.Write(payload.Bytes())
	return mac.Sum(nil)
}

func writeString(buffer *bytes.Buffer, value string) { writeBytes(buffer, []byte(value)) }

func writeBytes(buffer *bytes.Buffer, value []byte) {
	_ = binary.Write(buffer, binary.LittleEndian, uint16(len(value)))
	_, _ = buffer.Write(value)
}

type capsuleReader struct {
	value  []byte
	offset int
}

func (reader *capsuleReader) bytes() ([]byte, error) {
	if reader.offset+2 > len(reader.value) {
		return nil, errors.New("capsule field length is truncated")
	}
	length := int(binary.LittleEndian.Uint16(reader.value[reader.offset : reader.offset+2]))
	reader.offset += 2
	if reader.offset+length > len(reader.value) {
		return nil, errors.New("capsule field is truncated")
	}
	result := append([]byte(nil), reader.value[reader.offset:reader.offset+length]...)
	reader.offset += length
	return result, nil
}

func (reader *capsuleReader) string() (string, error) {
	value, err := reader.bytes()
	if err != nil {
		return "", err
	}
	if len(value) > 512 {
		return "", errors.New("capsule string is oversized")
	}
	return string(value), nil
}

func (reader *capsuleReader) int64() (int64, error) {
	if reader.offset+8 > len(reader.value) {
		return 0, errors.New("capsule integer is truncated")
	}
	value := int64(binary.LittleEndian.Uint64(reader.value[reader.offset : reader.offset+8]))
	reader.offset += 8
	return value, nil
}

func (reader *capsuleReader) done() bool { return reader.offset == len(reader.value) }
