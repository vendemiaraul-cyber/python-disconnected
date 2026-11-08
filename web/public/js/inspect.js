// "Can I download / run this model?" — browser version of probe.py.
// Hugging Face is queried directly (it allows browser requests); the Ollama
// registry and arbitrary links go through the site's small /api/proxy function.
// Nothing is downloaded here; fit is computed separately from the visitor's machine.

import * as gguf from "./gguf.js";
import { parse, label } from "./sources.js";

const GB = 1024 ** 3;
const HUB = "https://huggingface.co";
const SPLIT_RE = /^(.*?)-(\d{5})-of-(\d{5})\.gguf$/i;
const PREFERRED = ["Q4_K_M", "Q4_K_S", "Q5_K_M", "IQ4_XS", "Q4_0", "Q5_K_S", "Q6_K", "Q3_K_L", "Q3_K_M", "Q8_0"];
export const LEVELS = { ok: 0, warn: 1, bad: 2 };

export function humanSize(n) {
  if (n == null || isNaN(n)) return "unknown size";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i < 2 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}

const paramText = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1).replace(/\.0$/, "")}B` : n >= 1e6 ? `${Math.round(n / 1e6)}M` : n.toLocaleString());

// machine: { ramBytes, diskBytes|null }. The browser can't see free RAM, so ~70% of total counts as comfortable.
export function assessFit(size, machine, overhead = null) {
  if (!size) return { level: "warn", messages: ["Size unknown, so I can't tell whether it fits."], blocked: false };
  const msgs = [];
  let level = "ok", blocked = false;
  const bump = (l) => { if (LEVELS[l] > LEVELS[level]) level = l; };
  const disk = machine.diskBytes;
  if (disk) {
    if (size + 256 * 1024 ** 2 > disk) { bump("bad"); blocked = true; msgs.push(`Not enough free disk: needs ${humanSize(size)}, you said ${humanSize(disk)} free.`); }
    else if (size > disk * 0.8) { bump("warn"); msgs.push(`Uses most of your free disk (${humanSize(size)} of ${humanSize(disk)}).`); }
  }
  const need = size + (overhead ?? Math.max(0.5 * GB, size * 0.1));
  const total = machine.ramBytes;
  if (total) {
    if (need <= total * 0.7) msgs.push(`Should fit in memory: ~${humanSize(need)} needed of your ${humanSize(total)}.`);
    else if (need <= total * 0.9) { bump("warn"); msgs.push(`Tight: ~${humanSize(need)} needed of your ${humanSize(total)}. Close other apps first.`); }
    else if (need <= total * 1.5) { bump("bad"); msgs.push(`Bigger than your RAM (~${humanSize(need)} vs ${humanSize(total)}): it would swap and crawl, if it loads at all.`); }
    else { bump("bad"); msgs.push(`Too big for this computer: needs ~${humanSize(need)}, you have ${humanSize(total)}.`); }
  } else {
    bump("warn");
    msgs.push(`Plan on ~${humanSize(need)} of memory. Set your RAM to check.`);
  }
  return { level, messages: msgs, blocked, memoryNeeded: need };
}

export function headline(level, name, local = false) {
  if (local) return { ok: `${name} should run on this computer.`, warn: `${name} will be tight on this computer.`, bad: `${name} probably won't run well on this computer.` }[level];
  return { ok: `${name} can be downloaded and should run on this computer.`, warn: `${name} can be downloaded, but it'll be tight on this computer.`, bad: `${name} probably won't run well on this computer.` }[level];
}

function base(title, kind) {
  return { title, kind, facts: [], access: { ok: true, status: "unknown", message: "" }, options: [], notes: [], suggestions: [], links: [], verdict: null, webllmId: null };
}

async function getJSON(url, headers = {}) {
  const res = await fetch(url, { headers });
  if (!res.ok) { const e = new Error(`HTTP ${res.status}`); e.status = res.status; throw e; }
  return res.json();
}

const proxy = (op, url) => `/api/proxy?op=${op}&url=${encodeURIComponent(url)}`;

