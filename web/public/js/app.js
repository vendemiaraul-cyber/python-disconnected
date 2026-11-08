// LLM Stream — website edition. Everything runs in the visitor's browser.

import * as B from "./backends.js";
import { inspect, inspectFile, applyFit, humanSize } from "./inspect.js";

const WEBLLM_URL = "https://cdn.jsdelivr.net/npm/@mlc-ai/web-llm@0.2.79/+esm";
const GB = 1024 ** 3;

const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...children) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) n.append(c.nodeType ? c : String(c));
  return n;
};

const state = {
  backend: B.load("backend") || "webllm",
  model: null,
  models: [],
  status: null,
  messages: [],
  abort: null,
  webllm: null,
  webllmIds: null,
  engine: null,
  engineModel: null,
  report: null,
};
try { state.model = JSON.parse(B.load("model") || "null"); } catch { /* ignore */ }

// ------------------------------------------------------------------ machine info

function machine() {
  const ram = parseFloat(B.load("ramGB"));
  const disk = parseFloat(B.load("diskGB"));
  const guess = navigator.deviceMemory; // Chrome only, capped at 8
  return {
    ramBytes: (ram || guess || 0) * GB,
    diskBytes: disk ? disk * GB : null,
    ramGuessed: !ram,
    ramGuess: guess,
  };
}

function ramHint() {
  const m = machine();
  if (!m.ramGuessed) return "";
  if (!m.ramGuess) return "Your browser doesn't share how much RAM you have. Enter it for accurate checks.";
  return m.ramGuess >= 8
    ? "Your browser only reports “at least 8 GB”. Enter your real RAM for accurate checks."
    : `Your browser reports about ${m.ramGuess} GB of RAM.`;
}

// ------------------------------------------------------------------ helpers

