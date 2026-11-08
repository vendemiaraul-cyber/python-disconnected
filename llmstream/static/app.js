// LLM Stream front-end. Talks to the local Python server for Ollama / llama.cpp /
// Hugging Face, and runs WebLLM models directly in the browser (WebGPU).

const WEBLLM_URL = "https://cdn.jsdelivr.net/npm/@mlc-ai/web-llm@0.2.79/+esm";
const HEADER_SLICE = 32 * 1024 * 1024; // how much of a dropped .gguf we read to inspect it

const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c.nodeType ? c : String(c));
  return node;
};

const state = {
  backend: localStorage.getItem("backend") || "webllm",
  model: null, // {backend, id, label}
  models: [],
  status: null,
  messages: [], // {role, content}
  abort: null,
  webllm: null, // the imported module
  engine: null,
  engineModel: null,
};
try { state.model = JSON.parse(localStorage.getItem("model") || "null"); } catch { /* ignore */ }

// ------------------------------------------------------------------ helpers

function humanSize(n) {
  if (n == null || isNaN(n)) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${i < 2 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}

function toast(text, kind = "", ms = 4500) {
  const t = el("div", { class: `toast ${kind}` }, text);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

async function api(path, { method = "GET", body, headers = {}, raw = false, signal } = {}) {
  const opts = { method, headers: { ...headers }, signal };
  if (method !== "GET") opts.headers["X-LLMStream"] = "1";
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) opts.body = body;
    else { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  }
  const res = await fetch(path, opts);
  if (raw) return res;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function* ndjson(res) {
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) yield JSON.parse(line);
    }
  }
  if (buf.trim()) yield JSON.parse(buf);
}