export function groupGGUF(files) {
  const groups = new Map();
  for (const f of files) {
    const rf = f.rfilename;
    if (!/\.gguf$/i.test(rf)) continue;
    const m = rf.match(SPLIT_RE);
    const key = m ? m[1] : rf;
    if (!groups.has(key)) groups.set(key, { key, name: key.split("/").pop(), files: [], size: 0, quant: null });
    const g = groups.get(key);
    g.files.push(f);
    g.size += f.size || f.lfs?.size || 0;
  }
  for (const g of groups.values()) {
    g.files.sort((a, b) => a.rfilename.localeCompare(b.rfilename));
    const parent = g.key.includes("/") ? g.key.split("/").slice(-2, -1)[0] : "";
    g.quant = gguf.quantFromName(g.key) || (parent ? gguf.quantFromName(parent) : null);
    g.size = g.size || null;
  }
  return [...groups.values()];
}

export function recommend(groups, fits) {
  const ok = groups.filter((g) => fits[g.key].level === "ok");
  for (const q of PREFERRED) { const g = ok.find((x) => (x.quant || "").toUpperCase() === q); if (g) return g.key; }
  if (ok.length) return ok.reduce((a, b) => ((b.size || 0) > (a.size || 0) ? b : a)).key;
  const warn = groups.filter((g) => fits[g.key].level === "warn");
  const pool = warn.length ? warn : groups;
  return pool.length ? pool.reduce((a, b) => ((b.size || 0) < (a.size || 0) ? b : a)).key : null;
}

// ---------------------------------------------------------------- entry points

export async function inspect(text, ctx) {
  const src = parse(text);
  let r;
  if (src.kind === "hf") r = await inspectHF(src, ctx);
  else if (src.kind === "ollama") r = await inspectOllama(src, ctx);
  else if (src.kind === "url") r = await inspectURL(src, ctx);
  else {
    r = base(src.path, "local");
    r.access = { ok: false, status: "local", message: "This website can't open files by path. Drag the .gguf file itself onto the page." };
  }
  r.source = { kind: src.kind, label: label(src), raw: src.raw };
  return r;
}

export async function inspectFile(file) {
  const r = base(file.name, "upload");
  r.source = { kind: "upload", label: file.name, raw: file.name };
  const header = gguf.parseHeader(await file.slice(0, Math.min(file.size, 32 * 1024 * 1024)).arrayBuffer());
  const s = gguf.summarize(header, file.size, file.name);
  summaryFacts(r, s);
  r.facts.push(`file: ${humanSize(file.size)}`);
  if (s.header_truncated) r.notes.push("Only part of the header was read; some details may be missing.");
  if (s.split_count > 1) r.notes.push("This is one part of a split model. Keep all the parts in the same folder and load the first one.");
  r.access = { ok: true, status: "local", message: "It's already on your computer — nothing to download." };
  const kv = gguf.kvCacheBytes(s, 4096);
  r.options.push({
    title: s.quantization || "GGUF", subtitle: file.name, size: file.size, local: true,
    overhead: kv ? kv + 0.3 * GB : null,
    actions: [
      { kind: "command", label: "Run with llama.cpp", command: `llama-server -m "${file.name}"` },
      { kind: "command", label: "Add to Ollama", command: `echo 'FROM ./${file.name}' > Modelfile && ollama create ${file.name.replace(/\.gguf$/i, "").toLowerCase().replace(/[^a-z0-9._-]+/g, "-")} -f Modelfile` },
    ],
  });
  r.headlineName = file.name;
  r.local = true;
  return r;
}

function summaryFacts(r, s) {
  if (s.size_label || s.parameters) r.facts.push(`${s.size_label || paramText(s.parameters)} parameters`);
  if (s.architecture) r.facts.push(`architecture: ${s.architecture}`);
  if (s.quantization) r.facts.push(`quantization: ${s.quantization}`);
  if (s.context_length) r.facts.push(`context: ${s.context_length.toLocaleString()} tokens`);
  if (s.has_chat_template === false) r.facts.push("no chat template (may be a base model)");
}

// ---------------------------------------------------------------- Hugging Face

