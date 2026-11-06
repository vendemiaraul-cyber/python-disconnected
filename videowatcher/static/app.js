(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const form = $("#search-form"), input = $("#q"), grid = $("#results");
  const statusEl = $("#status"), sourceStatus = $("#source-status");
  const tpl = $("#card-tpl"), dialog = $("#player"), frame = $("#player-frame");
  let controller = null;

  const fmtDuration = (s) => {
    if (!s) return "";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const mm = h ? String(m).padStart(2, "0") : m;
    return (h ? h + ":" : "") + mm + ":" + String(sec).padStart(2, "0");
  };
  const fmtCount = (n) => n == null ? "" : Intl.NumberFormat(undefined, { notation: "compact" }).format(n);
  const isHttps = (u) => { try { return new URL(u).protocol === "https:"; } catch { return false; } };

  const selectedSources = () =>
    [...document.querySelectorAll("#source-filters input:checked")].map((i) => i.value);

  async function search(query) {
    if (controller) controller.abort();
    controller = new AbortController();
    const sources = selectedSources();
    if (!sources.length) { statusEl.textContent = "Pick at least one source."; return; }
    statusEl.textContent = `Searching ${sources.length} site(s) for “${query}”…`;
    grid.replaceChildren(); sourceStatus.replaceChildren();
    const params = new URLSearchParams({ q: query, sources: sources.join(","), limit: "8" });
    history.replaceState(null, "", "?q=" + encodeURIComponent(query));
    try {
      const res = await fetch("/api/search?" + params, { signal: controller.signal });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      render(data);
    } catch (err) {
      if (err.name !== "AbortError") statusEl.textContent = "Search failed: " + err.message;
    }
  }

  function render(data) {
    const items = data.results.filter((v) => isHttps(v.embed_url) && isHttps(v.url));
    statusEl.textContent = items.length
      ? `${items.length} videos for “${data.query}”`
      : `No videos found for “${data.query}”.`;
    for (const [key, s] of Object.entries(data.sources)) {
      const li = document.createElement("li");
      const label = document.querySelector(`.chip[data-source="${key}"] span`)?.textContent || key;
      li.textContent = s.ok ? `${label}: ${s.count}` : `${label}: unavailable (${s.error})`;
      if (!s.ok) li.className = "err";
      sourceStatus.append(li);
    }
    grid.replaceChildren(...items.map(card));
  }

  function card(v) {
    const el = tpl.content.firstElementChild.cloneNode(true);
    const img = el.querySelector("img");
    if (v.thumbnail && isHttps(v.thumbnail)) img.src = v.thumbnail;
    img.alt = v.title;
    el.querySelector(".duration").textContent = fmtDuration(v.duration);
    el.querySelector(".title").textContent = v.title;
    el.querySelector(".title").title = v.title;
    el.querySelector(".channel").textContent =
      [v.channel, v.views != null ? fmtCount(v.views) + " views" : null].filter(Boolean).join(" · ");
    const badge = el.querySelector(".badge");
    badge.textContent = v.source_name;
    badge.dataset.source = v.source;
    const domain = el.querySelector(".domain");
    domain.textContent = v.source_domain;
    domain.href = v.url;
    domain.addEventListener("click", (e) => e.stopPropagation());
    const open = () => play(v);
    el.addEventListener("click", open);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
    return el;
  }

  function play(v) {
    $("#player-title").textContent = v.title;
    const badge = $("#player-source");
    badge.textContent = v.source_name;
    badge.dataset.source = v.source;
    $("#player-meta").textContent =
      [v.channel, v.published, v.views != null ? fmtCount(v.views) + " views" : null].filter(Boolean).join(" · ");
    const ads = $("#player-ads");
    ads.hidden = !v.ad_supported;
    ads.textContent = v.ad_supported ? `Playing in ${v.source_name}'s official player. Ads may be shown by ${v.source_name}.` : "";
    const link = $("#player-link");
    link.href = v.url;
    link.textContent = `Watch on ${v.source_domain} ↗`;
    frame.src = v.embed_url;
    dialog.showModal();
  }

  dialog.addEventListener("close", () => { frame.src = "about:blank"; });
  $("#player-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q) search(q);
  });
  document.querySelectorAll("#source-filters input").forEach((cb) =>
    cb.addEventListener("change", () => { if (input.value.trim()) search(input.value.trim()); }));

  if (input.value.trim()) search(input.value.trim());
})();
