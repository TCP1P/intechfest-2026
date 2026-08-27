// Container-level regression for the authoritative arena.
//
// Starts one isolated supervisor, joins all eight slots through the public
// QUIC protocol, rejects a stale client, samples resources, waits for a timed
// GameOver, and validates the ordered scoring-leg/final reports.
import { execFile, execFileSync, spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";

const image = process.env.IMAGE || "minions-in-32k:confirmed-v1";
const matchSeconds = boundedInteger("MATCH_SECONDS", 30, 1_800, 300);
const legSeconds = boundedInteger("LEG_SECONDS", 1, matchSeconds, 60);
const graceSeconds = boundedInteger("GRACE_SECONDS", 5, 120, 5);
if (matchSeconds % legSeconds !== 0) {
  throw new Error("LEG_SECONDS must evenly divide MATCH_SECONDS");
}

const container = `minions-confirmation-regression-${process.pid}`;
const temporary = mkdtempSync(join(tmpdir(), "minions-regression-"));
const reportToken = secret();
const reports = [];
const samples = [];
let human = null;
let collector = null;

try {
  const tls = createTlsIdentity(temporary);
  const players = Array.from({ length: 8 }, (_, index) => ({
    uuid: index === 0 ? "human-regression" : `npc-regression-${index}`,
    key: secret(),
    name: index === 0 ? "HumanRegression" : `NPC_${index}`,
  }));

  collector = await startCollector(reportToken, reports);
  run("docker", [
    "run",
    "-d",
    "--name",
    container,
    "--read-only",
    "--tmpfs",
    "/tmp:rw,noexec,nosuid,size=128m,uid=32032,gid=32032,mode=700",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
    "--pids-limit",
    "128",
    "--memory",
    "512m",
    "--cpus",
    "2",
    "--add-host",
    "host.docker.internal:host-gateway",
    "-p",
    "127.0.0.1::8080/tcp",
    "-p",
    "127.0.0.1::4433/udp",
    "-e",
    `PLAYER_TOKENS=${JSON.stringify(players)}`,
    "-e",
    `REPORT_URL=http://host.docker.internal:${collector.port}/report`,
    "-e",
    `REPORT_TOKEN=${reportToken}`,
    "-e",
    `WT_CERT_DER_B64=${tls.certDer}`,
    "-e",
    `WT_KEY_DER_B64=${tls.keyDer}`,
    "-e",
    `MINIONS_CERT_HASH=${tls.certHash}`,
    "-e",
    `MINIONS_BOT_KEYS=${players.slice(1).map((player) => player.key).join(",")}`,
    "-e",
    "MINIONS_BOT_COUNT=7",
    "-e",
    `MINIONS_GRACE_SECS=${graceSeconds}`,
    "-e",
    `MINIONS_MATCH_SECS=${matchSeconds}`,
    "-e",
    `MINIONS_SCORING_LEG_SECS=${legSeconds}`,
    "-e",
    "RUST_LOG=info",
    image,
  ]);

  const httpPort = await mappedPort(container, "8080/tcp");
  await waitFor(async () => {
    const response = await fetch(`http://127.0.0.1:${httpPort}/healthz`);
    return response.ok && (await response.json()).status === "idle";
  }, 15_000, "supervisor health");
  const initialHealth = await getHealth(httpPort);
  assert(initialHealth.protocol === "minions/0", "health ALPN is incorrect");
  assert(initialHealth.protocolVersion === 13, "health protocol revision is stale");
  assert(initialHealth.players === 8 && initialHealth.npcs === 7, "health roster is incorrect");
  const idleQuality = await getQuality(httpPort);
  assert(
    idleQuality.mode === "idle" && idleQuality.probe === "pinned-quic-invalid-key",
    "idle functional gameplay probe did not pass",
  );

  const unauthorized = await fetch(
    `http://127.0.0.1:${httpPort}/internal/start`,
    { method: "POST", headers: { authorization: "Bearer invalid" } },
  );
  assert(unauthorized.status === 401, "controller accepted an invalid token");

  const started = await fetch(`http://127.0.0.1:${httpPort}/internal/start`, {
    method: "POST",
    headers: { authorization: `Bearer ${reportToken}` },
  });
  assert(started.status === 201, `arena start returned HTTP ${started.status}`);
  const runningQuality = await getQuality(httpPort);
  assert(
    runningQuality.mode === "running" && runningQuality.protocolVersion === 13,
    "running functional gameplay probe did not pass",
  );

  // A protocol-v12 binary must fail closed before the valid player takes the
  // same authenticated slot. This catches accidental compatibility drift.
  const stale = await runHeadless(container, players[0].key, tls.certHash, 12);
  assert(
    /protocol|version|rejected/i.test(stale.output),
    "protocol-v12 client was not explicitly rejected",
  );

  human = spawn(
    "docker",
    [
      "exec",
      container,
      "/usr/local/bin/minions-headless",
      "--remote",
      "127.0.0.1:4433",
      "--name",
      players[0].name,
      "--count",
      "1",
      "--npc-count",
      "1",
      "--keys",
      players[0].key,
      "--cert-hash",
      tls.certHash,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  const humanOutput = collectOutput(human, 512 * 1024);

  const deadline = Date.now() + (graceSeconds + matchSeconds + 30) * 1_000;
  let runningSeen = false;
  while (Date.now() < deadline) {
    const health = await getHealth(httpPort);
    runningSeen ||= health.status === "running";
    samples.push(readStats(container));
    if (runningSeen && health.status === "idle") break;
    await sleep(1_000);
  }
  assert(runningSeen, "arena never entered running state");
  assert((await getHealth(httpPort)).status === "idle", "timed arena did not stop");

  const humanResult = await waitForChild(human, humanOutput, 10_000);
  human = null;
  assert(humanResult.code === 0, `human protocol client failed: ${humanResult.output}`);
  assert(/game over/i.test(humanResult.output), "human client did not receive GameOver");

  await waitFor(
    async () => reports.some((report) => report.kind === "final"),
    10_000,
    "final report",
  );
  const result = validateReports(reports, matchSeconds, legSeconds);
  const logs = run("docker", ["logs", container]);
  const stagingIntegrityPlayers = validateStagingIntegrity(logs, result.final);
  assert(/kill confirmed/i.test(logs), "network simulation produced no confirmation event");
  assert(!/panic|thread .* panicked|fatal/i.test(logs), "arena logs contain a fatal failure");

  const imageID = run("docker", ["image", "inspect", image, "--format", "{{.Id}}"]);
  const cpu = samples.map((sample) => sample.cpuPercent);
  const memory = samples.map((sample) => sample.memoryMiB);
  console.log(
    JSON.stringify(
      {
        image,
        imageID,
        protocol: 13,
        graceSeconds,
        matchSeconds,
        legSeconds,
        players: 8,
        npcs: 7,
        staleProtocolRejected: true,
        reportsReceived: reports.length,
        uniqueLegs: result.legs,
        allPlayersPresent: result.allPlayersPresent,
        totals: result.totals,
        automatedCombatPlayers: result.automatedCombatPlayers,
        stagingIntegrityPlayers,
        resources: {
          samples: samples.length,
          cpuPercentAverage: average(cpu),
          cpuPercentP95: percentile(cpu, 0.95),
          cpuPercentMax: maximum(cpu),
          memoryMiBMax: maximum(memory),
        },
      },
      null,
      2,
    ),
  );
} finally {
  if (human && human.exitCode === null) human.kill("SIGTERM");
  try {
    run("docker", ["rm", "-f", container]);
  } catch {}
  if (collector) await collector.close();
  rmSync(temporary, { recursive: true, force: true });
}

function boundedInteger(name, minimum, maximum, fallback) {
  const raw = process.env[name];
  const value = raw === undefined ? fallback : Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer from ${minimum} through ${maximum}`);
  }
  return value;
}

function secret() {
  return randomBytes(32).toString("base64url");
}

function run(file, args) {
  return execFileSync(file, args, {
    encoding: "utf8",
    maxBuffer: 8 * 1024 * 1024,
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

function createTlsIdentity(directory) {
  const certPem = join(directory, "cert.pem");
  const keyPem = join(directory, "key.pem");
  const certDerPath = join(directory, "cert.der");
  const keyDerPath = join(directory, "key.der");
  run("openssl", [
    "req",
    "-x509",
    "-newkey",
    "ec",
    "-pkeyopt",
    "ec_paramgen_curve:P-256",
    "-nodes",
    "-sha256",
    "-days",
    "1",
    "-subj",
    "/CN=localhost",
    "-addext",
    "subjectAltName=DNS:localhost",
    "-keyout",
    keyPem,
    "-out",
    certPem,
  ]);
  run("openssl", ["x509", "-in", certPem, "-outform", "DER", "-out", certDerPath]);
  run("openssl", [
    "pkcs8",
    "-topk8",
    "-nocrypt",
    "-in",
    keyPem,
    "-outform",
    "DER",
    "-out",
    keyDerPath,
  ]);
  const certificate = readFileSync(certDerPath);
  return {
    certDer: certificate.toString("base64"),
    keyDer: readFileSync(keyDerPath).toString("base64"),
    certHash: execFileSync("sha256sum", [certDerPath], { encoding: "utf8" })
      .split(/\s+/, 1)[0],
  };
}

async function startCollector(token, destination) {
  const server = createServer((request, response) => {
    if (
      request.method !== "POST" ||
      request.url !== "/report" ||
      request.headers.authorization !== `Bearer ${token}`
    ) {
      response.writeHead(401).end();
      return;
    }
    const chunks = [];
    let bytes = 0;
    request.on("data", (chunk) => {
      bytes += chunk.length;
      if (bytes > 1024 * 1024) request.destroy();
      else chunks.push(chunk);
    });
    request.on("end", () => {
      try {
        destination.push(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        response.writeHead(204).end();
      } catch {
        response.writeHead(400).end();
      }
    });
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "0.0.0.0", resolve);
  });
  const address = server.address();
  return {
    port: address.port,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function mappedPort(name, privatePort) {
  return Number(
    await waitFor(async () => {
      try {
        const output = run("docker", ["port", name, privatePort]);
        const match = output.match(/127\.0\.0\.1:(\d+)/);
        return match?.[1] || false;
      } catch {
        return false;
      }
    }, 10_000, `Docker port ${privatePort}`),
  );
}

async function getHealth(port) {
  const response = await fetch(`http://127.0.0.1:${port}/healthz`);
  assert(response.ok, `health returned HTTP ${response.status}`);
  return response.json();
}

async function getQuality(port) {
  const response = await fetch(`http://127.0.0.1:${port}/qualityz`);
  assert(response.ok, `quality probe returned HTTP ${response.status}`);
  return response.json();
}

function runHeadless(name, key, certHash, version) {
  return new Promise((resolve) => {
    execFile(
      "docker",
      [
        "exec",
        name,
        "/usr/local/bin/minions-headless",
        "--remote",
        "127.0.0.1:4433",
        "--count",
        "1",
        "--npc-count",
        "1",
        "--keys",
        key,
        "--cert-hash",
        certHash,
        "--protocol-version",
        String(version),
      ],
      { encoding: "utf8", maxBuffer: 1024 * 1024 },
      (error, stdout, stderr) => {
        resolve({ code: error?.code ?? 0, output: `${stdout}${stderr}` });
      },
    );
  });
}

function collectOutput(child, limit) {
  let output = "";
  const append = (chunk) => {
    output = `${output}${chunk}`.slice(-limit);
  };
  child.stdout.on("data", append);
  child.stderr.on("data", append);
  return () => output;
}

function waitForChild(child, output, timeout) {
  if (child.exitCode !== null) {
    return Promise.resolve({ code: child.exitCode, output: output() });
  }
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("protocol client did not exit")), timeout);
    child.once("exit", (code) => {
      clearTimeout(timer);
      resolve({ code, output: output() });
    });
  });
}