async function inspectHF(src, ctx) {
  const repo = src.repo;
  const r = base(repo, "hf");
  r.links.push({ label: "Model page", url: `${HUB}/${repo}` });
  const auth = ctx.hfToken ? { Authorization: `Bearer ${ctx.hfToken}` } : {};
  const rev = src.revision === "main" ? "" : `/revision/${encodeURIComponent(src.revision)}`;
  let info;
  try {
    info = await getJSON(`${HUB}/api/models/${repo}${rev}?blobs=true`, auth);
  } catch (e) {
    if (e.status === 401 || e.status === 404) {
      r.access = { ok: false, status: "not-found", message: "Not found on Hugging Face." + (ctx.hfToken ? "" : " If it's private, add your Hugging Face token in Settings.") };
    } else {
      r.access = { ok: false, status: "unreachable", message: `Couldn't reach Hugging Face (${e.message}).` };
    }
    r.verdict = { level: "bad", headline: r.access.message };
    return r;
  }
  const files = (info.siblings || []).filter((f) => f.rfilename);
  const gated = info.gated || false;
  const gg = info.gguf || {}, st = info.safetensors || {}, card = info.cardData || {};
  const params = gg.total || st.total;
  if (params) r.facts.push(`${paramText(params)} parameters`);
  if (gg.architecture) r.facts.push(`architecture: ${gg.architecture}`);
  if (gg.context_length) r.facts.push(`context: ${gg.context_length.toLocaleString()} tokens`);
  if (card.license) r.facts.push(`license: ${card.license}`);
  if (info.downloads != null) r.facts.push(`${info.downloads.toLocaleString()} downloads last month`);
  if (gated) r.facts.push("gated: accept the license on the model page");
  if (info.disabled) {
    r.access = { ok: false, status: "disabled", message: "This repository is disabled on Hugging Face." };
    r.verdict = { level: "bad", headline: r.access.message };
    return r;
  }
  const short = repo.split("/")[1];
  if (ctx.webllmIds?.has(short)) {
    r.webllmId = short;
    r.notes.push("This exact model is available in WebLLM, so it can run right here in your browser.");
  }

  let groups = groupGGUF(files);
  const mmproj = groups.filter((g) => /mmproj/i.test(g.name));
  groups = groups.filter((g) => !/mmproj/i.test(g.name));
  if (src.filename && /\.gguf$/i.test(src.filename)) {
    const m = src.filename.match(SPLIT_RE);
    const key = m ? m[1] : src.filename;
    const only = groups.filter((g) => g.key === key);
    if (only.length) groups = only;
  }
  if (src.quant) {
    const q = groups.filter((g) => (g.quant || "").toUpperCase() === src.quant);
    if (q.length) groups = q; else r.notes.push(`No ${src.quant} file here; showing every GGUF instead.`);
  }
  if (mmproj.length) r.notes.push("Includes a vision projector (mmproj) file; text chat works without it.");

  const probe = groups.length ? groups[0].files[0].rfilename : (files.find((f) => ["config.json", "README.md"].includes(f.rfilename)) || {}).rfilename;
  r.access = await hfAccess(repo, src.revision, probe, gated, info.private, ctx.hfToken);

  if (groups.length) {
    for (const g of [...groups].sort((a, b) => (a.size || 0) - (b.size || 0))) {
      const dl = g.files.map((f) => ({
        url: `${HUB}/${repo}/resolve/${encodeURIComponent(src.revision)}/${f.rfilename.split("/").map(encodeURIComponent).join("/")}?download=true`,
        name: f.rfilename.split("/").pop(), size: f.size,
      }));
      const actions = [];
      if (r.access.ok) {
        actions.push({ kind: "download-link", label: "Download file" + (dl.length > 1 ? "s" : ""), files: dl });
        if (g.quant && g.files.length === 1 && !g.files[0].rfilename.includes("/")) {
          actions.push({ kind: "ollama-pull", label: "Pull into my Ollama", model: `hf.co/${repo}:${g.quant}` });
        }
      }
      r.options.push({ key: g.key, title: g.quant || g.name, subtitle: g.name + (g.files.length > 1 ? ` (${g.files.length} parts)` : ""), size: g.size, quant: g.quant, actions });
    }
    r.pickBest = true;
  } else {
    await hfNoGGUF(r, repo, files, params, auth);
  }
  return r;
}

