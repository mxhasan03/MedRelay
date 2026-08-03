// MedRelay courier PWA — action submission, geolocation pings, and
// camera QR scanning with a manual-entry fallback (Phase 5).
//
// Honesty note (see docs/CURRENT_STATUS.md "Phase 5" for the full
// write-up): real camera-based QR scanning (startCameraScan below) is
// genuinely browser/hardware-dependent and is NOT exercised by this
// project's automated test suite — it can only be manually reviewed in a
// real browser with camera access. The manual-code-entry <input> field is
// always present and always functional regardless of camera availability,
// and IS covered by Django view/service-level tests
// (apps/couriers/tests/test_views.py, apps/cargo/tests/test_services.py).
(function () {
  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function generateId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  // Generic "submit this courier action" helper used by every button/form in
  // the courier templates (accept/decline offer, advance status, package
  // scan). Every call carries a freshly generated Idempotency-Key. If the
  // network request itself fails (offline), the event is hoisted into the
  // offline event queue (static/js/offline-queue.js) instead of being lost,
  // and `onResult` is told it was queued rather than confirmed.
  async function submitAction(url, body, onResult) {
    const idempotencyKey = generateId();
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
          "X-CSRFToken": csrfToken(),
        },
        body: JSON.stringify(body || {}),
      });
      let data = {};
      try {
        data = await response.json();
      } catch (parseError) {
        data = {};
      }
      onResult(response.ok, data, response.status);
    } catch (networkError) {
      if (window.MedRelayOfflineQueue) {
        window.MedRelayOfflineQueue.enqueue(url, body);
      }
      onResult(null, { queued: true }, 0);
    }
  }

  // --- Geolocation location pings -----------------------------------------
  function startLocationPings(pingUrl) {
    if (!("geolocation" in navigator)) {
      return null;
    }
    return navigator.geolocation.watchPosition(
      function (position) {
        const body = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy_meters: position.coords.accuracy,
        };
        submitAction(pingUrl, body, function () {
          /* fire-and-forget: the active-delivery page does not need to react
             to every individual ping's response */
        });
      },
      function () {
        /* permission denied/unavailable — every other courier PWA feature
           still works without location sharing */
      },
      { enableHighAccuracy: true, maximumAge: 15000, timeout: 20000 }
    );
  }

  // --- Camera QR scanning, with an always-present manual fallback ---------
  async function startCameraScan(videoEl, onDecoded) {
    if (!("BarcodeDetector" in window) || !navigator.mediaDevices) {
      return false; // caller keeps showing the manual-entry field only
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
      });
      videoEl.srcObject = stream;
      await videoEl.play();
      const detector = new window.BarcodeDetector({ formats: ["qr_code"] });
      const interval = setInterval(async function () {
        try {
          const codes = await detector.detect(videoEl);
          if (codes.length > 0) {
            clearInterval(interval);
            stream.getTracks().forEach(function (track) {
              track.stop();
            });
            onDecoded(codes[0].rawValue);
          }
        } catch (detectError) {
          /* ignore a single failed detection frame; keep trying */
        }
      }, 500);
      return true;
    } catch (cameraError) {
      return false; // camera unavailable/denied — manual entry remains the path
    }
  }

  window.MedRelayCourier = {
    submitAction: submitAction,
    startLocationPings: startLocationPings,
    startCameraScan: startCameraScan,
  };
})();
