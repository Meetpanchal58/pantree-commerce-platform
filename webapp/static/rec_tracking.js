// Recommendation impression and click tracking.
// - link(item): product URL carrying the recommendation request id and position,
//   so opening it is recorded as a click.
// - track(card, item): reports the card as "seen" once at least half of it is on
//   screen (the usual definition of an impression), batched every second.
window.PantreeRecs = (() => {
  const pending = new Map();
  let timer = null;

  function flush() {
    if (!pending.size) return;
    const body = JSON.stringify({ seen: [...pending.values()] });
    pending.clear();
    const sent = navigator.sendBeacon &&
      navigator.sendBeacon("/api/rec-events/seen", new Blob([body], { type: "application/json" }));
    if (!sent) {
      fetch("/api/rec-events/seen", {
        method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true,
      }).catch(() => {});
    }
  }

  const observer = "IntersectionObserver" in window
    ? new IntersectionObserver(entries => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          const { recRequest, recPosition } = entry.target.dataset;
          pending.set(`${recRequest}:${recPosition}`, { request_id: recRequest, position: Number(recPosition) });
          observer.unobserve(entry.target);
        });
        clearTimeout(timer);
        timer = setTimeout(flush, 1000);
      }, { threshold: 0.5 })
    : null;

  function track(card, item) {
    if (!item.rec_request_id || !observer) return;
    card.dataset.recRequest = item.rec_request_id;
    card.dataset.recPosition = item.rec_position;
    observer.observe(card);
  }

  function link(item) {
    const base = `/product/${encodeURIComponent(item.product_id)}`;
    return item.rec_request_id
      ? `${base}?rec=${encodeURIComponent(item.rec_request_id)}&pos=${encodeURIComponent(item.rec_position)}`
      : base;
  }

  addEventListener("pagehide", flush);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
  return { track, link };
})();
