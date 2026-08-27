package arena

import (
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"strconv"
	"strings"
	"time"
)

const (
	SecureChannelProtocol = "rythme-secure-channel-v3"
	channelKeySize        = 32
	channelKeyCount       = 3
	channelLifetime       = 6 * time.Hour
	channelClockSkew      = 2 * time.Minute
)

var initialChannelMAC = strings.Repeat("0", sha256.Size*2)

type ChannelRequestProof struct {
	ChannelID string
	Sequence  uint64
	SentAtMS  int64
	Previous  string
	Signature string
}

// ChannelReceipt is deliberately opaque to the HTTP layer. It contains the
// response key selected only after a request has passed the channel ratchet.
type ChannelReceipt struct {
	Sequence uint64

	channelID   string
	requestMAC  []byte
	responseKey []byte
}

func (a *Arena) CreateSecureSession(
	teamID string,
	teamName string,
	clientPublicKey string,
	clientNonce string,
	now time.Time,
) (SessionHandshake, error) {
	clientPublicDER, err := base64.StdEncoding.DecodeString(clientPublicKey)
	if err != nil || len(clientPublicDER) == 0 {
		return SessionHandshake{}, fmt.Errorf("%w: malformed channel public key", ErrBadRequest)
	}
	parsed, err := x509.ParsePKIXPublicKey(clientPublicDER)
	if err != nil {
		return SessionHandshake{}, fmt.Errorf("%w: unsupported channel public key", ErrBadRequest)
	}

	var clientECDH *ecdh.PublicKey
	switch public := parsed.(type) {
	case *ecdh.PublicKey:
		clientECDH = public
	case *ecdsa.PublicKey:
		clientECDH, err = public.ECDH()
	default:
		err = fmt.Errorf("not an elliptic-curve public key")
	}
	if err != nil || clientECDH.Curve() != ecdh.P256() {
		return SessionHandshake{}, fmt.Errorf("%w: channel requires a P-256 key", ErrBadRequest)
	}

	clientNonceBytes, err := base64.StdEncoding.DecodeString(clientNonce)
	if err != nil || len(clientNonceBytes) != 32 {
		return SessionHandshake{}, fmt.Errorf("%w: channel nonce must contain 32 bytes", ErrBadRequest)
	}

	serverPrivate, err := ecdh.P256().GenerateKey(rand.Reader)
	if err != nil {
		return SessionHandshake{}, fmt.Errorf("generate channel key: %w", err)
	}
	shared, err := serverPrivate.ECDH(clientECDH)
	if err != nil {
		return SessionHandshake{}, fmt.Errorf("%w: channel key agreement failed", ErrBadRequest)
	}
	defer zeroBytes(shared)

	serverPublicDER, err := x509.MarshalPKIXPublicKey(serverPrivate.PublicKey())
	if err != nil {
		return SessionHandshake{}, fmt.Errorf("encode channel public key: %w", err)
	}
	serverPublicKey := base64.StdEncoding.EncodeToString(serverPublicDER)
	clientPublicKey = base64.StdEncoding.EncodeToString(clientPublicDER)

	serverNonceBytes := make([]byte, 32)
	if _, err := rand.Read(serverNonceBytes); err != nil {
		return SessionHandshake{}, fmt.Errorf("generate channel nonce: %w", err)
	}
	serverNonce := base64.StdEncoding.EncodeToString(serverNonceBytes)

	channelID, err := randomToken(16)
	if err != nil {
		return SessionHandshake{}, err
	}
	token, err := randomToken(32)
	if err != nil {
		return SessionHandshake{}, err
	}

	saltInput := append(append([]byte(nil), clientNonceBytes...), serverNonceBytes...)
	salt := sha256.Sum256(saltInput)
	zeroBytes(saltInput)
	info := []byte(strings.Join([]string{
		"rythme-channel-kdf-v3",
		strings.TrimSpace(teamID),
		channelID,
		clientPublicKey,
		serverPublicKey,
		"",
	}, "\n"))
	keyMaterial := hkdfSHA256(shared, salt[:], info, channelKeySize*channelKeyCount)
	defer zeroBytes(keyMaterial)

	session, err := a.storeSession(
		token,
		teamID,
		teamName,
		channelID,
		keyMaterial[0:32],
		keyMaterial[32:64],
		keyMaterial[64:96],
		now.UTC(),
		now.UTC().Add(channelLifetime),
	)
	if err != nil {
		return SessionHandshake{}, err
	}

	expiresAtMS := session.ExpiresAt.UnixMilli()
	proofPayload := canonicalHandshake(
		session,
		clientPublicKey,
		serverPublicKey,
		clientNonce,
		serverNonce,
		expiresAtMS,
	)
	proof := hmacSHA256(session.ResponseKey, []byte(proofPayload))

	return SessionHandshake{
		Session:         session,
		ServerPublicKey: serverPublicKey,
		ServerNonce:     serverNonce,
		ServerProof:     hex.EncodeToString(proof),
	}, nil
}