// Minimal, safe markdown: escape everything, then add code blocks, inline code, bold, italics, links.
function renderMarkdown(text) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const parts = text.split(/```/);
  let html = "";
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const nl = part.indexOf("\n");
      const code = nl >= 0 ? part.slice(nl + 1) : part;
      html += `<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`;
    } else {
      const paras = esc(part).split(/\n{2,}/).map((p) => p
        .replace(/`([^`\n]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
        .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
        .replace(/\n/g, "<br>"));
      html += paras.filter((p) => p.trim()).map((p) => `<p>${p}</p>`).join("");
    }
  });
  return html;
}

// ------------------------------------------------------------------ status + model lists

async function refreshStatus() {
  try {
    state.status = await api("/api/status");
  } catch (e) {
    state.status = null;
    $("#pills").replaceChildren(el("span", { class: "pill off" }, "server offline"));
    return;
  }
  const b = state.status.backends;
  const webgpu = !!navigator.gpu;
  $("#pills").replaceChildren(
    el("span", { class: `pill ${webgpu ? "on" : "off"}`, title: webgpu ? "WebGPU available" : "No WebGPU in this browser" }, "WebLLM"),
    el("span", { class: `pill ${b.ollama.available ? "on" : "off"}`, title: b.ollama.detail }, "Ollama"),
    el("span", { class: `pill ${b.llamacpp.available ? "on" : "off"}`, title: b.llamacpp.detail }, "llama.cpp"),
    el("span", { class: `pill ${b.huggingface.needs_token ? "" : "on"}`, title: b.huggingface.detail }, "Hugging Face"),
  );
  renderBackendDetail();
}

function renderBackendDetail() {
  let text = "";
  if (state.backend === "webllm") {
    text = navigator.gpu
      ? "Runs entirely in this browser tab with WebGPU. Weights download once, then stay cached (works offline)."
      : "This browser has no WebGPU, so WebLLM can't run here. Try a recent Chrome or Edge, or use another backend.";
  } else if (state.status) {
    text = state.status.backends[state.backend]?.detail || "";
  }
  $("#backend-detail").textContent = text;
}

function selectTab(name) {
  state.backend = name;
  localStorage.setItem("backend", name);
  for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-selected", String(b.dataset.backend === name));
  renderBackendDetail();
  loadModels();
}

async function loadModels() {
  const list = $("#model-list");
  list.replaceChildren(el("li", { class: "hint" }, el("span", { class: "spinner" }), "Loading models…"));
  const backend = state.backend;
  try {
    if (backend === "webllm") {
      state.models = await webllmModels();
    } else if (backend === "llamacpp") {
      const [running, local] = await Promise.all([
        api("/api/models?backend=llamacpp").then((d) => d.models).catch(() => []),
        api("/api/models?backend=local").then((d) => d.models).catch(() => []),
      ]);
      state.models = [
        ...running.map((m) => ({ ...m, group: "Running in llama-server" })),
        ...local.map((m) => ({ ...m, group: "Downloaded GGUF files", local: true })),
      ];
    } else {
      state.models = (await api(`/api/models?backend=${backend}`)).models;
    }
  } catch (e) {
    state.models = [];
    if (backend === state.backend) list.replaceChildren(el("li", { class: "hint" }, e.message));
    return;
  }
  if (backend === state.backend) renderModels();
}

function renderModels() {
  const q = $("#model-search").value.trim().toLowerCase();
  const list = $("#model-list");
  const items = state.models.filter((m) => !q || m.label.toLowerCase().includes(q) || (m.details || "").toLowerCase().includes(q));
  list.replaceChildren();
  if (!items.length) {
    const hints = {
      ollama: "No Ollama models yet. Drag a link from ollama.com here, or run `ollama pull llama3.2`.",
      llamacpp: "No GGUF files yet. Drag a Hugging Face GGUF link onto the page to download one.",
      huggingface: "Nothing matches.",
      webllm: "Nothing matches.",
    };
    list.append(el("li", { class: "hint" }, q ? "Nothing matches." : hints[state.backend]));
    return;
  }
  let group = null;
  for (const m of items.slice(0, 400)) {
    if (m.group && m.group !== group) {
      group = m.group;
      list.append(el("li", { class: "section" }, group));
    }
    const active = state.model && state.model.backend === state.backend && state.model.id === m.id;
    const li = el("li", { class: active ? "active" : "", title: m.id },
      el("span", { class: "name" }, m.label),
      el("span", { class: "size" }, m.size ? humanSize(m.size) : ""),
      el("span", { class: "meta" }, m.details || ""),
    );
    if (m.local) {
      li.append(el("div", { class: "actions" },
        el("button", { class: "btn small", onclick: (e) => { e.stopPropagation(); loadLlamaCpp(m.id); } }, "Load"),
        state.status?.system?.runtimes?.ollama_cli
          ? el("button", { class: "btn small ghost", onclick: (e) => { e.stopPropagation(); importToOllama(m.id); } }, "Add to Ollama")
          : null,
      ));
      li.addEventListener("click", () => loadLlamaCpp(m.id));
    } else {
      li.addEventListener("click", () => chooseModel(state.backend, m));
    }
    list.append(li);
  }
}

async function chooseModel(backend, m) {
  if (backend === "webllm") {
    const ok = await prepareWebLLM(m);
    if (!ok) return;
  }
  state.model = { backend, id: m.id, label: m.label };
  localStorage.setItem("model", JSON.stringify(state.model));
  renderCurrent();
  renderModels();
  $("#sidebar").classList.remove("open");
  $("#prompt").focus();
}

function renderCurrent() {
  const names = { webllm: "WebLLM (in your browser)", ollama: "Ollama", llamacpp: "llama.cpp", huggingface: "Hugging Face Inference" };
  $("#current-model").textContent = state.model ? state.model.label : "No model selected";
  $("#current-backend").textContent = state.model ? names[state.model.backend] : "Pick one on the left";
}

// ------------------------------------------------------------------ WebLLM (in the browser)

async function webllmModule() {
  if (!state.webllm) state.webllm = await import(WEBLLM_URL);
  return state.webllm;
}

async function webllmModels() {
  if (!navigator.gpu) return [];
  const w = await webllmModule();
  return w.prebuiltAppConfig.model_list
    .filter((m) => !/embed/i.test(m.model_id))
    .map((m) => ({
      id: m.model_id,
      label: m.model_id.replace(/-MLC$/, ""),
      size: m.vram_required_MB ? m.vram_required_MB * 1024 * 1024 : null,
      details: `${m.vram_required_MB ? "needs ~" + humanSize(m.vram_required_MB * 1048576) + " GPU memory" : ""}${m.low_resource_required ? " · fine on low-end devices" : ""}`,
      vram: m.vram_required_MB,
    }))
    .sort((a, b) => (a.vram || 0) - (b.vram || 0));
}

async function prepareWebLLM(m) {
  if (!navigator.gpu) { toast("This browser has no WebGPU, so WebLLM can't run here.", "bad"); return false; }
  if (state.engine && state.engineModel === m.id) return true;
  const w = await webllmModule();
  let cached = false;
  try { cached = await w.hasModelInCache(m.id); } catch { /* ignore */ }
  if (!cached) {
    const ok = await confirmDialog({
      title: "Download this model into your browser?",
      size: m.vram ? `~${humanSize(m.vram * 1048576 * 0.85)}` : "",
      lines: [
        `${m.label} runs inside this tab using your GPU.`,
        "The weights download once from Hugging Face and stay in the browser cache, so next time it starts offline.",
        m.vram ? `It needs about ${humanSize(m.vram * 1048576)} of GPU memory.` : "",
      ],
      okText: "Download & load",
    });
    if (!ok) return false;
  }
  const job = addJobCard({ label: `WebLLM: ${m.label}`, cancellable: false });
  try {
    if (state.engine) { await state.engine.unload().catch(() => {}); }
    state.engine = await w.CreateMLCEngine(m.id, {
      initProgressCallback: (r) => job.update({ percent: (r.progress || 0) * 100, status: r.text }),
    });
    state.engineModel = m.id;
    job.update({ percent: 100, status: "ready", state: "done" });
    job.finish(3000);
    return true;
  } catch (e) {
    job.update({ status: e.message, state: "error" });
    job.finish(9000);
    toast(`WebLLM couldn't load ${m.label}: ${e.message}`, "bad", 8000);
    return false;
  }
}

async function* webllmChat(messages, options) {
  if (!state.engine || state.engineModel !== state.model.id) {
    const ok = await prepareWebLLM({ id: state.model.id, label: state.model.label });
    if (!ok) throw new Error("model not loaded");
  }
  const started = performance.now();
  let first = null, count = 0, usage = null;
  const stream = await state.engine.chat.completions.create({
    messages, stream: true, stream_options: { include_usage: true },
    temperature: options.temperature, max_tokens: options.max_tokens || undefined,
  });
  for await (const chunk of stream) {
    if (chunk.usage) usage = chunk.usage;
    const text = chunk.choices?.[0]?.delta?.content;
    if (text) { if (first == null) first = performance.now(); count++; yield { type: "token", text }; }
  }
  const tokens = usage?.completion_tokens || count;
  const genSec = (performance.now() - (first || started)) / 1000;
  yield { type: "done", stats: { tokens, seconds: +((performance.now() - started) / 1000).toFixed(2), tokens_per_second: genSec > 0 ? +(tokens / genSec).toFixed(1) : null } };
}

// ------------------------------------------------------------------ llama.cpp / ollama helpers

async function loadLlamaCpp(path) {
  const name = path.split(/[\\/]/).pop();
  const job = addJobCard({ label: `llama.cpp: loading ${name}`, cancellable: false, indeterminate: true });
  try {
    await api("/api/llamacpp/load", { method: "POST", body: { path, context: +$("#context").value || 4096 } });
    job.update({ percent: 100, status: "ready", state: "done" });
    job.finish(2500);
    await refreshStatus();
    const running = (await api("/api/models?backend=llamacpp")).models;
    const m = running[0] || { id: name, label: name };
    if (state.backend === "llamacpp") loadModels();
    chooseModel("llamacpp", { ...m, label: name });
  } catch (e) {
    job.update({ status: e.message, state: "error" });
    job.finish(10000);
  }
}

async function importToOllama(path) {
  const suggested = path.split(/[\\/]/).pop().replace(/\.gguf$/i, "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-");
  const name = prompt("Name for this model in Ollama:", suggested);
  if (!name) return;
  const job = addJobCard({ label: `Ollama: creating ${name}`, cancellable: false, indeterminate: true });
  try {
    const r = await api("/api/ollama/import", { method: "POST", body: { path, name } });
    job.update({ percent: 100, status: "added to Ollama", state: "done" });
    job.finish(4000);
    selectTab("ollama");
    chooseModel("ollama", { id: r.ollama_model.includes(":") ? r.ollama_model : r.ollama_model + ":latest", label: r.ollama_model });
  } catch (e) {
    job.update({ status: e.message, state: "error" });
    job.finish(10000);
  }
}

// ------------------------------------------------------------------ chat

function addMessage(role, text = "") {
  $("#empty")?.remove();
  const node = el("div", { class: `msg ${role}` });
  if (role === "assistant") node.innerHTML = renderMarkdown(text);
  else node.textContent = text;
  $("#messages").append(node);
  node.scrollIntoView({ block: "end" });
  return node;
}

function setBusy(busy) {
  $("#send-btn").hidden = busy;
  $("#stop-btn").hidden = !busy;
}

async function send(text) {
  if (!state.model) { toast("Pick a model on the left first."); $("#sidebar").classList.add("open"); return; }
  const system = $("#system-prompt").value.trim();
  state.messages.push({ role: "user", content: text });
  addMessage("user", text);
  const bubble = addMessage("assistant");
  const body = el("div", { class: "cursor" });
  bubble.append(body);
  const messages = system ? [{ role: "system", content: system }, ...state.messages] : [...state.messages];
  const options = {
    temperature: parseFloat($("#temperature").value),
    max_tokens: parseInt($("#max-tokens").value, 10) || undefined,
  };
  let reply = "", reasoning = "", reasoningNode = null, stats = null;
  const controller = new AbortController();
  state.abort = controller;
  setBusy(true);
  const messagesEl = $("#messages");
  try {
    let events;
    if (state.model.backend === "webllm") {
      events = webllmChat(messages, options);
    } else {
      const res = await api("/api/chat", {
        method: "POST", raw: true, signal: controller.signal,
        body: { backend: state.model.backend, model: state.model.id, messages, options },
      });
      events = ndjson(res);
    }
    for await (const ev of events) {
      if (controller.signal.aborted) break;
      const nearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
      if (ev.type === "token") {
        reply += ev.text;
        body.innerHTML = renderMarkdown(reply);
      } else if (ev.type === "reasoning") {
        reasoning += ev.text;
        if (!reasoningNode) {
          reasoningNode = el("details", { class: "reasoning" }, el("summary", {}, "Thinking…"), el("div"));
          bubble.prepend(reasoningNode);
        }
        reasoningNode.lastChild.textContent = reasoning;
      } else if (ev.type === "done") {
        stats = ev.stats;
      } else if (ev.type === "error") {
        throw new Error(ev.message);
      }
      if (nearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  } catch (e) {
    if (e.name !== "AbortError" && !controller.signal.aborted) {
      addMessage("error", e.message);
    }
  } finally {
    body.classList.remove("cursor");
    if (reasoningNode) reasoningNode.firstChild.textContent = "Thoughts";
    if (!reply && !reasoning) bubble.remove();
    else {
      state.messages.push({ role: "assistant", content: reply });
      const bits = [];
      if (controller.signal.aborted) bits.push("stopped");
      if (stats) {
        bits.push(`${stats.tokens} tokens`, `${stats.seconds}s`);
        if (stats.tokens_per_second) bits.push(`${stats.tokens_per_second} tok/s`);
      }
      if (bits.length) bubble.append(el("div", { class: "stats" }, bits.join(" · ")));
    }
    state.abort = null;
    setBusy(false);
  }
}

function stop() {
  if (state.abort) state.abort.abort();
  if (state.model?.backend === "webllm" && state.engine) state.engine.interruptGenerate();
}

// ------------------------------------------------------------------ inspect (drag & drop)

async function inspectText(text) {
  showInspectLoading(text.length > 80 ? text.slice(0, 80) + "…" : text);
  try {
    const report = await api("/api/inspect", { method: "POST", body: { input: text } });
    renderReport(report);
  } catch (e) {
    renderInspectError(e.message);
  }
}

async function inspectFile(file) {
  if (/\.gguf$/i.test(file.name)) {
    showInspectLoading(file.name);
    try {
      const slice = file.slice(0, Math.min(file.size, HEADER_SLICE));
      const report = await api("/api/inspect-file", {
        method: "POST", body: slice,
        headers: { "X-Filename": encodeURIComponent(file.name).replace(/%20/g, " "), "X-File-Size": String(file.size), "Content-Type": "application/octet-stream" },
      });
      report._file = file;
      renderReport(report);
    } catch (e) {
      renderInspectError(e.message);
    }
    return;
  }
  if (file.size < 1024 * 1024) {
    const text = await file.text();
    return inspectText(text);
  }
  toast("Drop a .gguf file, or a link / text file naming a model.", "bad");
}

function openInspect() {
  const d = $("#inspect-dialog");
  if (!d.open) d.showModal();
}

function showInspectLoading(label) {
  $("#inspect-body").replaceChildren(el("div", {},
    el("h2", {}, el("span", { class: "spinner" }), "Checking…"),
    el("p", { class: "muted" }, label),
    el("p", { class: "muted small" }, "Looking up the files, whether you're allowed to download them, and whether they fit this machine. Nothing is downloaded."),
  ));
  openInspect();
}

function renderInspectError(message) {
  $("#inspect-body").replaceChildren(el("div", {},
    el("div", { class: "inspect-head" }, el("h2", {}, "Couldn't check that"), closeBtn()),
    el("div", { class: "verdict level-bad" }, el("span", { class: "icon" }, "✗"), el("span", {}, message)),
  ));
  openInspect();
}

function closeBtn() {
  return el("button", { class: "icon-btn", "aria-label": "Close", onclick: () => $("#inspect-dialog").close() }, "✕");
}

const ICON = { ok: "✓", warn: "!", bad: "✗" };

function renderReport(r) {
  const sys = r.system || {};
  const webllmMatch = r.webllm_model_id && state.webllmIds?.has(r.webllm_model_id);
  const wrap = el("div", {},
    el("div", { class: "inspect-head" },
      el("div", {},
        el("h2", {}, r.title),
        el("div", { class: "muted small" }, { hf: "Hugging Face", ollama: "Ollama library", url: "Direct link", local: "File on disk", upload: "File from your computer" }[r.source?.kind] || ""),
      ),
      closeBtn(),
    ),
    r.facts?.length ? el("div", { class: "facts" }, r.facts.map((f) => el("span", { class: "fact" }, f))) : null,
    el("div", { class: `verdict level-${r.verdict.level}` }, el("span", { class: "icon" }, ICON[r.verdict.level]), el("span", {}, r.verdict.headline)),
    el("dl", { class: "kv" },
      el("dt", {}, "Download"), el("dd", {}, r.access.ok ? "✓ " : "✗ ", r.access.message || (r.access.ok ? "accessible" : "not accessible"),
        r.access.link ? el("span", {}, " ", el("a", { href: r.access.link, target: "_blank", rel: "noopener" }, "Open model page")) : null),
      el("dt", {}, "This machine"), el("dd", {},
        `${humanSize(sys.memory_total)} RAM (${humanSize(sys.memory_available)} free) · ${humanSize(sys.disk_free)} free disk`,
        sys.gpu ? ` · ${sys.gpu}` : ""),
    ),
  );

  if (r.options?.length) {
    wrap.append(el("h2", {}, r.options.length > 1 ? "Pick a version" : "Get it"));
    const box = el("div", { class: "options" });
    for (const o of r.options) {
      const lvl = o.fit?.level || "ok";
      const buttons = el("div", { class: "buttons" });
      for (const a of o.actions) {
        const blocked = o.fit?.blocked && a.kind !== "remote";
        buttons.append(el("button", {
          class: `btn small ${a.kind === "ollama-pull" ? "ghost" : ""}`,
          disabled: blocked || null,
          title: blocked ? "Not enough free disk space" : null,
          onclick: () => runAction(r, o, a),
        }, a.label));
      }
      if (!o.actions.length && !r.access.ok) buttons.append(el("span", { class: "muted small" }, "not accessible"));
      box.append(el("div", { class: `option ${o.recommended ? "recommended" : ""}` },
        el("span", { class: `dot ${lvl}`, title: lvl }),
        el("div", {},
          el("span", { class: "title" }, o.title, o.recommended ? el("span", { class: "badge" }, "best fit") : null,
            el("small", {}, o.size_text || "")),
        ),
        buttons,
        el("div", { class: "msg-line" }, (o.fit?.messages || []).join(" ")),
      ));
    }
    wrap.append(box);
    queueMicrotask(() => box.querySelector(".recommended")?.scrollIntoView({ block: "nearest" }));
  }

  if (webllmMatch) {
    wrap.append(el("div", { class: "row" },
      el("button", { class: "btn", onclick: () => { $("#inspect-dialog").close(); selectTab("webllm"); chooseModel("webllm", { id: r.webllm_model_id, label: r.webllm_model_id.replace(/-MLC$/, "") }); } }, "Run in this browser (WebLLM)"),
    ));
  }

  if (r.notes?.length) wrap.append(el("ul", { class: "notes" }, r.notes.map((n) => el("li", {}, n))));
  if (r.suggestions?.length) {
    wrap.append(el("h2", {}, "GGUF versions you can run locally"));
    wrap.append(el("div", { class: "suggestions" }, r.suggestions.map((s) =>
      el("button", { title: s.detail || "", onclick: () => inspectText(s.input) }, s.label))));
  }
  if (r.links?.length) wrap.append(el("div", { class: "row small" }, r.links.map((l) => el("a", { href: l.url, target: "_blank", rel: "noopener" }, l.label + " ↗"))));
  $("#inspect-body").replaceChildren(wrap);
  openInspect();
}

async function runAction(report, option, action) {
  if (action.kind === "remote") {
    $("#inspect-dialog").close();
    selectTab("huggingface");
    chooseModel("huggingface", { id: action.remote_model, label: action.remote_model });
    if (state.status?.backends?.huggingface?.needs_token) toast("Set HF_TOKEN before starting llmstream to chat with hosted models.", "bad", 8000);
    return;
  }
  const fit = option.fit || { level: "ok", messages: [] };
  const where = action.kind === "ollama-pull" ? `into Ollama as ${action.ollama_model}` : action.kind === "import" ? `copy to ${action.dest}` : `to ${action.dest}`;
  const ok = await confirmDialog({
    title: action.kind === "import" ? "Copy this file into your models folder?" : `Download ${report.title}?`,
    size: option.size_text,
    lines: [
      `${option.title} — ${where}${action.file_count > 1 ? ` (${action.file_count} files)` : ""}.`,
      ...(fit.level !== "ok" ? fit.messages : []),
    ],
    level: fit.level,
    okText: action.kind === "import" ? "Copy" : fit.level === "ok" ? "Download" : "Download anyway",
  });
  if (!ok) return;
  $("#inspect-dialog").close();
  if (action.kind === "import") return uploadFile(report._file, action);
  try {
    const { job } = await api("/api/download", { method: "POST", body: { plan_id: action.plan_id, confirm: true } });
    followJob(job);
  } catch (e) {
    toast(e.message, "bad", 8000);
  }
}

function confirmDialog({ title, size, lines = [], level = "ok", okText = "Download" }) {
  return new Promise((resolve) => {
    const d = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-body").replaceChildren(
      size ? el("div", { class: "confirm-size" }, size) : null,
      ...lines.filter(Boolean).map((l, i) => el("p", { class: i === 0 ? "confirm-path" : `verdict level-${level}` }, l)),
    );
    const okBtn = $("#confirm-ok");
    okBtn.textContent = okText;
    okBtn.className = level === "bad" ? "btn danger" : "btn";
    d.returnValue = "";
    d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
    d.showModal();
  });
}

// ------------------------------------------------------------------ jobs (downloads)

function addJobCard({ label, cancellable = true, onCancel, indeterminate = false }) {
  const bar = el("span");
  const status = el("div", { class: "status" }, "starting…");
  const pct = el("span", { class: "muted" }, "");
  const buttons = el("div", { class: "buttons" });
  const card = el("div", { class: "job" }, el("div", { class: "top" }, el("span", {}, label), pct), el("div", { class: "bar" }, bar), status, buttons);
  if (cancellable && onCancel) buttons.append(el("button", { class: "btn small ghost", onclick: onCancel }, "Cancel"));
  $("#jobs").append(card);
  if (indeterminate) bar.style.width = "35%";
  return {
    card, buttons,
    update({ percent, status: s, state: st }) {
      if (percent != null) { bar.style.width = `${Math.min(100, percent)}%`; pct.textContent = `${Math.round(percent)}%`; }
      if (s != null) status.textContent = s;
      if (st) card.className = `job ${st}`;
    },
    finish(ms = 6000) { buttons.querySelectorAll("button").forEach((b) => b.textContent === "Cancel" && b.remove()); setTimeout(() => card.remove(), ms); },
  };
}

async function followJob(job) {
  const card = addJobCard({
    label: job.label === "Pull into Ollama" ? `Ollama pull` : job.label,
    onCancel: () => api(`/api/jobs/${job.job_id}/cancel`, { method: "POST" }).catch(() => {}),
  });
  try {
    const res = await api(`/api/jobs/${job.job_id}/events`, { raw: true });
    for await (const s of ndjson(res)) {
      if (s.heartbeat) continue;
      const speed = s.speed ? ` · ${humanSize(s.speed)}/s` : "";
      const amount = s.total ? `${humanSize(s.completed)} / ${humanSize(s.total)}` : humanSize(s.completed);
      card.update({ percent: s.percent, status: s.error || `${s.status} · ${amount}${speed}`, state: s.state });
      if (s.state === "done") {
        card.finish(15000);
        if (s.result?.path) {
          card.buttons.append(el("button", { class: "btn small", onclick: () => { card.card.remove(); loadLlamaCpp(s.result.path); } }, "Load in llama.cpp"));
          if (state.status?.system?.runtimes?.ollama_cli) {
            card.buttons.append(el("button", { class: "btn small ghost", onclick: () => { card.card.remove(); importToOllama(s.result.path); } }, "Add to Ollama"));
          }
          if (state.backend === "llamacpp") loadModels();
        } else if (s.result?.ollama_model) {
          card.buttons.append(el("button", { class: "btn small", onclick: () => {
            card.card.remove(); selectTab("ollama");
            const id = s.result.ollama_model.includes(":") ? s.result.ollama_model : `${s.result.ollama_model}:latest`;
            chooseModel("ollama", { id, label: s.result.ollama_model });
          } }, "Chat with it"));
          if (state.backend === "ollama") loadModels();
        }
      } else if (s.state === "error" || s.state === "cancelled") {
        card.finish(12000);
      }
    }
  } catch (e) {
    card.update({ status: e.message, state: "error" });
    card.finish(10000);
  }
}

function uploadFile(file, action) {
  const card = addJobCard({ label: `Copying ${file.name}` });
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/import?plan_id=${encodeURIComponent(action.plan_id)}&confirm=1`);
  xhr.setRequestHeader("X-LLMStream", "1");
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.upload.onprogress = (e) => e.lengthComputable && card.update({ percent: (100 * e.loaded) / e.total, status: `${humanSize(e.loaded)} / ${humanSize(e.total)}` });
  card.buttons.append(el("button", { class: "btn small ghost", onclick: () => xhr.abort() }, "Cancel"));
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
    if (xhr.status >= 200 && xhr.status < 300) {
      card.update({ percent: 100, status: `saved to ${data.path}`, state: "done" });
      card.finish(15000);
      card.buttons.append(el("button", { class: "btn small", onclick: () => { card.card.remove(); loadLlamaCpp(data.path); } }, "Load in llama.cpp"));
      if (state.backend === "llamacpp") loadModels();
    } else {
      card.update({ status: data.error || `HTTP ${xhr.status}`, state: "error" });
      card.finish(10000);
    }
  };
  xhr.onerror = xhr.onabort = () => { card.update({ status: "stopped", state: "error" }); card.finish(6000); };
  xhr.send(file);
}

// ------------------------------------------------------------------ drag & drop wiring

let dragDepth = 0;
const overlay = $("#drop-overlay");
const isExternalDrag = (e) => {
  const types = Array.from(e.dataTransfer?.types || []);
  return types.includes("Files") || types.includes("text/uri-list") || types.includes("text/plain");
};
window.addEventListener("dragenter", (e) => {
  if (!isExternalDrag(e)) return;
  e.preventDefault();
  dragDepth++;
  overlay.hidden = false;
});
window.addEventListener("dragover", (e) => { if (isExternalDrag(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
window.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) overlay.hidden = true; });
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  overlay.hidden = true;
  const dt = e.dataTransfer;
  if (dt.files && dt.files.length) return inspectFile(dt.files[0]);
  const text = dt.getData("text/uri-list") || dt.getData("text/plain");
  if (text) inspectText(text);
});
window.addEventListener("paste", (e) => {
  const target = e.target;
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) return;
  const text = e.clipboardData?.getData("text/plain");
  if (text && /huggingface\.co|hf\.co|ollama\.com|\.gguf/i.test(text)) inspectText(text);
});

// ------------------------------------------------------------------ UI wiring

$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-backend]"); if (b) selectTab(b.dataset.backend); });
$("#model-search").addEventListener("input", renderModels);
$("#inspect-form").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#inspect-input").value.trim(); if (v) inspectText(v); });
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#prompt").value.trim();
  if (!text || state.abort) return;
  $("#prompt").value = "";
  autosize();
  send(text);
});
$("#prompt").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); }
});
const autosize = () => { const t = $("#prompt"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; };
$("#prompt").addEventListener("input", autosize);
$("#stop-btn").addEventListener("click", stop);
$("#clear-btn").addEventListener("click", () => {
  stop();
  state.messages = [];
  $("#messages").replaceChildren(el("div", { class: "empty", id: "empty" }, el("h1", {}, "New chat"), el("p", { class: "muted" }, state.model ? `Talking to ${state.model.label}.` : "Pick a model on the left.")));
});
$("#settings-btn").addEventListener("click", () => {
  const s = $("#settings");
  s.hidden = !s.hidden;
  $("#settings-btn").setAttribute("aria-expanded", String(!s.hidden));
});
$("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
$("#inspect-dialog").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
for (const id of ["system-prompt", "temperature", "max-tokens", "context"]) {
  const node = document.getElementById(id);
  const saved = localStorage.getItem(`setting:${id}`);
  if (saved != null) node.value = saved;
  node.addEventListener("change", () => localStorage.setItem(`setting:${id}`, node.value));
}

// boot
renderCurrent();
selectTab(state.backend);
refreshStatus();
setInterval(refreshStatus, 15000);
if (navigator.gpu) {
  webllmModule().then((w) => { state.webllmIds = new Set(w.prebuiltAppConfig.model_list.map((m) => m.model_id)); }).catch(() => {});
}
api("/api/jobs").then(({ jobs }) => jobs.filter((j) => j.state === "running").forEach(followJob)).catch(() => {});