function toast(text, kind = "", ms = 4500) {
  const t = el("div", { class: `toast ${kind}` }, text);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

function renderMarkdown(text) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return text.split(/```/).map((part, i) => {
    if (i % 2) {
      const nl = part.indexOf("\n");
      return `<pre><code>${esc((nl >= 0 ? part.slice(nl + 1) : part).replace(/\n$/, ""))}</code></pre>`;
    }
    return esc(part).split(/\n{2,}/).filter((p) => p.trim()).map((p) => `<p>${p
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\n/g, "<br>")}</p>`).join("");
  }).join("");
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { prompt("Copy this:", text); }
}

// ------------------------------------------------------------------ status + models

async function refreshStatus() {
  state.status = await B.status();
  const s = state.status;
  const webgpu = !!navigator.gpu;
  $("#pills").replaceChildren(
    el("span", { class: `pill ${webgpu ? "on" : "off"}`, title: webgpu ? "WebGPU available" : "No WebGPU in this browser" }, "WebLLM"),
    el("span", { class: `pill ${s.ollama.available ? "on" : "off"}`, title: s.ollama.detail }, "Ollama"),
    el("span", { class: `pill ${s.llamacpp.available ? "on" : "off"}`, title: s.llamacpp.detail }, "llama.cpp"),
    el("span", { class: `pill ${s.huggingface.needsToken ? "" : "on"}`, title: s.huggingface.detail }, "Hugging Face"),
  );
  renderBackendDetail();
}

function renderBackendDetail() {
  let text = "";
  if (state.backend === "webllm") {
    text = navigator.gpu
      ? "Runs entirely in this tab with your GPU. Weights download once, then stay cached (works offline)."
      : "This browser has no WebGPU, so WebLLM can't run here. Try a recent Chrome or Edge, or use another backend.";
  } else if (state.status) text = state.status[state.backend]?.detail || "";
  $("#backend-detail").textContent = text;
}

function selectTab(name) {
  state.backend = name;
  B.save("backend", name);
  for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-selected", String(b.dataset.backend === name));
  renderBackendDetail();
  if (name === "ollama" || name === "llamacpp") $("#connect").open = !state.status?.[name]?.available;
  loadModels();
}

async function loadModels() {
  const list = $("#model-list");
  const backend = state.backend;
  list.replaceChildren(el("li", { class: "hint" }, el("span", { class: "spinner" }), "Loading models…"));
  try {
    state.models = backend === "webllm" ? await webllmModels() : await B.listModels(backend);
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
      webllm: navigator.gpu ? "Nothing matches." : "WebLLM needs WebGPU (Chrome or Edge on a computer).",
      ollama: "No Ollama models yet. Drag a link from ollama.com here to pull one.",
      llamacpp: "No model loaded in llama-server.",
      huggingface: "Nothing matches.",
    };
    list.append(el("li", { class: "hint" }, q ? "Nothing matches." : hints[state.backend]));
    return;
  }
  for (const m of items.slice(0, 400)) {
    const active = state.model && state.model.backend === state.backend && state.model.id === m.id;
    list.append(el("li", { class: active ? "active" : "", title: m.id, onclick: () => chooseModel(state.backend, m) },
      el("span", { class: "name" }, m.label),
      el("span", { class: "size" }, m.size ? humanSize(m.size) : ""),
      el("span", { class: "meta" }, m.details || "")));
  }
}

async function chooseModel(backend, m) {
  if (backend === "webllm" && !(await prepareWebLLM(m))) return;
  state.model = { backend, id: m.id, label: m.label };
  B.save("model", JSON.stringify(state.model));
  renderCurrent();
  renderModels();
  $("#sidebar").classList.remove("open");
  $("#prompt").focus();
}

function renderCurrent() {
  const names = { webllm: "WebLLM (in your browser)", ollama: "Ollama (your computer)", llamacpp: "llama.cpp (your computer)", huggingface: "Hugging Face Inference" };
  $("#current-model").textContent = state.model ? state.model.label : "No model selected";
  $("#current-backend").textContent = state.model ? names[state.model.backend] : "Pick one on the left";
}

// ------------------------------------------------------------------ WebLLM

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
      size: m.vram_required_MB ? m.vram_required_MB * 1048576 : null,
      details: `${m.vram_required_MB ? "needs ~" + humanSize(m.vram_required_MB * 1048576) + " GPU memory" : ""}${m.low_resource_required ? " · fine on low-end devices" : ""}`,
      vram: m.vram_required_MB,
    }))
    .sort((a, b) => (a.vram || 0) - (b.vram || 0));
}

async function prepareWebLLM(m) {
  if (!navigator.gpu) { toast("This browser has no WebGPU, so WebLLM can't run here.", "bad"); return false; }
  if (state.engine && state.engineModel === m.id) return true;
  const w = await webllmModule();
  const info = w.prebuiltAppConfig.model_list.find((x) => x.model_id === m.id);
  const vram = m.vram || info?.vram_required_MB;
  let cached = false;
  try { cached = await w.hasModelInCache(m.id); } catch { /* ignore */ }
  if (!cached) {
    const ok = await confirmDialog({
      title: "Download this model into your browser?",
      size: vram ? `~${humanSize(vram * 1048576 * 0.85)}` : "",
      lines: [
        `${m.label} runs inside this tab using your GPU.`,
        "The weights download once from Hugging Face and stay in your browser's cache, so next time it starts offline.",
        vram ? `It needs about ${humanSize(vram * 1048576)} of GPU memory.` : "",
      ],
      okText: "Download & load",
    });
    if (!ok) return false;
  }
  const job = addJobCard({ label: `WebLLM: ${m.label}` });
  try {
    if (state.engine) await state.engine.unload().catch(() => {});
    state.engine = await w.CreateMLCEngine(m.id, { initProgressCallback: (r) => job.update({ percent: (r.progress || 0) * 100, status: r.text }) });
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
    if (!(await prepareWebLLM({ id: state.model.id, label: state.model.label }))) throw new Error("model not loaded");
  }
  const started = performance.now();
  let first = null, count = 0, usage = null;
  const stream = await state.engine.chat.completions.create({
    messages, stream: true, stream_options: { include_usage: true },
    temperature: options.temperature, max_tokens: options.max_tokens || undefined,
  });
  for await (const chunk of stream) {
    if (chunk.usage) usage = chunk.usage;
    const t = chunk.choices?.[0]?.delta?.content;
    if (t) { if (first == null) first = performance.now(); count++; yield { type: "token", text: t }; }
  }
  const tokens = usage?.completion_tokens || count;
  const gen = (performance.now() - (first || started)) / 1000;
  yield { type: "done", stats: { tokens, seconds: +((performance.now() - started) / 1000).toFixed(2), tokens_per_second: gen > 0 ? +(tokens / gen).toFixed(1) : null } };
}

// ------------------------------------------------------------------ chat

function addMessage(role, text = "") {
  $("#empty")?.remove();
  const n = el("div", { class: `msg ${role}` });
  if (role === "assistant") n.innerHTML = renderMarkdown(text); else n.textContent = text;
  $("#messages").append(n);
  n.scrollIntoView({ block: "end" });
  return n;
}

function setBusy(b) { $("#send-btn").hidden = b; $("#stop-btn").hidden = !b; }

async function send(text) {
  if (!state.model) { toast("Pick a model on the left first."); $("#sidebar").classList.add("open"); return; }
  const system = $("#system-prompt").value.trim();
  state.messages.push({ role: "user", content: text });
  addMessage("user", text);
  const bubble = addMessage("assistant");
  const body = el("div", { class: "cursor" });
  bubble.append(body);
  const messages = system ? [{ role: "system", content: system }, ...state.messages] : [...state.messages];
  const options = { temperature: parseFloat($("#temperature").value), max_tokens: parseInt($("#max-tokens").value, 10) || undefined };
  let reply = "", reasoning = "", reasoningNode = null, stats = null;
  const controller = new AbortController();
  state.abort = controller;
  setBusy(true);
  const box = $("#messages");
  try {
    const events = state.model.backend === "webllm" ? webllmChat(messages, options) : B.chat(state.model.backend, state.model.id, messages, options, controller.signal);
    for await (const ev of events) {
      if (controller.signal.aborted) break;
      const near = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
      if (ev.type === "token") { reply += ev.text; body.innerHTML = renderMarkdown(reply); }
      else if (ev.type === "reasoning") {
        reasoning += ev.text;
        if (!reasoningNode) { reasoningNode = el("details", { class: "reasoning" }, el("summary", {}, "Thinking…"), el("div")); bubble.prepend(reasoningNode); }
        reasoningNode.lastChild.textContent = reasoning;
      } else if (ev.type === "done") stats = ev.stats;
      if (near) box.scrollTop = box.scrollHeight;
    }
  } catch (e) {
    if (e.name !== "AbortError" && !controller.signal.aborted) {
      const local = ["ollama", "llamacpp"].includes(state.model.backend) && /fetch|network/i.test(e.message);
      addMessage("error", local ? `Couldn't reach ${state.model.backend === "ollama" ? "Ollama" : "llama-server"} on your computer. Open “Connect your computer” on the left.` : e.message);
    }
  } finally {
    body.classList.remove("cursor");
    if (reasoningNode) reasoningNode.firstChild.textContent = "Thoughts";
    if (!reply && !reasoning) bubble.remove();
    else {
      state.messages.push({ role: "assistant", content: reply });
      const bits = [];
      if (controller.signal.aborted) bits.push("stopped");
      if (stats) { bits.push(`${stats.tokens} tokens`, `${stats.seconds}s`); if (stats.tokens_per_second) bits.push(`${stats.tokens_per_second} tok/s`); }
      if (bits.length) bubble.append(el("div", { class: "stats" }, bits.join(" · ")));
    }
    state.abort = null;
    setBusy(false);
  }
}

