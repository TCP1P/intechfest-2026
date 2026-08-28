package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestGatewayForwardsOpaqueCapabilityAndRewritesOnlyRemote(t *testing.T) {
	const capability = "koth_opaque-player-capability"
	var receivedAuthorization string
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/v1/arena-session" || request.Method != http.MethodPost {
			http.NotFound(w, request)
			return
		}
		receivedAuthorization = request.Header.Get("Authorization")
		_ = json.NewEncoder(w).Encode(arenaSession{
			Protocol: "minions/0", Remote: "internal.invalid:1", Name: "Team_deadbeef0000",
			Key: strings.Repeat("a", 64), CertHash: strings.Repeat("b", 64),
		})
	}))
	defer target.Close()
	targetURL, err := url.Parse(target.URL + "/api/v1/arena-session")
	if err != nil {
		t.Fatal(err)
	}
	g := &gateway{
		cfg:    configuration{SessionURL: targetURL, PublicRemote: "public.example:4532"},
		client: target.Client(),
	}
	request := httptest.NewRequest(http.MethodPost, "/api/v1/arena-session", http.NoBody)
	request.Header.Set("Authorization", "Bearer "+capability)
	recorder := httptest.NewRecorder()
	g.createSession(recorder, request)
	if recorder.Code != http.StatusOK || receivedAuthorization != "Bearer "+capability {
		t.Fatalf("status=%d authorization=%q body=%s", recorder.Code, receivedAuthorization, recorder.Body.String())
	}
	var session arenaSession
	if err := json.NewDecoder(recorder.Body).Decode(&session); err != nil {
		t.Fatal(err)
	}
	if session.Remote != "public.example:4532" || session.Name != "Team_deadbeef0000" ||
		session.Key != strings.Repeat("a", 64) {
		t.Fatalf("unexpected relayed session: %+v", session)
	}
}

func TestGatewayRejectsMalformedCapabilityAndTargetSession(t *testing.T) {
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"protocol":"minions/0","name":"bad\nname","key":"short"}`))
	}))
	defer target.Close()
	targetURL, _ := url.Parse(target.URL)
	g := &gateway{cfg: configuration{SessionURL: targetURL, PublicRemote: "public.example:4532"}, client: target.Client()}
	for _, test := range []struct {
		authorization string
		want          int
	}{
		{"", http.StatusUnauthorized},
		{"Bearer has spaces", http.StatusUnauthorized},
		{"Bearer koth_valid-looking-token", http.StatusBadGateway},
	} {
		request := httptest.NewRequest(http.MethodPost, "/api/v1/arena-session", http.NoBody)
		request.Header.Set("Authorization", test.authorization)
		recorder := httptest.NewRecorder()
		g.createSession(recorder, request)
		if recorder.Code != test.want {
			t.Fatalf("authorization=%q status=%d want=%d", test.authorization, recorder.Code, test.want)
		}
	}
}

func TestUDPProxyRelaysBothDirections(t *testing.T) {
	upstream, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer upstream.Close()
	proxy, err := newUDPProxy("127.0.0.1:0", upstream.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = proxy.serve(ctx) }()
	defer proxy.close()
	go func() {
		buffer := make([]byte, 64)
		count, address, readErr := upstream.ReadFromUDP(buffer)
		if readErr == nil {
			_, _ = upstream.WriteToUDP(append([]byte("echo:"), buffer[:count]...), address)
		}
	}()
	client, err := net.DialUDP("udp", nil, proxy.public.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	_ = client.SetDeadline(time.Now().Add(2 * time.Second))
	if _, err := client.Write([]byte("hello")); err != nil {
		t.Fatal(err)
	}
	response := make([]byte, 64)
	count, err := client.Read(response)
	if err != nil || string(response[:count]) != "echo:hello" {
		t.Fatalf("response=%q error=%v", response[:count], err)
	}
}

func TestGatewayHealthcheckRequiresBoundedHTTP200(t *testing.T) {
	for _, test := range []struct {
		status  int
		body    string
		wantErr bool
	}{
		{http.StatusOK, `{"status":"ok"}`, false},
		{http.StatusServiceUnavailable, `{}`, true},
		{http.StatusOK, strings.Repeat("x", 1025), true},
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(test.status)
			_, _ = w.Write([]byte(test.body))
		}))
		err := gatewayHealthcheck(server.URL)
		server.Close()
		if (err != nil) != test.wantErr {
			t.Fatalf("status=%d error=%v wantErr=%v", test.status, err, test.wantErr)
		}
	}
}
