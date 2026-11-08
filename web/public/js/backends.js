// Chat backends that the browser talks to directly:
//   Ollama + llama.cpp on the visitor's own computer (localhost), and
//   Hugging Face Inference Providers with the visitor's own token.
// (WebLLM lives in app.js because it runs in the page itself.)

export const settings = {
  get ollamaUrl() { return (load("ollamaUrl") || "http://localhost:11434").replace(/\/+$/, ""); },
  get llamaUrl() { return (load("llamaUrl") || "http://localhost:8080").replace(/\/+$/, ""); },
  get hfToken() { return load("hfToken") || ""; },
};

export function load(key) { try { return localStorage.getItem(`llmstream:${key}`); } catch { return null; } }
export function save(key, value) {
  try { value ? localStorage.setItem(`llmstream:${key}`, value) : localStorage.removeItem(`llmstream:${key}`); } catch { /* private mode */ }
}

const timeout = (ms) => (AbortSignal.timeout ? AbortSignal.timeout(ms) : undefined);

async function getJSON(url, opts = {}) {
  const res = await fetch(url, { signal: timeout(8000), ...opts });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function status() {
  const out = {};
  await Promise.all([
    getJSON(`${settings.ollamaUrl}/api/version`, { signal: timeout(2500) })
      .then((v) => { out.ollama = { available: true, detail: `Ollama ${v.version || ""} on ${settings.ollamaUrl}` }; })
      .catch(() => { out.ollama = { available: false, detail: `Can't reach Ollama at ${settings.ollamaUrl}. See “Connect your computer”.` }; }),
    getJSON(`${settings.llamaUrl}/health`, { signal: timeout(2500) })
      .then(() => { out.llamacpp = { available: true, detail: `llama-server on ${settings.llamaUrl}` }; })
      .catch(() => { out.llamacpp = { available: false, detail: `No llama-server at ${settings.llamaUrl}. Start one with: llama-server -m model.gguf` }; }),
  ]);
  out.huggingface = settings.hfToken
    ? { available: true, detail: "Hugging Face Inference Providers (your token is saved in this browser only)." }
    : { available: true, needsToken: true, detail: "Browse freely. To chat, add a Hugging Face token in Settings (it stays in this browser)." };
  return out;
}

let hfCache = null;
export async function listModels(backend) {
  if (backend === "ollama") {
    const data = await getJSON(`${settings.ollamaUrl}/api/tags`).catch(() => { throw new Error(`Can't reach Ollama at ${settings.ollamaUrl}. Open “Connect your computer” below for the one-line setup.`); });
    return (data.models || []).map((m) => {
      const d = m.details || {};
      return { id: m.name, label: m.name, size: m.size, details: [d.parameter_size, d.quantization_level, d.family].filter(Boolean).join(" · ") };
    });
  }
  if (backend === "llamacpp") {
    const data = await getJSON(`${settings.llamaUrl}/v1/models`).catch(() => { throw new Error(`No llama-server at ${settings.llamaUrl}. Start one with: llama-server -m model.gguf`); });
    return (data.data || []).map((m) => ({ id: m.id, label: m.id.split(/[\\/]/).pop(), details: "loaded in llama-server" }));
  }
  if (backend === "huggingface") {
    if (!hfCache) {
      const data = await getJSON("https://router.huggingface.co/v1/models");
      hfCache = (data.data || []).map((m) => {
        const live = (m.providers || []).filter((p) => (p.status || "live") === "live");
        const ctx = Math.max(0, ...live.map((p) => p.context_length || 0));
        let details = `${live.length} provider${live.length === 1 ? "" : "s"}`;
        if (ctx) details += ` · ${Math.round(ctx / 1000)}k ctx`;
        if (live.some((p) => p.is_free)) details += " · free tier";
        return { id: m.id, label: m.id, details };
      });
    }
    return hfCache;
  }
  return [];
}

async function* lines(res) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).replace(/\r$/, "");
      buf = buf.slice(i + 1);
      if (line.trim()) yield line;
    }
  }
  if (buf.trim()) yield buf;
}

async function errorText(res) {
  const t = await res.text().catch(() => "");
  try { const j = JSON.parse(t); const e = j.error; return (typeof e === "object" ? e.message : e) || j.message || t; } catch { return t || `HTTP ${res.status}`; }
}