function stop() {
  state.abort?.abort();
  if (state.model?.backend === "webllm" && state.engine) state.engine.interruptGenerate();
}

// ------------------------------------------------------------------ inspect (drag & drop)

const ctx = () => ({ hfToken: B.settings.hfToken, webllmIds: state.webllmIds });

async function inspectText(text) {
  showInspectLoading(text.length > 80 ? text.slice(0, 80) + "…" : text);
  try {
    state.report = await inspect(text, ctx());
    renderReport();
  } catch (e) { renderInspectError(e.message); }
}

async function inspectDroppedFile(file) {
  if (/\.gguf$/i.test(file.name)) {
    showInspectLoading(file.name);
    try { state.report = await inspectFile(file); renderReport(); } catch (e) { renderInspectError(e.message); }
    return;
  }
  if (file.size < 1024 * 1024) return inspectText(await file.text());
  toast("Drop a .gguf file, or a link / text file naming a model.", "bad");
}

function openInspect() { const d = $("#inspect-dialog"); if (!d.open) d.showModal(); }
const closeBtn = () => el("button", { class: "icon-btn", "aria-label": "Close", onclick: () => $("#inspect-dialog").close() }, "✕");

function showInspectLoading(label) {
  $("#inspect-body").replaceChildren(el("div", {},
    el("h2", {}, el("span", { class: "spinner" }), "Checking…"),
    el("p", { class: "muted" }, label),
    el("p", { class: "muted small" }, "Looking up the files, whether you're allowed to download them, and whether they fit your computer. Nothing is downloaded.")));
  openInspect();
}