func (a *Arena) VerifyChannelRequest(
	token string,
	proof ChannelRequestProof,
	method string,
	target string,
	body []byte,
	now time.Time,
) (ChannelReceipt, error) {
	a.mu.Lock()
	defer a.mu.Unlock()

	session, ok := a.sessions[token]
	if !ok {
		return ChannelReceipt{}, ErrUnauthorized
	}
	if now.UTC().After(session.ExpiresAt) {
		delete(a.sessions, token)
		return ChannelReceipt{}, fmt.Errorf("%w: secure channel expired", ErrUnauthorized)
	}
	if proof.ChannelID == "" || proof.ChannelID != session.ChannelID {
		return ChannelReceipt{}, fmt.Errorf("%w: secure channel identity mismatch", ErrUnauthorized)
	}
	if proof.Sequence != session.LastRequestSeq+1 {
		return ChannelReceipt{}, fmt.Errorf("%w: secure channel sequence is out of order", ErrConflict)
	}
	sentAt := time.UnixMilli(proof.SentAtMS)
	if delta := now.UTC().Sub(sentAt); delta < -channelClockSkew || delta > channelClockSkew {
		return ChannelReceipt{}, fmt.Errorf("%w: secure channel timestamp is outside the acceptance window", ErrUnauthorized)
	}

	expectedPrevious := initialChannelMAC
	if len(session.LastRequestMAC) != 0 {
		expectedPrevious = hex.EncodeToString(session.LastRequestMAC)
	}
	if !hmac.Equal([]byte(strings.ToLower(proof.Previous)), []byte(expectedPrevious)) {
		return ChannelReceipt{}, fmt.Errorf("%w: secure channel transcript forked", ErrConflict)
	}

	provided, err := hex.DecodeString(proof.Signature)
	if err != nil || len(provided) != sha256.Size {
		return ChannelReceipt{}, fmt.Errorf("%w: malformed secure channel signature", ErrBadRequest)
	}
	canonical := CanonicalChannelRequest(
		session.ChannelID,
		method,
		target,
		proof.Sequence,
		proof.SentAtMS,
		body,
		expectedPrevious,
	)
	expected := hmacSHA256(session.RequestKey, []byte(canonical))
	if !hmac.Equal(provided, expected) {
		return ChannelReceipt{}, fmt.Errorf("%w: secure channel signature did not verify", ErrUnauthorized)
	}

	session.LastRequestSeq = proof.Sequence
	session.LastRequestMAC = append(session.LastRequestMAC[:0], provided...)
	a.sessions[token] = session

	return ChannelReceipt{
		Sequence:    proof.Sequence,
		channelID:   session.ChannelID,
		requestMAC:  append([]byte(nil), provided...),
		responseKey: append([]byte(nil), session.ResponseKey...),
	}, nil
}

func SignChannelResponse(receipt ChannelReceipt, status int, body []byte) string {
	bodyDigest := sha256.Sum256(body)
	canonical := strings.Join([]string{
		SecureChannelProtocol,
		receipt.channelID,
		strconv.FormatUint(receipt.Sequence, 10),
		strconv.Itoa(status),
		hex.EncodeToString(bodyDigest[:]),
		hex.EncodeToString(receipt.requestMAC),
		"",
	}, "\n")
	return hex.EncodeToString(hmacSHA256(receipt.responseKey, []byte(canonical)))
}

func CanonicalChannelRequest(
	channelID string,
	method string,
	target string,
	sequence uint64,
	sentAtMS int64,
	body []byte,
	previous string,
) string {
	bodyDigest := sha256.Sum256(body)
	return strings.Join([]string{
		SecureChannelProtocol,
		channelID,
		strings.ToUpper(method),
		target,
		strconv.FormatUint(sequence, 10),
		strconv.FormatInt(sentAtMS, 10),
		hex.EncodeToString(bodyDigest[:]),
		strings.ToLower(previous),
		"",
	}, "\n")
}

func canonicalHandshake(
	session Session,
	clientPublicKey string,
	serverPublicKey string,
	clientNonce string,
	serverNonce string,
	expiresAtMS int64,
) string {
	return strings.Join([]string{
		"rythme-channel-handshake-v3",
		session.TeamID,
		session.Token,
		session.ChannelID,
		clientPublicKey,
		serverPublicKey,
		clientNonce,
		serverNonce,
		strconv.FormatInt(expiresAtMS, 10),
		"",
	}, "\n")
}

func hkdfSHA256(inputKeyMaterial, salt, info []byte, length int) []byte {
	if len(salt) == 0 {
		salt = make([]byte, sha256.Size)
	}
	pseudorandomKey := hmacSHA256(salt, inputKeyMaterial)
	defer zeroBytes(pseudorandomKey)

	output := make([]byte, 0, length)
	var previous []byte
	for counter := byte(1); len(output) < length; counter++ {
		mac := hmac.New(sha256.New, pseudorandomKey)
		mac.Write(previous)
		mac.Write(info)
		mac.Write([]byte{counter})
		next := mac.Sum(nil)
		zeroBytes(previous)
		previous = next
		output = append(output, previous...)
	}
	zeroBytes(previous)
	return output[:length]
}

func hmacSHA256(key, message []byte) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write(message)
	return mac.Sum(nil)
}

func zeroBytes(value []byte) {
	for index := range value {
		value[index] = 0
	}
}