async function* openaiStream(url, model, messages, options, headers, signal) {
  const body = { model, messages, stream: true, stream_options: { include_usage: true } };
  if (options.temperature != null && !isNaN(options.temperature)) body.temperature = options.temperature;
  if (options.max_tokens) body.max_tokens = options.max_tokens;
  const started = performance.now();
  let first = null, pieces = 0, usage = null;
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body), signal });
  if (!res.ok) throw new Error(res.status === 401 ? "Hugging Face says your token isn't valid (Settings)." : await errorText(res));
  for await (const line of lines(res)) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice(5).trim();
    if (payload === "[DONE]") break;
    let chunk;
    try { chunk = JSON.parse(payload); } catch { continue; }
    if (chunk.error) throw new Error(chunk.error.message || String(chunk.error));
    if (chunk.usage) usage = chunk.usage;
    for (const c of chunk.choices || []) {
      const d = c.delta || {};
      const reasoning = d.reasoning_content || d.reasoning;
      if (reasoning) yield { type: "reasoning", text: reasoning };
      if (d.content) { if (first == null) first = performance.now(); pieces++; yield { type: "token", text: d.content }; }
    }
  }
  yield done(started, first, usage?.completion_tokens || pieces);
}

function done(started, first, tokens) {
  const gen = (performance.now() - (first || started)) / 1000;
  return { type: "done", stats: { tokens, seconds: +((performance.now() - started) / 1000).toFixed(2), tokens_per_second: gen > 0 ? +(tokens / gen).toFixed(1) : null } };
}

export async function* chat(backend, model, messages, options, signal) {
  if (backend === "huggingface") {
    if (!settings.hfToken) throw new Error("Add a Hugging Face token in Settings to chat with hosted models.");
    yield* openaiStream("https://router.huggingface.co/v1/chat/completions", model, messages, options, { Authorization: `Bearer ${settings.hfToken}` }, signal);
    return;
  }
  if (backend === "llamacpp") {
    yield* openaiStream(`${settings.llamaUrl}/v1/chat/completions`, model, messages, options, {}, signal);
    return;
  }
  if (backend === "ollama") {
    const opts = {};
    if (options.temperature != null && !isNaN(options.temperature)) opts.temperature = options.temperature;
    if (options.max_tokens) opts.num_predict = options.max_tokens;
    const started = performance.now();
    let first = null;
    const res = await fetch(`${settings.ollamaUrl}/api/chat`, {
      method: "POST", headers: { "Content-Type": "application/json" }, signal,
      body: JSON.stringify({ model, messages, stream: true, options: opts }),
    });
    if (!res.ok) throw new Error(await errorText(res));
    for await (const line of lines(res)) {
      const c = JSON.parse(line);
      if (c.error) throw new Error(c.error);
      if (c.message?.thinking) yield { type: "reasoning", text: c.message.thinking };
      if (c.message?.content) { if (first == null) first = performance.now(); yield { type: "token", text: c.message.content }; }
      if (c.done) {
        const dur = (c.eval_duration || 0) / 1e9;
        const ev = done(started, first, c.eval_count || 0);
        if (dur) ev.stats.tokens_per_second = +((c.eval_count || 0) / dur).toFixed(1);
        yield ev;
        return;
      }
    }
    return;
  }
  throw new Error(`unknown backend ${backend}`);
}

// Stream `ollama pull` progress from the visitor's own Ollama.
export async function* ollamaPull(model, signal) {
  let res;
  try {
    res = await fetch(`${settings.ollamaUrl}/api/pull`, {
      method: "POST", headers: { "Content-Type": "application/json" }, signal,
      body: JSON.stringify({ model, stream: true }),
    });
  } catch {
    throw new Error(`Can't reach your Ollama at ${settings.ollamaUrl}. See “Connect your computer”.`);
  }
  if (!res.ok) throw new Error(await errorText(res));
  const totals = {}, done = {};
  for await (const line of lines(res)) {
    const ev = JSON.parse(line);
    if (ev.error) throw new Error(ev.error);
    if (ev.digest && ev.total) { totals[ev.digest] = ev.total; done[ev.digest] = ev.completed || 0; }
    const total = Object.values(totals).reduce((a, b) => a + b, 0);
    const completed = Object.values(done).reduce((a, b) => a + b, 0);
    yield { status: ev.status || "", total, completed };
    if (ev.status === "success") return;
  }
}