function renderInspectError(msg) {
  $("#inspect-body").replaceChildren(el("div", {},
    el("div", { class: "inspect-head" }, el("h2", {}, "Couldn't check that"), closeBtn()),
    el("div", { class: "verdict level-bad" }, el("span", { class: "icon" }, "✗"), el("span", {}, msg))));
  openInspect();
}

const ICON = { ok: "✓", warn: "!", bad: "✗" };
const KIND = { hf: "Hugging Face", ollama: "Ollama library", url: "Direct link", local: "File path", upload: "File from your computer" };

function renderReport() {
  const r = applyFit(state.report, machine());
  const m = machine();
  const ramInput = el("input", { type: "number", min: "1", step: "1", value: m.ramGuessed ? (m.ramGuess || "") : Math.round(m.ramBytes / GB), placeholder: "?" });
  const diskInput = el("input", { type: "number", min: "1", step: "1", value: m.diskBytes ? Math.round(m.diskBytes / GB) : "", placeholder: "?" });
  const recompute = () => { B.save("ramGB", ramInput.value); B.save("diskGB", diskInput.value); renderReport(); };
  ramInput.addEventListener("change", recompute);
  diskInput.addEventListener("change", recompute);

  const wrap = el("div", {},
    el("div", { class: "inspect-head" },
      el("div", {}, el("h2", {}, r.title), el("div", { class: "muted small" }, KIND[r.source?.kind] || "")),
      closeBtn()),
    r.facts.length ? el("div", { class: "facts" }, r.facts.map((f) => el("span", { class: "fact" }, f))) : null,
    el("div", { class: `verdict level-${r.verdict.level}` }, el("span", { class: "icon" }, ICON[r.verdict.level]), el("span", {}, r.verdict.headline)),
    el("dl", { class: "kv" },
      el("dt", {}, "Download"),
      el("dd", {}, r.access.ok ? "✓ " : "✗ ", r.access.message || "",
        r.access.link ? el("span", {}, " ", el("a", { href: r.access.link, target: "_blank", rel: "noopener" }, "Open model page")) : null),
      el("dt", {}, "Your computer"),
      el("dd", {}, el("div", { class: "machine-row" },
        el("label", {}, "RAM", ramInput, "GB"),
        el("label", {}, "free disk", diskInput, "GB"),
        m.ramGuessed ? el("span", { class: "muted small" }, ramHint()) : null))));

  if (r.options.length) {
    wrap.append(el("h2", {}, r.options.length > 1 ? "Pick a version" : "Get it"));
    const box = el("div", { class: "options" });
    for (const o of r.options) {
      const lvl = o.fit.level;
      const buttons = el("div", { class: "buttons" });
      for (const a of o.actions) {
        const blocked = o.fit.blocked && ["download-link", "ollama-pull"].includes(a.kind);
        buttons.append(el("button", {
          class: `btn small ${a.kind === "download-link" || a.kind === "remote" ? "" : "ghost"}`,
          disabled: blocked || null,
          title: blocked ? "Not enough free disk space" : a.command || null,
          onclick: () => runAction(r, o, a),
        }, a.label));
      }
      if (!o.actions.length && !r.access.ok) buttons.append(el("span", { class: "muted small" }, "not accessible"));
      box.append(el("div", { class: `option ${o.recommended ? "recommended" : ""}` },
        el("span", { class: `dot ${lvl}` }),
        el("div", {}, el("span", { class: "title" }, o.title, o.recommended ? el("span", { class: "badge" }, "best fit") : null, el("small", {}, o.remote ? "no download" : humanSize(o.size)))),
        buttons,
        el("div", { class: "msg-line" }, o.fit.messages.join(" "))));
    }
    wrap.append(box);
    queueMicrotask(() => box.querySelector(".recommended")?.scrollIntoView({ block: "nearest" }));
  }

  if (r.webllmId) {
    wrap.append(el("div", { class: "row" }, el("button", { class: "btn", onclick: () => {
      $("#inspect-dialog").close(); selectTab("webllm"); chooseModel("webllm", { id: r.webllmId, label: r.webllmId.replace(/-MLC$/, "") });
    } }, "Run in this browser (WebLLM)")));
  }
  const notes = [...r.notes, r.estimateNote].filter(Boolean);
  if (notes.length) wrap.append(el("ul", { class: "notes" }, notes.map((n) => el("li", {}, n))));
  if (r.suggestions.length) {
    wrap.append(el("h2", {}, "GGUF versions you can run locally"));
    wrap.append(el("div", { class: "suggestions" }, r.suggestions.map((s) => el("button", { title: s.detail || "", onclick: () => inspectText(s.input) }, s.label))));
  }
  if (r.links.length) wrap.append(el("div", { class: "row small" }, r.links.map((l) => el("a", { href: l.url, target: "_blank", rel: "noopener" }, l.label + " ↗"))));
  $("#inspect-body").replaceChildren(wrap);
  openInspect();
}