function readStats(name) {
  const output = run("docker", [
    "stats",
    "--no-stream",
    "--format",
    "{{.CPUPerc}}|{{.MemUsage}}",
    name,
  ]);
  const [cpu, memory] = output.split("|", 2);
  return {
    cpuPercent: Number(cpu.replace("%", "")),
    memoryMiB: parseMemoryMiB(memory.split("/", 1)[0].trim()),
  };
}

function parseMemoryMiB(value) {
  const match = value.match(/^([0-9.]+)([KMG]iB)$/);
  assert(match, `unrecognized Docker memory value: ${value}`);
  const number = Number(match[1]);
  return number * { KiB: 1 / 1024, MiB: 1, GiB: 1024 }[match[2]];
}

function validateReports(received, duration, leg) {
  const legCount = duration / leg;
  const bySequence = new Map();
  let final = null;
  for (const report of received) {
    if (report.kind === "leg") bySequence.set(report.sequence, report);
    if (report.kind === "final") final = report;
  }
  assert(bySequence.size === legCount, `expected ${legCount} unique legs, got ${bySequence.size}`);
  assert(final, "final reconciliation report is missing");
  assert(final.sequence === legCount + 1, "final report sequence is invalid");
  assert(final.leg_count === legCount, "final report leg_count is invalid");
  assert(final.players?.length === 8, "final report does not contain eight players");
  const allPlayersPresent = [...bySequence.values()].every(
    (report) => report.players?.length === 8 && report.players.every((player) => player.connected),
  );
  assert(allPlayersPresent, "at least one player failed the per-leg presence gate");
  for (const report of [...bySequence.values(), final]) {
    for (const player of report.players) {
      assert(Number.isSafeInteger(player.confirmed_kills), "confirmed_kills is missing");
      assert(player.confirmed_kills >= 0, "confirmed_kills is negative");
      assert(
        player.integrity_status === "clean" || player.integrity_status === "automated_combat",
        "private integrity_status is missing or invalid",
      );
    }
  }
  const automatedCombatPlayers = final.players
    .filter((player) => player.integrity_status === "automated_combat")
    .map((player) => player.name)
    .sort();
  assert(
    automatedCombatPlayers.includes("HumanRegression"),
    "the unattended public-protocol actor was not privately classified",
  );
  for (const player of final.players) {
    assert(
      player.confirmed_kills <= player.kills,
      "cumulative confirmed_kills exceeds cumulative kills",
    );
  }
  const totals = final.players.reduce(
    (sum, player) => ({
      confirmedKills: sum.confirmedKills + player.confirmed_kills,
      kills: sum.kills + player.kills,
      deaths: sum.deaths + player.deaths,
    }),
    { confirmedKills: 0, kills: 0, deaths: 0 },
  );
  assert(totals.kills > 0, "network simulation produced no frags");
  assert(totals.confirmedKills > 0, "network simulation produced no confirmed kills");
  return {
    final,
    legs: [...bySequence.keys()].sort((a, b) => a - b),
    allPlayersPresent,
    totals,
    automatedCombatPlayers,
  };
}