async function hfAccess(repo, revision, filename, gated, isPrivate, token) {
  const page = `${HUB}/${repo}`;
  if (!filename) return { ok: true, status: "open", message: "Public repository." };
  const url = `${HUB}/${repo}/resolve/${encodeURIComponent(revision)}/${filename.split("/").map(encodeURIComponent).join("/")}`;
  try {
    const res = await fetch(url, { method: "HEAD", redirect: "manual", headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (res.type === "opaqueredirect" || res.ok || res.status === 302) {
      return gated ? { ok: true, status: "gated-granted", message: "Gated, and your token has been granted access." } : { ok: true, status: "open", message: "Download is accessible." };
    }
    if (res.status === 401 || res.status === 403) {
      if (gated) return { ok: false, status: "gated", message: `Gated model: accept the license on the model page first.${token ? "" : " Then add your Hugging Face token in Settings."}`, link: page };
      if (isPrivate) return { ok: false, status: "private", message: "Private repository: add a Hugging Face token that can see it (Settings)." };
      return { ok: false, status: "denied", message: `Hugging Face refused the download (HTTP ${res.status}).` };
    }
    return { ok: false, status: "error", message: `Download check failed (HTTP ${res.status}).` };
  } catch {
    return { ok: false, status: "unreachable", message: "Couldn't reach Hugging Face to check the download." };
  }
}

async function hfNoGGUF(r, repo, files, params, auth) {
  const weights = files.filter((f) => /\.(safetensors|bin|pth|pt)$/.test(f.rfilename) && f.size);
  const st = weights.filter((f) => f.rfilename.endsWith(".safetensors"));
  const total = (st.length ? st : weights).reduce((a, f) => a + f.size, 0);
  if (total) r.facts.push(`full weights: ${humanSize(total)}`);
  r.notes.push("No GGUF files in this repo, so llama.cpp and Ollama can't run it directly. Try a GGUF conversion (below), or chat with it through Hugging Face.");
  if (params) r.estimateQ4 = Math.round(params * 0.6);
  try {
    const found = await getJSON(`${HUB}/api/models?filter=base_model:quantized:${repo}&filter=gguf&sort=downloads&limit=5`, auth);
    for (const m of found) r.suggestions.push({ label: m.id, input: m.id, detail: `${(m.downloads || 0).toLocaleString()} downloads` });
  } catch { /* optional */ }
  try {
    const info = await getJSON(`${HUB}/api/models/${repo}?expand[]=inferenceProviderMapping`, auth);
    let mapping = info.inferenceProviderMapping || {};
    if (Array.isArray(mapping)) mapping = Object.fromEntries(mapping.map((m) => [m.provider, m]));
    const live = Object.entries(mapping).filter(([, v]) => v?.status === "live" && ["conversational", "text-generation", undefined].includes(v?.task)).map(([p]) => p);
    if (live.length) {
      r.options.push({
        title: "Hosted", subtitle: "runs on: " + live.sort().slice(0, 6).join(", "), size: null, remote: true,
        actions: [{ kind: "remote", label: "Chat via Hugging Face", model: repo }],
      });
    }
  } catch { /* optional */ }
}

// ---------------------------------------------------------------- Ollama library

async function inspectOllama(src) {
  const { model: name, tag } = src;
  const ns = name.includes("/") ? name.split("/")[0] : "library";
  const short = name.split("/").pop();
  const r = base(`${name}:${tag}`, "ollama");
  r.links.push({ label: "Model page", url: `https://ollama.com/${ns === "library" ? "library/" + short : name}` });
  const reg = "https://registry.ollama.ai";
  let manifest;
  try {
    manifest = await getJSON(proxy("manifest", `${reg}/v2/${ns}/${short}/manifests/${encodeURIComponent(tag)}`));
  } catch (e) {
    const msg = e.status === 404 ? `${name}:${tag} isn't in the Ollama library.` : `Couldn't reach the Ollama registry (${e.message}).`;
    r.access = { ok: false, status: "not-found", message: msg };
    r.verdict = { level: "bad", headline: msg };
    return r;
  }
  const layers = manifest.layers || [];
  const total = layers.reduce((a, l) => a + (l.size || 0), 0);
  r.access = { ok: true, status: "open", message: "Available from the Ollama library." };
  try {
    const cfg = await getJSON(proxy("blob", `${reg}/v2/${ns}/${short}/blobs/${manifest.config.digest}`));
    if (cfg.model_family) r.facts.push(`family: ${cfg.model_family}`);
    if (cfg.model_type) r.facts.push(`${cfg.model_type} parameters`);
    if (cfg.file_type) r.facts.push(`quantization: ${cfg.file_type}`);
  } catch { /* optional */ }
  r.facts.push(`download: ${humanSize(total)}`);
  r.options.push({
    title: tag, subtitle: `${name}:${tag}`, size: total,
    actions: [
      { kind: "ollama-pull", label: "Pull into my Ollama", model: `${name}:${tag}` },
      { kind: "command", label: "Copy command", command: `ollama pull ${name}:${tag}` },
    ],
  });
  r.pickBest = true;
  return r;
}

// ---------------------------------------------------------------- direct links

async function inspectURL(src) {
  const r = base(src.filename || src.url, "url");
  r.links.push({ label: "Link", url: src.url });
  let head;
  try { head = await getJSON(proxy("head", src.url)); } catch (e) { head = { status: 0 }; }
  if (![200, 206].includes(head.status)) {
    const msg = head.status ? `That link isn't downloadable (HTTP ${head.status}).` : "Couldn't reach that link.";
    r.access = { ok: false, status: "denied", message: msg };
    r.verdict = { level: "bad", headline: msg };
    return r;
  }
  const size = head.content_length || null;
  r.access = { ok: true, status: "open", message: "Download is accessible." + (head.accept_ranges === "bytes" ? " Resumable." : "") };
  let overhead = null, quant = null;
  if (/\.gguf$/i.test(src.filename || "") && head.accept_ranges === "bytes") {
    try {
      const res = await fetch(proxy("range", src.url));
      if (res.ok) {
        const s = gguf.summarize(gguf.parseHeader(await res.arrayBuffer()), size, src.filename);
        summaryFacts(r, s);
        quant = s.quantization;
        const kv = gguf.kvCacheBytes(s, 4096);
        if (kv) overhead = kv + 0.3 * GB;
      }
    } catch { /* optional */ }
  }
  if (size) r.facts.push(`download: ${humanSize(size)}`);
  if (!/\.gguf$/i.test(src.filename || "")) r.notes.push("This doesn't look like a GGUF model file; llama.cpp may not be able to load it.");
  r.options.push({ title: quant || "File", subtitle: src.filename, size, overhead, actions: [{ kind: "download-link", label: "Download file", files: [{ url: src.url, name: src.filename, size }] }] });
  r.pickBest = true;
  return r;
}

// ---------------------------------------------------------------- fit everything for a machine

export function applyFit(r, machine) {
  const fits = {};
  for (const o of r.options) {
    o.fit = o.remote ? { level: "ok", messages: ["Runs on Hugging Face's servers; needs your HF token."], blocked: false } : assessFit(o.size, machine, o.overhead);
    if (o.local) o.fit.blocked = false;
    fits[o.key || o.title] = o.fit;
  }
  const local = r.options.filter((o) => !o.remote);
  if (r.pickBest && local.length) {
    const rec = recommend(local.map((o) => ({ key: o.key || o.title, quant: o.quant, size: o.size })), fits);
    for (const o of r.options) o.recommended = (o.key || o.title) === rec;
  } else if (local.length === 1) local[0].recommended = true;
  if (!r.access.ok) { r.verdict = { level: "bad", headline: r.access.message }; return r; }
  if (r.fixedVerdict) return r;
  if (local.length) {
    const best = local.map((o) => o.fit.level).reduce((a, b) => (LEVELS[b] < LEVELS[a] ? b : a));
    r.verdict = { level: best, headline: headline(best, r.headlineName || r.title, !!r.local) };
  } else if (r.estimateQ4) {
    const f = assessFit(r.estimateQ4, machine);
    r.verdict = { level: f.level, headline: `${r.title} has no GGUF here. A 4-bit version would be ~${humanSize(r.estimateQ4)}${f.level === "ok" ? " and should fit." : " — see below."}` };
    r.estimateNote = `A 4-bit GGUF of this model would be about ${humanSize(r.estimateQ4)}: ${f.messages[f.messages.length - 1]}`;
    const hosted = r.options.find((o) => o.remote);
    if (hosted) hosted.recommended = f.level !== "ok";
  } else if (!r.verdict) {
    r.verdict = { level: "warn", headline: `${r.title} has no GGUF files to run locally.` };
  }
  return r;
}