async function runAction(r, o, a) {
  if (a.kind === "remote") {
    $("#inspect-dialog").close();
    selectTab("huggingface");
    chooseModel("huggingface", { id: a.model, label: a.model });
    if (!B.settings.hfToken) { toast("Add your Hugging Face token in Settings to chat with hosted models.", "bad", 8000); openSettings(); }
    return;
  }
  if (a.kind === "command") return copy(a.command);

  const fit = o.fit;
  const pull = a.kind === "ollama-pull";
  if (pull && !state.status?.ollama?.available) {
    await refreshStatus();
    if (!state.status.ollama.available) {
      toast("Your Ollama isn't reachable from this site yet. Follow “Connect your computer”, then try again.", "bad", 9000);
      $("#inspect-dialog").close();
      $("#connect").open = true;
      $("#sidebar").classList.add("open");
      return;
    }
  }
  const files = a.files || [];
  const ok = await confirmDialog({
    title: `${pull ? "Pull" : "Download"} ${r.title}?`,
    size: humanSize(o.size),
    lines: [
      pull ? `${o.title}: into your Ollama as ${a.model}.` : `${o.title}: ${files.length > 1 ? `${files.length} files` : files[0]?.name}, saved by your browser to your Downloads folder.`,
      ...(fit.level !== "ok" ? fit.messages : []),
    ],
    level: fit.level,
    okText: fit.level === "ok" ? (pull ? "Pull" : "Download") : (pull ? "Pull anyway" : "Download anyway"),
  });
  if (!ok) return;
  $("#inspect-dialog").close();
  if (pull) return followPull(a.model);
  for (const [i, f] of files.entries()) {
    setTimeout(() => { const link = el("a", { href: f.url, download: f.name, rel: "noopener" }); document.body.append(link); link.click(); link.remove(); }, i * 1200);
  }
  toast(files.length > 1 ? `Starting ${files.length} downloads. Allow multiple downloads if your browser asks, and keep the parts in one folder.` : "Download started. Check your browser's downloads.", "", 8000);
  const local = el("div", { class: "cmd" }, el("code", {}, `llama-server -m "${files[0]?.name}"`), el("button", { onclick: () => copy(`llama-server -m "${files[0]?.name}"`) }, "Copy"));
  const card = addJobCard({ label: "When it finishes, run it with llama.cpp:" });
  card.update({ percent: 100, status: "", state: "done" });
  card.buttons.replaceWith(local);
  setTimeout(() => card.card.remove(), 30000);
}

