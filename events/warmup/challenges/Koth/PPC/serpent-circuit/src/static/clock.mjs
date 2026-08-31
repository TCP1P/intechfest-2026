function finite(value, label) {
  if (!Number.isFinite(value)) {
    throw new TypeError(`${label} must be finite`);
  }
  return value;
}

export function makeServerClock(serverTime, sentMonotonic, receivedMonotonic) {
  if (!Number.isSafeInteger(serverTime) || serverTime < 0) {
    throw new TypeError("serverTime must be a nonnegative safe integer");
  }
  const sent = finite(sentMonotonic, "sentMonotonic");
  const received = finite(receivedMonotonic, "receivedMonotonic");
  if (sent < 0 || received < sent) {
    throw new RangeError("monotonic request interval is invalid");
  }
  return Object.freeze({
    monotonicAtReceive: received,
    unixAtReceive: serverTime + (received - sent) / 2,
  });
}

export function serverNow(clock, currentMonotonic) {
  if (
    !clock
    || !Number.isFinite(clock.monotonicAtReceive)
    || !Number.isFinite(clock.unixAtReceive)
  ) {
    throw new TypeError("server clock is invalid");
  }
  const current = finite(currentMonotonic, "currentMonotonic");
  if (current < clock.monotonicAtReceive) {
    throw new RangeError("monotonic time moved backwards");
  }
  return clock.unixAtReceive + current - clock.monotonicAtReceive;
}
