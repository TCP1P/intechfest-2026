import assert from "node:assert/strict";
import test from "node:test";

import { makeServerClock, serverNow } from "./static/clock.mjs";


test("server clock uses the request midpoint and monotonic elapsed time", () => {
  const clock = makeServerClock(1_000_000, 20, 60);
  assert.deepEqual(clock, {
    monotonicAtReceive: 60,
    unixAtReceive: 1_000_020,
  });
  assert.equal(serverNow(clock, 60), 1_000_020);
  assert.equal(serverNow(clock, 1_040), 1_001_000);
});

test("countdown expiry is independent of the browser wall clock", () => {
  const clock = makeServerClock(2_000, 100, 100);
  const waveEndsAt = 62_000;
  assert.equal(waveEndsAt - serverNow(clock, 60_099), 1);
  assert.equal(waveEndsAt - serverNow(clock, 60_100), 0);
});

test("invalid or reversed clock inputs fail closed", () => {
  assert.throws(() => makeServerClock(Number.NaN, 0, 1), TypeError);
  assert.throws(() => makeServerClock(1.5, 0, 1), TypeError);
  assert.throws(() => makeServerClock(1, 2, 1), RangeError);
  const clock = makeServerClock(1, 2, 3);
  assert.throws(() => serverNow(clock, 2), RangeError);
});