function confirmDialog({ title, size, lines = [], level = "ok", okText = "Download" }) {
  return new Promise((resolve) => {
    const d = $("#confirm-dialog");
    $("#confirm-title").textContent = title;
    $("#confirm-body").replaceChildren(
      size ? el("div", { class: "confirm-size" }, size) : null,
      ...lines.filter(Boolean).map((l, i) => el("p", { class: i === 0 ? "confirm-path" : `verdict level-${level}` }, l)));
    const okBtn = $("#confirm-ok");
    okBtn.textContent = okText;
    okBtn.className = level === "bad" ? "btn danger" : "btn";
    d.returnValue = "";
    d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
    d.showModal();
  });
}

// ------------------------------------------------------------------ progress cards

function addJobCard({ label, onCancel }) {
  const bar = el("span");
  const status = el("div", { class: "status" }, "starting…");
  const pct = el("span", { class: "muted" });
  const buttons = el("div", { class: "buttons" });
  const card = el("div", { class: "job" }, el("div", { class: "top" }, el("span", {}, label), pct), el("div", { class: "bar" }, bar), status, buttons);
  if (onCancel) buttons.append(el("button", { class: "btn small ghost", onclick: onCancel }, "Cancel"));
  $("#jobs").append(card);
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

async function followPull(model) {
  const controller = new AbortController();
  const card = addJobCard({ label: `Ollama pull ${model}`, onCancel: () => controller.abort() });
  const started = performance.now();
  try {
    for await (const p of B.ollamaPull(model, controller.signal)) {
      const secs = (performance.now() - started) / 1000;
      const speed = p.completed && secs > 1 ? ` · ${humanSize(p.completed / secs)}/s` : "";
      card.update({ percent: p.total ? (100 * p.completed) / p.total : null, status: `${p.status}${p.total ? ` · ${humanSize(p.completed)} / ${humanSize(p.total)}` : ""}${speed}` });
    }
    card.update({ percent: 100, status: "pulled into Ollama", state: "done" });
    card.finish(15000);
    card.buttons.append(el("button", { class: "btn small", onclick: () => {
      card.card.remove(); selectTab("ollama");
      chooseModel("ollama", { id: model.includes(":") ? model : `${model}:latest`, label: model });
    } }, "Chat with it"));
    if (state.backend === "ollama") loadModels();
  } catch (e) {
    card.update({ status: controller.signal.aborted ? "cancelled (Ollama resumes it next time)" : e.message, state: "error" });
    card.finish(12000);
  }
}

// ------------------------------------------------------------------ settings

function openSettings() {
  const m = machine();
  $("#set-ram").value = m.ramGuessed ? "" : Math.round(m.ramBytes / GB);
  $("#set-disk").value = m.diskBytes ? Math.round(m.diskBytes / GB) : "";
  $("#ram-hint").textContent = ramHint();
  $("#set-hf").value = B.settings.hfToken;
  $("#set-ollama").value = B.load("ollamaUrl") || "";
  $("#set-llama").value = B.load("llamaUrl") || "";
  $("#settings-dialog").showModal();
}

$("#settings-dialog").addEventListener("close", () => {
  if ($("#settings-dialog").returnValue !== "save") return;
  B.save("ramGB", $("#set-ram").value.trim());
  B.save("diskGB", $("#set-disk").value.trim());
  B.save("hfToken", $("#set-hf").value.trim());
  B.save("ollamaUrl", $("#set-ollama").value.trim());
  B.save("llamaUrl", $("#set-llama").value.trim());
  toast("Saved");
  refreshStatus().then(loadModels);
});

// ------------------------------------------------------------------ drag & drop

let depth = 0;
const overlay = $("#drop-overlay");
const external = (e) => { const t = Array.from(e.dataTransfer?.types || []); return t.includes("Files") || t.includes("text/uri-list") || t.includes("text/plain"); };
window.addEventListener("dragenter", (e) => { if (!external(e)) return; e.preventDefault(); depth++; overlay.hidden = false; });
window.addEventListener("dragover", (e) => { if (external(e)) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; } });
window.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) overlay.hidden = true; });
window.addEventListener("drop", (e) => {
  e.preventDefault();
  depth = 0;
  overlay.hidden = true;
  const dt = e.dataTransfer;
  if (dt.files?.length) return inspectDroppedFile(dt.files[0]);
  const text = dt.getData("text/uri-list") || dt.getData("text/plain");
  if (text) inspectText(text);
});
window.addEventListener("paste", (e) => {
  if (e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement) return;
  const text = e.clipboardData?.getData("text/plain");
  if (text && /huggingface\.co|hf\.co|ollama\.com|\.gguf/i.test(text)) inspectText(text);
});