function validateStagingIntegrity(logs, final) {
  if (process.env.ASSERT_STAGING_INTEGRITY !== "1") return [];

  const plain = logs.replace(/\x1b\[[0-9;]*m/g, "");
  const boundary = plain.indexOf("official scoring window started");
  assert(boundary >= 0, "official scoring boundary is absent from arena logs");
  const staging = plain.slice(0, boundary);
  const namesByID = new Map();
  const classifiedIDs = new Set();
  for (const line of staging.split("\n")) {
    if (line.includes("player joined (instance)")) {
      const joined = line.match(/id=(\d+)\s+name=([^\s]+)/);
      if (joined) namesByID.set(Number(joined[1]), joined[2]);
    }
    if (line.includes("automated combat classified")) {
      const classified = line.match(/id=(\d+)/);
      if (classified) classifiedIDs.add(Number(classified[1]));
    }
  }
  assert(classifiedIDs.size > 0, "no automated actor was classified during staging");

  const players = [];
  for (const id of classifiedIDs) {
    const name = namesByID.get(id);
    assert(name, `staging classification for slot ${id} has no authoritative roster entry`);
    const reported = final.players.find((player) => player.name === name);
    assert(reported, `staging-classified player ${name} is absent from the final report`);
    assert(
      reported.integrity_status === "automated_combat",
      `staging classification for ${name} was lost when official scoring started`,
    );
    players.push(name);
  }
  return players.sort();
}

async function waitFor(check, timeout, label) {
  const deadline = Date.now() + timeout;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await sleep(200);
  }
  throw new Error(`timed out waiting for ${label}${lastError ? `: ${lastError.message}` : ""}`);
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function average(values) {
  return rounded(values.reduce((sum, value) => sum + value, 0) / values.length);
}

function maximum(values) {
  return rounded(Math.max(...values));
}

function percentile(values, quantile) {
  const sorted = [...values].sort((a, b) => a - b);
  return rounded(sorted[Math.ceil(quantile * sorted.length) - 1]);
}

function rounded(value) {
  return Math.round(value * 100) / 100;
}
