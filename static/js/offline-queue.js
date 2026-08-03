// MedRelay courier PWA — offline event queue (Phase 5).
//
// Design decision (docs/CURRENT_STATUS.md "Phase 5" has the full write-up):
// storage is plain `localStorage`, not IndexedDB. A courier's queued events
// at this prototype's scale are a handful of status transitions plus
// periodic location pings for a single active delivery at a time — a tiny,
// synchronous JSON blob is a perfectly adequate queue here, and localStorage's
// synchronous API is simpler to reason about (and test) than IndexedDB's
// async API for zero present benefit at this volume. This is a deliberate,
// documented choice, not an oversight.
//
// Every queued event carries a client-generated `idempotencyKey` (a v4 UUID)
// created once, at enqueue time — not regenerated on each retry — so a
// server that has already applied an event's effect (see
// apps.couriers.idempotency.idempotent_call) recognizes a replay and returns
// the original result instead of re-applying it. This is what makes
// "reruns/retries do not duplicate events" hold even when the *same* queued
// event is submitted more than once (e.g. a flush that partially succeeds
// before the page is closed, then resumes on the next load).
(function () {
  const STORAGE_KEY = "medrelay_offline_queue_v1";

  function generateId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    // Fallback for browsers without crypto.randomUUID (older mobile Safari) —
    // not cryptographically strong, but only needs to be practically unique
    // per-device for this prototype's idempotency purposes.
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function readQueue() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (err) {
      return [];
    }
  }

  function writeQueue(queue) {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  // Queue one event for `url` (a courier action endpoint) with JSON `body`.
  // Returns the generated idempotencyKey. Attempts an immediate flush.
  function enqueue(url, body) {
    const queue = readQueue();
    const idempotencyKey = generateId();
    queue.push({
      id: idempotencyKey,
      idempotencyKey: idempotencyKey,
      url: url,
      body: body || {},
      createdAt: new Date().toISOString(),
    });
    writeQueue(queue);
    flush();
    return idempotencyKey;
  }

  // Attempt to submit every queued event, in order. An event that gets any
  // HTTP response at all (success or a legitimate rejection like 409/403/422)
  // is considered "resolved" and removed — retrying a request the server has
  // already definitively answered would not change the outcome. An event
  // that fails with a genuine network error (fetch rejects, e.g. offline)
  // stays queued and stops the flush loop, since later events plausibly
  // depend on earlier ones landing.
  async function flush() {
    const queue = readQueue();
    if (queue.length === 0) {
      return;
    }
    const remaining = queue.slice();
    while (remaining.length > 0) {
      const event = remaining[0];
      try {
        await fetch(event.url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": event.idempotencyKey,
            "X-CSRFToken": csrfToken(),
          },
          body: JSON.stringify(event.body),
        });
        remaining.shift();
      } catch (networkError) {
        break; // stay offline-queued; try again on the next flush() call
      }
    }
    writeQueue(remaining);
  }

  window.MedRelayOfflineQueue = {
    enqueue: enqueue,
    flush: flush,
    _readQueue: readQueue, // exposed for manual/browser-console inspection only
  };

  window.addEventListener("online", flush);
  window.addEventListener("load", flush);
})();