// ------------------------------------------------------------------ wiring

$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-backend]"); if (b) selectTab(b.dataset.backend); });
$("#model-search").addEventListener("input", renderModels);
$("#inspect-form").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#inspect-input").value.trim(); if (v) inspectText(v); });
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("#prompt").value.trim();
  if (!t || state.abort) return;
  $("#prompt").value = "";
  autosize();
  send(t);
});
$("#prompt").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#composer").requestSubmit(); } });
const autosize = () => { const t = $("#prompt"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 200) + "px"; };
$("#prompt").addEventListener("input", autosize);
$("#stop-btn").addEventListener("click", stop);
$("#clear-btn").addEventListener("click", () => {
  stop();
  state.messages = [];
  $("#messages").replaceChildren(el("div", { class: "empty", id: "empty" }, el("h1", {}, "New chat"), el("p", { class: "muted" }, state.model ? `Talking to ${state.model.label}.` : "Pick a model on the left.")));
});
$("#settings-btn").addEventListener("click", () => { const s = $("#settings"); s.hidden = !s.hidden; $("#settings-btn").setAttribute("aria-expanded", String(!s.hidden)); });
$("#open-settings").addEventListener("click", openSettings);
$("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
$("#inspect-dialog").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
for (const id of ["system-prompt", "temperature", "max-tokens"]) {
  const n = document.getElementById(id);
  const saved = B.load(`setting:${id}`);
  if (saved != null) n.value = saved;
  n.addEventListener("change", () => B.save(`setting:${id}`, n.value));
}
document.addEventListener("click", (e) => { const b = e.target.closest("[data-copy]"); if (b) copy(document.getElementById(b.dataset.copy).textContent); });
$("#ollama-cmd").textContent = `OLLAMA_ORIGINS="${location.origin}" ollama serve`;
$("#ollama-cmd-win").textContent = `$env:OLLAMA_ORIGINS="${location.origin}"; ollama serve`;

// boot
renderCurrent();
refreshStatus().then(() => selectTab(state.backend));
setInterval(refreshStatus, 20000);
if (navigator.gpu) webllmModule().then((w) => { state.webllmIds = new Set(w.prebuiltAppConfig.model_list.map((m) => m.model_id)); }).catch(() => {});
