/*! MiniTok 1.0.0 - a TikTok-style feed with an algorithm you control. MIT License. https://github.com/vendemiaraul-cyber/python-disconnected/tree/claude/minitok-app/js */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.MiniTok = factory();
})(typeof self !== "undefined" ? self : this, function () {
"use strict";
const VERSION = "1.0.0";
/* ============================================================ algorithm (mirrors minitok/algorithm) */
const FEATURES = {
  interest: "Match with your hashtags, creators, sounds and words",
  popularity: "Total views, log-scaled",
  like_rate: "Likes per view",
  comment_rate: "Comments per view",
  share_rate: "Shares per view",
  freshness: "How new the video is",
  duration_fit: "Closeness to your preferred length",
  novelty: "Creators you haven't seen lately",
  random: "Pure exploration",
};
const FN = Object.keys(FEATURES);
const W = (o) => Object.fromEntries(FN.map((k) => [k, o[k] || 0]));
const PRESETS = {
  for_you: { name: "For You (TikTok-style)", desc: "Interest first, then engagement quality, a bit of freshness and exploration. Learns from what you finish, rewatch or skip.",
    w: W({ interest: 3, like_rate: 1.5, comment_rate: .5, share_rate: 1, popularity: .6, freshness: .8, duration_fit: .4, novelty: .6, random: .3 }), exploration: .10, diversity: .5 },
  popular: { name: "Most Popular", desc: "Biggest videos first: views and likes dominate, your interests barely matter.",
    w: W({ popularity: 3, like_rate: 1, share_rate: .5, interest: .3, random: .1 }), exploration: .02, diversity: .3 },
  fresh: { name: "Fresh / Newest", desc: "Newest uploads first, lightly filtered by quality.",
    w: W({ freshness: 3, like_rate: .5, interest: .5, random: .1 }), exploration: .05, diversity: .3 },
  niche: { name: "Niche / Interests only", desc: "Only what matches your interests; popularity is ignored.",
    w: W({ interest: 4, duration_fit: .3, novelty: .3, random: .1 }), exploration: .03, diversity: .4 },
  discovery: { name: "Discovery / Small creators", desc: "Pushes small and unseen creators; penalises viral videos.",
    w: W({ novelty: 2, random: 1.5, popularity: -1, like_rate: .8, interest: .5 }), exploration: .30, diversity: .8 },
  random: { name: "Random", desc: "No algorithm at all - pure shuffle.", w: W({ random: 1 }), exploration: 1, diversity: 0 },
  custom: { name: "Custom weights", desc: "Set every weight yourself with the sliders.",
    w: W({ interest: 1, like_rate: 1, popularity: 1, freshness: 1, random: .3 }), exploration: .10, diversity: .5 },
  formula: { name: "Custom formula", desc: "Type your own ranking formula.", w: W({}), exploration: .05, diversity: .3 },
  learned: { name: "Learned from my data", desc: "Weights and interests fitted by Find my algorithm.",
    w: W({ interest: 2, like_rate: 1, random: .2 }), exploration: .08, diversity: .5 },
};

function defaultConfig() {
  return { preset: "for_you", weights: { ...PRESETS.for_you.w }, formula: '3*interest + like_rate + 0.5*freshness + 0.2*random',
           exploration: .10, diversity: .5, interests: {}, blocked: [], preferredDuration: 10, learn: true, learnedWeights: null };
}
function applyPreset(cfg, key) {
  const p = PRESETS[key];
  cfg.preset = key;
  cfg.weights = key === "learned" && cfg.learnedWeights ? { ...W(cfg.learnedWeights) } : { ...p.w };
  cfg.exploration = p.exploration;
  cfg.diversity = p.diversity;
}

const HASH = /#(\w+)/gu;
function tokens(v) {
  if (v._tokens) return v._tokens;
  const t = v.hashtags.map((h) => "#" + h);
  if (v.author) t.push("@" + v.author.toLowerCase().replace(/^@/, ""));
  if (v.music) t.push("♪" + v.music.toLowerCase());
  const words = v.description.toLowerCase().replace(HASH, " ").match(/\p{L}{4,}/gu) || [];
  return (v._tokens = [...new Set([...t, ...words])]);
}
const interestScore = (v, interests) => {
  let s = 0; for (const t of tokens(v)) s += interests[t] || 0;
  return Math.tanh(s / 2);
};
function features(v, viewer) {
  const age = v.created ? Math.max(0, (Date.now() / 1000 - v.created) / 3600) : null;
  const rate = (p, w, k) => (w > 0 ? Math.min(1, (p / w) * k) : 0);
  let dfit = .5;
  if (v.duration > 0 && viewer.preferredDuration > 0) dfit = Math.max(0, 1 - Math.abs(v.duration - viewer.preferredDuration) / viewer.preferredDuration);
  let novelty = 1;
  const a = (v.author || "").toLowerCase(), ra = viewer.recentAuthors;
  const last = ra.lastIndexOf(a);
  if (a && last >= 0) novelty = Math.min(1, (ra.length - last) / 10);
  return {
    interest: interestScore(v, viewer.interests),
    popularity: Math.min(1, Math.log10(v.views + 1) / 7),
    like_rate: rate(v.likes, v.views, 8), comment_rate: rate(v.comment_count, v.views, 100), share_rate: rate(v.shares, v.views, 50),
    freshness: age === null ? .5 : Math.exp(-age / 72), duration_fit: dfit, novelty, random: Math.random(),
  };
}
function isBlocked(v, cfg) {
  const toks = new Set(tokens(v)), d = v.description.toLowerCase();
  return cfg.blocked.some((raw) => { const b = raw.trim().toLowerCase(); return b && (toks.has(b) || (!"#@♪".includes(b[0]) && d.includes(b))); });
}
function scoreVideo(v, cfg, viewer, formula) {
  const f = features(v, viewer);
  let s;
  if (cfg.preset === "formula" && formula) s = formula.evaluate(f, v);
  else s = FN.reduce((acc, k) => acc + (cfg.weights[k] || 0) * f[k], 0);
  s -= cfg.diversity * (1 - f.novelty) * 2;
  return { video: v, score: s, features: f, explored: false };
}
function rankAll(cands, cfg, recentAuthors) {
  const viewer = { interests: cfg.interests, preferredDuration: +cfg.preferredDuration || 10, recentAuthors: recentAuthors.map((a) => a.toLowerCase()) };
  const formula = cfg.preset === "formula" ? new Formula(cfg.formula) : null;
  return cands.filter((v) => !isBlocked(v, cfg)).map((v) => scoreVideo(v, cfg, viewer, formula)).sort((a, b) => b.score - a.score);
}

/* ---------- safe formula language (same syntax as the desktop app) ---------- */
class FormulaError extends Error {}
const RAW = ["views", "likes", "comments", "shares", "duration", "age_hours"];
const FUNCS = {
  log: (x) => Math.log(Math.max(x, 1e-9)), log10: (x) => Math.log10(Math.max(x, 1e-9)), sqrt: (x) => Math.sqrt(Math.max(x, 0)),
  exp: (x) => Math.exp(Math.min(x, 50)), tanh: Math.tanh, abs: Math.abs, min: Math.min, max: Math.max,
  clamp: (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x)),
};
class Formula {
  constructor(src) {
    this.src = (src || "").trim();
    if (!this.src) throw new FormulaError("The formula is empty");
    if (this.src.length > 2000) throw new FormulaError("The formula is too long");
    this.toks = this.lex(this.src); this.i = 0;
    this.ast = this.ternary();
    if (this.peek()) throw new FormulaError(`Unexpected '${this.peek().v}'`);
  }
  lex(s) {
    const out = [], re = /\s*(?:(\d+\.?\d*(?:e[+-]?\d+)?|\.\d+)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|([A-Za-z_]\w*)|(\*\*|\/\/|==|!=|<=|>=|[-+*/%<>(),]))/y;
    let m; re.lastIndex = 0;
    while (re.lastIndex < s.length) {
      const start = re.lastIndex;
      if (!(m = re.exec(s))) { if (/^\s*$/.test(s.slice(start))) break; throw new FormulaError(`Syntax error at column ${start + 1}`); }
      if (m[1]) out.push({ t: "num", v: parseFloat(m[1]) });
      else if (m[2]) out.push({ t: "str", v: m[2].slice(1, -1) });
      else if (m[3]) out.push({ t: ["if", "else", "and", "or", "not", "True", "False"].includes(m[3]) ? "kw" : "id", v: m[3] });
      else out.push({ t: "op", v: m[4] });
    }
    return out;
  }
  peek() { return this.toks[this.i]; }
  take(v) { const t = this.peek(); if (t && (v === undefined || t.v === v)) { this.i++; return t; } return null; }
  need(v) { if (!this.take(v)) throw new FormulaError(`Expected '${v}'`); }
  ternary() {
    const body = this.or();
    if (this.take("if")) { const test = this.or(); this.need("else"); const alt = this.ternary(); return { k: "if", test, body, alt }; }
    return body;
  }
  or() { let l = this.and(); while (this.take("or")) l = { k: "or", l, r: this.and() }; return l; }
  and() { let l = this.not(); while (this.take("and")) l = { k: "and", l, r: this.not() }; return l; }
  not() { return this.take("not") ? { k: "not", e: this.not() } : this.cmp(); }
  cmp() {
    const first = this.add(), ops = [], rest = [];
    while (this.peek() && ["<", ">", "<=", ">=", "==", "!="].includes(this.peek().v)) { ops.push(this.take().v); rest.push(this.add()); }
    return ops.length ? { k: "cmp", first, ops, rest } : first;
  }
  add() { let l = this.mul(); while (this.peek() && "+-".includes(this.peek().v) && this.peek().t === "op") l = { k: "bin", op: this.take().v, l, r: this.mul() }; return l; }
  mul() { let l = this.unary(); while (this.peek() && ["*", "/", "%", "//"].includes(this.peek().v)) l = { k: "bin", op: this.take().v, l, r: this.unary() }; return l; }
  unary() { const t = this.peek(); if (t && t.t === "op" && (t.v === "-" || t.v === "+")) { this.i++; return { k: "neg", sign: t.v, e: this.unary() }; } return this.power(); }
  power() { const b = this.atom(); if (this.take("**")) return { k: "bin", op: "**", l: b, r: this.unary() }; return b; }
  atom() {
    const t = this.take();
    if (!t) throw new FormulaError("The formula ends too early");
    if (t.t === "num" || t.t === "str") return { k: "lit", v: t.v };
    if (t.t === "kw" && (t.v === "True" || t.v === "False")) return { k: "lit", v: t.v === "True" };
    if (t.v === "(") { const e = this.ternary(); this.need(")"); return e; }
    if (t.t === "id") {
      if (this.take("(")) {
        if (!(t.v in FUNCS) && t.v !== "has" && t.v !== "contains") throw new FormulaError(`Unknown function '${t.v}'`);
        const args = [];
        if (!this.take(")")) { do args.push(this.ternary()); while (this.take(",")); this.need(")"); }
        return { k: "call", f: t.v, args };
      }
      if (!(t.v in FEATURES) && !RAW.includes(t.v)) throw new FormulaError(`Unknown variable '${t.v}'`);
      return { k: "var", n: t.v };
    }
    throw new FormulaError(`'${t.v}' is not allowed here`);
  }
  evaluate(f, v) {
    const env = { ...f, views: v.views, likes: v.likes, comments: v.comment_count, shares: v.shares, duration: v.duration,
                  age_hours: v.created ? (Date.now() / 1000 - v.created) / 3600 : 0 };
    const toks = new Set(tokens(v)), desc = v.description.toLowerCase();
    const ev = (n) => {
      switch (n.k) {
        case "lit": return n.v;
        case "var": return env[n.n];
        case "neg": return n.sign === "-" ? -ev(n.e) : +ev(n.e);
        case "not": return !ev(n.e);
        case "and": { const l = ev(n.l); return l ? ev(n.r) : l; }
        case "or": { const l = ev(n.l); return l ? l : ev(n.r); }
        case "if": return ev(n.test) ? ev(n.body) : ev(n.alt);
        case "cmp": {
          let l = ev(n.first);
          for (let i = 0; i < n.ops.length; i++) {
            const r = ev(n.rest[i]), op = n.ops[i];
            const ok = op === "<" ? l < r : op === ">" ? l > r : op === "<=" ? l <= r : op === ">=" ? l >= r : op === "==" ? l === r : l !== r;
            if (!ok) return false; l = r;
          }
          return true;
        }
        case "bin": {
          const a = ev(n.l), b = ev(n.r);
          switch (n.op) {
            case "+": return a + b; case "-": return a - b; case "*": return a * b;
            case "/": return b ? a / b : 0; case "//": return b ? Math.floor(a / b) : 0; case "%": return b ? a % b : 0;
            case "**": return Math.pow(a, Math.min(b, 10));
          }
        }
        case "call": {
          const args = n.args.map(ev);
          if (n.f === "has") return toks.has(String(args[0]).toLowerCase()) ? 1 : 0;
          if (n.f === "contains") return desc.includes(String(args[0]).toLowerCase()) ? 1 : 0;
          return FUNCS[n.f](...args);
        }
      }
      throw new FormulaError("Unsupported expression");
    };
    const out = Number(ev(this.ast));
    if (!Number.isFinite(out)) throw new FormulaError("The formula did not produce a number");
    return out;
  }
}

/* ---------- learning (mirrors minitok/events.py + learner.py) ---------- */
function engagement(imp) {
  const d = imp.video.duration, total = imp.loops * d + imp.watched;
  const ratio = d > 0 ? total / d : 0;
  if (imp.loops >= 1 || ratio >= .9) return 1;
  if (total < 2 || (d > 0 && ratio < .2)) return 0;
  let s = d > 0 ? Math.min(1, ratio) : Math.min(1, total / 30);
  if (imp.openedComments) s = Math.min(1, s + .3);
  return s;
}
function updateInterests(interests, imp) {
  const delta = engagement(imp) - .4;
  if (Math.abs(delta) < 1e-6) return;
  for (const t of tokens(imp.video)) {
    const strength = "#@♪".includes(t[0]) ? 1 : .3;
    interests[t] = Math.max(-3, Math.min(3, (interests[t] || 0) + delta * strength));
  }
}
const sigmoid = (z) => (z < -40 ? 0 : z > 40 ? 1 : 1 / (1 + Math.exp(-z)));
function fitLogistic(rows, ys, sw, l2 = .05, epochs = 800, lr = .5) {
  const d = rows[0].length, tw = sw.reduce((a, b) => a + b, 0) || 1;
  const w = new Array(d).fill(0); let b = 0;
  for (let e = 0; e < epochs; e++) {
    const gw = new Array(d).fill(0); let gb = 0;
    rows.forEach((x, i) => {
      const err = (sigmoid(x.reduce((acc, xi, j) => acc + xi * w[j], b)) - ys[i]) * sw[i];
      for (let j = 0; j < d; j++) gw[j] += err * x[j];
      gb += err;
    });
    for (let j = 0; j < d; j++) w[j] -= lr * (gw[j] / tw + l2 * w[j]);
    b -= lr * gb / tw;
  }
  return { w, b };
}
function tokenProfile(examples, minCount = 2) {
  const pos = {}, neg = {}; let np = 0, nn = 0;
  for (const ex of examples) {
    np += ex.label * ex.weight; nn += (1 - ex.label) * ex.weight;
    for (const t of tokens(ex.video)) { pos[t] = (pos[t] || 0) + ex.label * ex.weight; neg[t] = (neg[t] || 0) + (1 - ex.label) * ex.weight; }
  }
  const prof = {};
  for (const t of new Set([...Object.keys(pos), ...Object.keys(neg)])) {
    const p = pos[t] || 0, q = neg[t] || 0;
    if (p + q < minCount) continue;
    let lo = Math.log((p + .5) / (np + 1)) - Math.log((q + .5) / (nn + 1));
    if (!"#@♪".includes(t[0])) lo *= .5;
    prof[t] = Math.max(-3, Math.min(3, lo));
  }
  return prof;
}
const LEARNED = FN.filter((f) => f !== "random");
const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
function learn(history, seeds, exportStats) {
  const recent = [], examples = history.map((imp) => {
    const e = engagement(imp);
    const ex = { video: imp.video, label: e >= .6 ? 1 : 0, weight: .4 + 1.2 * Math.abs(e - .5), seen: [...recent] };
    recent.push((imp.video.author || "").toLowerCase());
    return ex;
  });
  const report = [], positives = examples.filter((e) => e.label).length;
  const liked = examples.filter((e) => e.label && e.video.duration > 0).map((e) => e.video.duration);
  let preferred = liked.length ? median(liked) : null;
  if (!preferred && exportStats && exportStats.median) preferred = exportStats.median;
  const interests = { ...seeds };
  for (const [t, v] of Object.entries(tokenProfile(examples))) interests[t] = Math.max(-3, Math.min(3, (interests[t] || 0) + v));
  if (examples.length < 8 || positives === 0 || positives === examples.length) {
    report.push(`Only ${examples.length} videos watched here (${positives} finished or looped) - not enough to fit weights yet. ` +
      `Watch at least 8 clips, finish some and skip some, then try again.` + (Object.keys(seeds).length ? " Interests from your export were still added." : ""));
    return { weights: null, interests, preferred, exploration: .1, report };
  }
  const idx = examples.map((_, i) => i).sort(() => Math.random() - .5), folds = new Array(examples.length);
  idx.forEach((i, r) => (folds[i] = r % 2));
  const profiles = [0, 1].map((k) => {
    const p = { ...seeds };
    for (const [t, v] of Object.entries(tokenProfile(examples.filter((_, i) => folds[i] !== k)))) p[t] = Math.max(-3, Math.min(3, (p[t] || 0) + v));
    return p;
  });
  const rows = examples.map((ex, i) => {
    const f = features(ex.video, { interests: profiles[folds[i]], preferredDuration: preferred || 10, recentAuthors: ex.seen });
    return LEARNED.map((k) => f[k]);
  });
  const cols = LEARNED.map((_, j) => rows.map((r) => r[j]));
  const means = cols.map((c) => c.reduce((a, b) => a + b, 0) / c.length);
  const stds = cols.map((c, j) => Math.sqrt(c.reduce((a, b) => a + (b - means[j]) ** 2, 0) / c.length) || 1);
  const z = rows.map((r) => r.map((x, j) => (x - means[j]) / stds[j]));
  const ys = examples.map((e) => e.label), { w, b } = fitLogistic(z, ys, examples.map((e) => e.weight));
  const acc = z.filter((r, i) => (sigmoid(r.reduce((a, x, j) => a + x * w[j], b)) >= .5) === (ys[i] >= .5)).length / z.length;
  const raw = w.map((c, j) => c / stds[j]), big = Math.max(...raw.map(Math.abs)) || 1;
  const weights = W({}); LEARNED.forEach((f, j) => (weights[f] = +(raw[j] / big * 3).toFixed(2))); weights.random = .2;
  const off = examples.filter((e) => e.label && interestScore(e.video, interests) <= 0).length;
  const exploration = +Math.min(.3, Math.max(.03, .4 * off / Math.max(1, positives))).toFixed(2);
  report.push(`Fitted on ${examples.length} videos (${positives} finished/looped, ${examples.length - positives} skipped).`);
  report.push(`The model explains ${Math.round(acc * 100)}% of your choices (always guessing the majority: ${Math.round(Math.max(positives, examples.length - positives) / examples.length * 100)}%).`);
  report.push("What drives your feed, strongest first:");
  for (const f of [...LEARNED].sort((a, c) => Math.abs(weights[c]) - Math.abs(weights[a]))) {
    if (Math.abs(weights[f]) < .05) continue;
    report.push(`  ${weights[f] > 0 ? "+" : ""}${weights[f].toFixed(2)}  ${f}: you want ${weights[f] > 0 ? "more" : "LESS"} of this`);
  }
  if (preferred) report.push(`Preferred length: about ${Math.round(preferred)}s.`);
  const top = Object.entries(interests).filter(([, v]) => v > 0).sort((a, c) => c[1] - a[1]).slice(0, 8).map(([t]) => t);
  if (top.length) report.push("Top interests: " + top.join(", "));
  report.push(`Suggested exploration: ${Math.round(exploration * 100)}%.`);
  return { weights, interests, preferred, exploration, report };
}

/* ---------- TikTok data export (mirrors tiktok_export.py, no network lookups) ---------- */
function parseExport(name, text) {
  const data = { watched: [], liked: [], favorited: [], searches: [], following: [], hashtags: [] };
  const get = (o, ...names) => { const n = {}; for (const k in o) n[k.toLowerCase().replace(/[ _]/g, "")] = o[k]; for (const x of names) { const v = n[x.toLowerCase()]; if (v !== undefined) return v; } };
  const date = (v) => { const t = Date.parse(String(v || "").replace(" UTC", "").replace(" ", "T")); return isNaN(t) ? 0 : t / 1000; };
  const kindOf = (path) => {
    const p = path.toLowerCase().split("/").slice(-2).join("/");
    if (p.includes("live")) return null;
    if (p.includes("hashtag")) return "hashtags";
    if (p.includes("favorite video") || p.includes("favoritevideolist")) return "favorited";
    if (p.includes("like")) return "liked";
    if (p.includes("browsing") || p.includes("watch")) return "watched";
    if (p.includes("search")) return "searches";
    if (p.includes("following")) return "following";
    return null;
  };
  const add = (kind, items) => {
    for (const it of items) {
      if (!it || typeof it !== "object") continue;
      const rec = { ts: date(get(it, "Date", "Time")), link: String(get(it, "Link", "VideoLink", "Url") || ""),
                    text: String(get(it, "SearchTerm", "UserName", "Username", "HashtagName") || "") };
      if (rec.link || rec.text) data[kind].push(rec);
    }
  };
  if (name.toLowerCase().endsWith(".json")) {
    const walk = (node, path) => {
      if (Array.isArray(node)) { if (node.length && typeof node[0] === "object") { const k = kindOf(path); if (k) add(k, node); } }
      else if (node && typeof node === "object") for (const k in node) walk(node[k], path + "/" + k);
    };
    walk(JSON.parse(text), "");
  } else {
    const stem = name.toLowerCase();
    const kind = stem.includes("watch") || stem.includes("browsing") ? "watched" : stem.includes("like") ? "liked"
      : stem.includes("favorite video") ? "favorited" : stem.includes("hashtag") ? "hashtags" : stem.includes("search") ? "searches"
      : stem.includes("following") ? "following" : null;
    if (!kind) throw new Error("Unrecognised file - use user_data_tiktok.json or a history .txt file");
    add(kind, text.replace(/\r\n/g, "\n").split(/\n\s*\n/).map((block) => {
      const o = {}; for (const line of block.split("\n")) { const i = line.indexOf(":"); if (i > 0) o[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
      return o;
    }));
  }
  const ordered = data.watched.filter((r) => /\/video\/\d+/.test(r.link)).sort((a, b) => a.ts - b.ts), est = [];
  for (let i = 0; i + 1 < ordered.length; i++) { const gap = ordered[i + 1].ts - ordered[i].ts; if (gap >= 0 && gap <= 600) est.push(Math.min(gap, 300)); }
  const stats = est.length ? { median: median(est), skip: est.filter((s) => s <= 3).length / est.length } : null;
  const seeds = {}, bump = (t, a) => { t = t.trim().toLowerCase(); if (t.length >= 2) seeds[t] = Math.min(3, (seeds[t] || 0) + a); };
  for (const r of data.hashtags) { const n = r.text || r.link.replace(/\/$/, "").split("/").pop(); if (n) bump("#" + n.replace(/^#/, ""), 1.5); }
  for (const r of data.following) if (r.text) bump("@" + r.text.replace(/^@/, ""), 1);
  for (const r of data.searches) for (const w of r.text.toLowerCase().match(/#?\p{L}[\p{L}\d_]{2,}/gu) || []) { bump(w, .4); if (w[0] !== "#") bump("#" + w, .3); }
  return { data, stats, seeds };
}


/* ============================================================ widget: MiniTok.mount(element, options) */
const STARTER_VIDEOS = [
  // public videos checked to play in TikTok's official embed player
  { id: "6718335390845095173", author: "scout2015", comment_count: 5841 },
  { id: "6742501081818877190", author: "patroxofficial", comment_count: 4957 },
  { id: "7039716639834656002", author: "cookierun_dev", comment_count: 15630 },
  { id: "7047596209028074758", author: "hankgreen1", comment_count: 4409 },
  { id: "7059698374567611694", author: "pokemonlife22", comment_count: 2 },
];
const DEFAULT_MESSAGE = "Dear TikTok: please promise a better embed. I want my own UI. 💬 sent from MiniTok";
const PLAYER_PARAMS = "controls=0&progress_bar=0&play_button=0&volume_control=0&fullscreen_button=0&timestamp=0" +
  "&loop=1&autoplay=1&music_info=1&description=1&rel=0&closed_caption=0"; // the player only takes commands once it may autoplay

const ICON = {
  play: `<svg viewBox="0 0 24 24"><path fill="#fff" d="M8 5v14l11-7z"/></svg>`,
  gear: `<svg viewBox="0 0 24 24" fill="#fff"><path d="M19.4 13a7.5 7.5 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7.6 7.6 0 0 0-1.7-1L15 3.3h-4l-.4 2.6a7.6 7.6 0 0 0-1.7 1l-2.5-1-2 3.5L6.6 11a7.5 7.5 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1c.5.4 1.1.8 1.7 1l.4 2.6h4l.4-2.6c.6-.2 1.2-.6 1.7-1l2.5 1 2-3.5L19.4 13zM13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z" transform="translate(-1 0)"/></svg>`,
  comment: `<svg viewBox="0 0 48 48"><path fill="#fff" d="M24 4C12.4 4 3 11.9 3 21.7c0 5.6 3.1 10.6 7.9 13.9L9 44l9-4.6c1.9.4 3.9.6 6 .6 11.6 0 21-7.9 21-17.7S35.6 4 24 4z"/><circle cx="14.5" cy="22" r="3" fill="#000"/><circle cx="24" cy="22" r="3" fill="#000"/><circle cx="33.5" cy="22" r="3" fill="#000"/></svg>`,
  heartSm: `<svg viewBox="0 0 24 24"><path fill="none" stroke="#8a8b91" stroke-width="2" d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/></svg>`,
  spkOn: `<path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z"/>`,
  spkOff: `<path d="M16.5 12A4.5 4.5 0 0 0 14 8v2.2l2.4 2.4.1-.6zM19 12c0 .9-.2 1.8-.5 2.6l1.5 1.5A8.8 8.8 0 0 0 21 12a9 9 0 0 0-7-8.8v2.1a7 7 0 0 1 5 6.7zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.2c-.7.5-1.4.9-2.3 1.2v2.1a9 9 0 0 0 3.7-1.8l2 2 1.3-1.3L4.3 3zM12 4 9.9 6.1 12 8.2V4z"/>`,
};

const CSS = `
:host { all: initial; display: block; position: relative; width: 100%; height: 100%; min-height: 420px; background: #000; color: #fff;
  font: 15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; overflow: hidden;
  --red: #fe2c55; --cyan: #25f4ee; --ink: #161823; --grey: #8a8b91; --panel: #121212; --panel-2: #1c1c1c; --line: #2c2c2c; --bar: 48px; }
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
.root { position: absolute; inset: 0; overflow: hidden; }
.top { position: absolute; top: 0; left: 0; right: 0; z-index: 5; height: var(--bar); display: flex; justify-content: center; align-items: center; gap: 18px; background: #000; }
.top .tab { font-weight: 600; font-size: 16px; color: rgba(255,255,255,.6); }
.top .tab.on { color: #fff; font-weight: 700; position: relative; }
.top .tab.on::after { content: ""; position: absolute; left: 25%; right: 25%; bottom: -6px; height: 2.5px; background: #fff; border-radius: 2px; }
.iconbtn { position: absolute; bottom: 5px; width: 38px; height: 38px; display: grid; place-items: center; border-radius: 50%; }
.iconbtn svg { width: 22px; height: 22px; }
.gear { right: 8px; } .cbtn { right: 48px; } .cbtn svg { width: 24px; height: 24px; }
.sound { left: 8px; background: #1f1f1f; } .sound svg { width: 20px; height: 20px; } .sound.on { background: transparent; }
.feed { position: absolute; top: var(--bar); left: 0; right: 0; bottom: 0; overflow-y: scroll; scroll-snap-type: y mandatory; scrollbar-width: none; overscroll-behavior: contain; }
.feed::-webkit-scrollbar { display: none; }
.slide { position: relative; height: 100%; scroll-snap-align: start; scroll-snap-stop: always; background: #000; }
.slide iframe { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; background: #000; }
.slide .wait { position: absolute; inset: 0; display: grid; place-items: center; color: #666; font-size: 13px; }
.touch { position: absolute; top: 64px; left: 0; right: 76px; bottom: 140px; z-index: 2; }
.plea { position: absolute; z-index: 3; left: 10px; top: 66px; max-width: calc(100% - 100px); pointer-events: none; background: rgba(0,0,0,.6);
  border: 1px solid rgba(255,255,255,.2); border-radius: 10px; padding: 6px 10px; font-size: 12px; line-height: 1.35; }
.paused { position: absolute; inset: 0; z-index: 2; display: none; place-items: center; pointer-events: none; }
.paused svg { width: 72px; height: 72px; opacity: .8; filter: drop-shadow(0 1px 3px rgba(0,0,0,.6)); }
.slide.is-paused .paused { display: grid; }
.toast { position: absolute; left: 50%; top: 45%; transform: translate(-50%, -50%); z-index: 30; background: rgba(0,0,0,.8); padding: 12px 18px;
  border-radius: 10px; font-size: 14px; max-width: 80%; text-align: center; opacity: 0; transition: opacity .2s; pointer-events: none; }
.toast.show { opacity: 1; }
.scrim { position: absolute; inset: 0; z-index: 20; background: rgba(0,0,0,.25); opacity: 0; pointer-events: none; transition: opacity .2s; }
.scrim.show { opacity: 1; pointer-events: auto; }
.sheet { position: absolute; left: 0; right: 0; bottom: 0; z-index: 21; height: 70%; background: #fff; color: var(--ink); border-radius: 14px 14px 0 0;
  transform: translateY(105%); transition: transform .24s cubic-bezier(.2,.8,.2,1); display: flex; flex-direction: column; }
.sheet.show { transform: none; }
.sheet-head { display: flex; align-items: center; padding: 12px 12px 4px; gap: 8px; }
.sheet-head h3 { flex: 1; text-align: center; margin: 0; font-size: 14px; }
.sheet-head .x { width: 32px; height: 32px; font-size: 18px; color: var(--ink); }
.exp { position: relative; }
.exp > button { background: var(--red); color: #fff; font-weight: 600; font-size: 13px; border-radius: 4px; padding: 5px 12px; }
.exp .menu { position: absolute; top: 32px; left: 0; z-index: 2; background: #fff; border: 1px solid #ddd; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.15); display: none; min-width: 150px; }
.exp .menu.show { display: block; }
.exp .menu button { display: block; width: 100%; text-align: left; padding: 10px 14px; color: var(--ink); font-size: 14px; }
.exp .menu button:hover { background: #f4f4f5; }
.note { text-align: center; font-size: 11px; color: var(--grey); padding: 0 16px 6px; }
.clist { flex: 1; overflow-y: auto; padding: 4px 0 12px; overscroll-behavior: contain; }
.c { display: flex; gap: 10px; padding: 9px 14px; }
.c .av { width: 32px; height: 32px; border-radius: 50%; flex: none; display: grid; place-items: center; font-weight: 700; font-size: 13px; color: #fff; }
.c .body { flex: 1; min-width: 0; }
.c .who { color: var(--grey); font-size: 12px; font-weight: 600; }
.c .txt { font-size: 14px; margin: 2px 0; overflow-wrap: anywhere; user-select: text; }
.c .meta { color: var(--grey); font-size: 11px; }
.c .lk { color: var(--grey); font-size: 11px; text-align: center; flex: none; width: 34px; }
.c .lk svg { width: 16px; height: 16px; display: block; margin: 0 auto 2px; }
.empty { text-align: center; color: var(--grey); padding: 40px 20px; font-size: 13px; }
.settings { position: absolute; inset: 0; z-index: 25; background: var(--panel); color: #eee; transform: translateX(100%); transition: transform .25s cubic-bezier(.2,.8,.2,1); display: flex; flex-direction: column; }
.settings.show { transform: none; }
.s-head { display: flex; align-items: center; gap: 8px; padding: 10px 10px 8px; border-bottom: 1px solid var(--line); }
.s-head h2 { flex: 1; margin: 0; font-size: 16px; text-align: center; }
.s-head button { width: 64px; height: 32px; font-weight: 600; }
.s-head .done { color: var(--red); text-align: right; padding-right: 6px; }
.s-tabs { display: flex; gap: 4px; padding: 8px 10px 0; overflow-x: auto; scrollbar-width: none; border-bottom: 1px solid var(--line); }
.s-tabs::-webkit-scrollbar { display: none; }
.s-tabs button { padding: 8px 10px; font-size: 13px; color: #aaa; white-space: nowrap; border-bottom: 2px solid transparent; }
.s-tabs button.on { color: #fff; border-color: #fff; font-weight: 600; }
.s-body { flex: 1; overflow-y: auto; padding: 14px 14px 30px; }
.pane { display: none; } .pane.on { display: block; }
.s-body h4 { margin: 16px 0 6px; font-size: 13px; color: #fff; text-transform: uppercase; letter-spacing: .4px; }
.s-body h4:first-child { margin-top: 0; }
.hint { color: #999; font-size: 12px; margin: 4px 0 10px; }
code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
select, input[type=text], input[type=number], textarea { width: 100%; background: var(--panel-2); color: #f2f2f2; border: 1px solid #333; border-radius: 8px; padding: 9px 10px; font: inherit; font-size: 14px; }
textarea { resize: vertical; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 13px; }
textarea.plain { font-family: inherit; font-size: 14px; }
.row { display: grid; grid-template-columns: 104px 1fr 44px; gap: 8px; align-items: center; margin: 6px 0; font-size: 13px; }
.row output { text-align: right; color: #ccc; font-variant-numeric: tabular-nums; }
input[type=range] { width: 100%; accent-color: var(--cyan); }
.check { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; margin: 10px 0; }
.check input { accent-color: var(--red); margin-top: 2px; }
.sbtn { display: inline-block; background: #2b2b2b; border: 1px solid #3a3a3a; border-radius: 8px; padding: 8px 12px; font-size: 13px; margin: 4px 4px 4px 0; }
.sbtn.primary { background: var(--red); border-color: var(--red); color: #fff; font-weight: 700; }
.sbtn:disabled { opacity: .45; cursor: default; }
.msg { font-size: 13px; margin: 6px 0; min-height: 1em; }
.msg.err { color: #ff6b81; } .msg.ok { color: var(--cyan); }
.report { white-space: pre-wrap; background: var(--panel-2); border: 1px solid #333; border-radius: 8px; padding: 10px; font-size: 12.5px; line-height: 1.5; min-height: 60px; color: #ddd; }
.tokens { display: grid; gap: 6px; }
.tok { display: grid; grid-template-columns: 1fr 70px 30px; gap: 6px; }
.tok input { padding: 7px 8px; }
.tok button, .vrow button { color: #999; font-size: 18px; }
.vrow { display: grid; grid-template-columns: 1fr 30px; gap: 6px; align-items: center; padding: 6px 0; border-bottom: 1px solid #222; font-size: 13px; }
.vrow span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; } .vrow small { color: #888; }
.addrow { display: grid; grid-template-columns: 1fr auto; gap: 6px; }
.rank { font-size: 12.5px; color: #ccc; }
.rank div { padding: 5px 0; border-bottom: 1px solid #222; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rank b { color: var(--cyan); font-variant-numeric: tabular-nums; display: inline-block; width: 52px; }
table.why { width: 100%; border-collapse: collapse; font-size: 12.5px; }
table.why td { padding: 4px 2px; border-bottom: 1px solid #222; font-variant-numeric: tabular-nums; }
table.why td:not(:first-child) { text-align: right; }
.flag { background: rgba(37,244,238,.1); border: 1px solid rgba(37,244,238,.3); color: #bff; border-radius: 8px; padding: 8px 10px; font-size: 12px; margin-bottom: 12px; }
`;

const TEMPLATE = `
<div class="root">
  <div class="feed" part="feed"></div>
  <div class="top">
    <button class="iconbtn sound" data-id="sound" aria-label="Sound" title="Sound (M)"><svg viewBox="0 0 24 24" fill="#fff" data-id="soundIcon"></svg></button>
    <span class="tab">Following</span><span class="tab on">For You</span>
    <button class="iconbtn cbtn" data-id="cbtn" aria-label="Comments" title="Comments (C)" hidden>${ICON.comment}</button>
    <button class="iconbtn gear" data-id="gear" aria-label="Algorithm and settings" title="Settings (S)">${ICON.gear}</button>
  </div>
  <div class="toast" data-id="toast"></div>
  <div class="scrim" data-id="scrim"></div>
  <section class="sheet" data-id="sheet" aria-label="Comments">
    <div class="sheet-head">
      <div class="exp"><button data-id="expBtn">Export</button><div class="menu" data-id="expMenu">
        <button data-fmt="json">JSON</button><button data-fmt="csv">CSV (Excel)</button><button data-fmt="txt">Plain text</button></div></div>
      <h3 data-id="cTitle">Comments</h3><button class="x" data-id="cClose" aria-label="Close">✕</button>
    </div>
    <div class="note">Read-only · up to 30 · deleted when you swipe to the next video</div>
    <div class="clist" data-id="clist"></div>
  </section>
  <section class="settings" data-id="settings" aria-label="Settings">
    <div class="s-head"><button data-id="sCancel">Cancel</button><h2>For You algorithm</h2><button class="done" data-id="sSave">Save</button></div>
    <div class="s-tabs" data-id="sTabs">
      <button data-tab="algo" class="on">Algorithm</button><button data-tab="videos">Videos &amp; message</button>
      <button data-tab="interests">Interests</button><button data-tab="find">Find my algorithm</button><button data-tab="why">Why this video?</button>
    </div>
    <div class="s-body">
      <div class="pane on" data-pane="algo">
        <div class="flag">Real TikTok videos through TikTok's official player. MiniTok's algorithm decides the order.</div>
        <h4>Type of algorithm</h4><select data-id="preset"></select><p class="hint" data-id="presetDesc"></p>
        <div data-id="weightsBox"><h4>Weights</h4><p class="hint">How much each signal counts. Moving a slider switches to Custom weights.</p><div data-id="sliders"></div></div>
        <div data-id="formulaBox"><h4>Your formula</h4><textarea data-id="formula" rows="3" spellcheck="false"></textarea>
          <button class="sbtn" data-id="checkFormula">Check formula</button><div class="msg" data-id="formulaMsg"></div>
          <p class="hint">Variables: interest, popularity, like_rate, comment_rate, share_rate, freshness, duration_fit, novelty, random, views, likes,
            comments, shares, duration, age_hours. Functions: log, log10, sqrt, exp, tanh, abs, min, max, clamp, has("#tag"), contains("word").
            Example: <code>3*interest + (2 if has("@hankgreen1") else 0)</code></p></div>
        <h4>Behaviour</h4>
        <div class="row"><span>Exploration</span><input type="range" data-id="exploration" min="0" max="100"><output data-id="explorationOut"></output></div>
        <div class="row"><span>Creator variety</span><input type="range" data-id="diversity" min="0" max="100"><output data-id="diversityOut"></output></div>
        <div class="row"><span>Preferred length</span><input type="number" data-id="prefLen" min="3" max="900"><output>s</output></div>
        <label class="check"><input type="checkbox" data-id="learn"> Learn my interests while I watch (finish/loop = more, skip = less)</label>
        <h4>Preview</h4><button class="sbtn" data-id="preview">Rank the videos with these settings</button><div class="rank" data-id="rank"></div>
      </div>
      <div class="pane" data-pane="videos">
        <h4>Message to TikTok</h4>
        <label class="check"><input type="checkbox" data-id="pleaOn"> Show this message on every video</label>
        <textarea data-id="pleaText" class="plain" rows="3" maxlength="200"></textarea>
        <p class="hint">Shown by MiniTok on top of the player, away from TikTok's buttons and creator name. It doesn't change the video.</p>
        <h4>Add a TikTok video</h4>
        <div class="addrow"><input type="text" data-id="addUrl" placeholder="https://www.tiktok.com/@user/video/123…" inputmode="url"><button class="sbtn primary" data-id="addBtn">Add</button></div>
        <div class="msg" data-id="addMsg"></div>
        <p class="hint">Paste the full link from TikTok's Share › Copy link. TikTok has no official feed for apps, so the algorithm ranks the videos listed here.
          Videos whose creators turned off embedding are skipped automatically.</p>
        <h4>Your videos</h4><div data-id="vlist"></div>
      </div>
      <div class="pane" data-pane="interests">
        <h4>Interest profile</h4><p class="hint">#hashtag, @creator, ♪sound or a word, weight −3 (never) to +3 (love it). Updates by itself as you watch.</p>
        <div class="tokens" data-id="tokens"></div><button class="sbtn" data-id="addTok">+ Add</button><button class="sbtn" data-id="resetTok">Reset all</button>
        <h4>Never show</h4><p class="hint">One per line: a word, #hashtag or @creator.</p><textarea data-id="blocked" rows="4"></textarea>
      </div>
      <div class="pane" data-pane="find">
        <h4>Find my algorithm</h4>
        <p class="hint">Fits the model that best explains what you finish, rewatch and skip. Everything stays in your browser.
          TikTok's own recommender is private, so this recovers your personal ranking, not TikTok's code.</p>
        <h4>1 · What you watched here</h4><p class="msg" data-id="histInfo"></p>
        <h4>2 · Your TikTok data export (optional)</h4>
        <p class="hint">In TikTok: Settings › Account › Download your data (JSON). Unzip it, then pick <code>user_data_tiktok.json</code> (or a .txt file).</p>
        <input type="file" data-id="exportFile" accept=".json,.txt" style="font-size:13px;color:#ccc"><div class="msg" data-id="exportInfo"></div>
        <button class="sbtn primary" data-id="fit">Find my algorithm</button><button class="sbtn" data-id="clearHist">Delete history</button>
        <div class="report" data-id="report">Results appear here.</div>
        <button class="sbtn primary" data-id="applyFit" disabled>Use this as my algorithm</button>
      </div>
      <div class="pane" data-pane="why"><h4>Why am I seeing this?</h4><div data-id="why"></div></div>
    </div>
  </section>
</div>`;

const fmtCount = (n) => n < 1e4 ? String(n) : n < 1e6 ? n.toLocaleString("de-DE") : (n / 1e6).toFixed(1).replace(".0", "").replace(".", ",") + "M";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** Turn a TikTok link, an id, or {id, author} into the library's video shape (or null). */
function toVideo(input) {
  let id = "", author = "", extra = {};
  if (input && typeof input === "object") { id = String(input.id || ""); author = input.author || ""; extra = input; if (!id && input.url) return toVideo(input.url); }
  else {
    const text = String(input || "").trim();
    const m = text.match(/tiktok\.com\/@([\w.-]+)\/(?:video|photo)\/(\d{8,25})/i) || text.match(/tiktok\.com\/(?:embed\/v2|player\/v1|share\/video)\/(\d{8,25})/i);
    if (m) { if (m.length === 3) { author = m[1]; id = m[2]; } else id = m[1]; }
    else if (/^\d{8,25}$/.test(text)) id = text;
  }
  if (!/^\d{8,25}$/.test(id)) return null;
  const description = extra.description || "";
  const hashtags = (extra.hashtags && extra.hashtags.length ? extra.hashtags : (description.match(/#(\w+)/gu) || []).map((h) => h.slice(1)))
    .map((h) => String(h).toLowerCase().replace(/^#/, ""));
  return { id, url: `https://www.tiktok.com/@${author || "_"}/video/${id}`, author, description,
           hashtags, music: extra.music || "", duration: extra.duration || 0, views: extra.views || 0,
           likes: extra.likes || 0, comment_count: extra.comment_count || 0, shares: extra.shares || 0, created: extra.created || 0 };
}

/** Comments as JSON / CSV / plain text, like the desktop app's export. */
function exportComments(video, comments, fmt = "json") {
  const date = (t) => (t ? new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") : "");
  if (fmt === "json") return JSON.stringify({ video: { id: video.id, author: video.author, url: video.url, description: video.description }, comments }, null, 2);
  if (fmt === "csv") {
    const q = (s) => `"${String(s ?? "").replace(/"/g, '""')}"`;
    return "﻿" + ["author,text,likes,replies,date,comment_id,video_url",
      ...comments.map((c) => [q(c.author), q(c.text), c.likes || 0, c.reply_count || 0, q(date(c.created)), q(c.id), q(video.url)].join(","))].join("\n");
  }
  if (fmt === "txt") return [`Comments on ${video.url}`, `@${video.author}: ${video.description}`, "",
    ...comments.flatMap((c) => [`@${c.author} (${c.likes || 0} likes${c.created ? ", " + date(c.created) : ""})`, "  " + c.text, ""])].join("\n");
  throw new Error("Unknown export format: " + fmt);
}

/** A comments provider backed by a running `minitok serve` (the Python app's small HTTP API). */
function commentsFrom(baseUrl) {
  const base = String(baseUrl).replace(/\/+$/, "");
  return async (video) => {
    const r = await fetch(`${base}/comments?id=${encodeURIComponent(video.id)}&limit=30`);
    if (!r.ok) throw new Error(`Comments server answered ${r.status}`);
    const d = await r.json();
    return d.comments || [];
  };
}

function mount(target, options = {}) {
  const host = typeof target === "string" ? document.querySelector(target) : target;
  if (!host) throw new Error("MiniTok.mount: element not found");
  const opt = Object.assign({ videos: STARTER_VIDEOS, storage: "minitok", settings: true, message: {}, algorithm: {}, oembed: true }, options);
  const shadow = host.shadowRoot || host.attachShadow({ mode: "open" });
  shadow.innerHTML = `<style>${CSS}</style>${TEMPLATE}`;
  const $ = (id) => shadow.querySelector(`[data-id="${id}"]`);
  const $$ = (sel) => [...shadow.querySelectorAll(sel)];
  const feedEl = shadow.querySelector(".feed");
  const listeners = [];
  const on = (el, ev, fn, o) => { el.addEventListener(ev, fn, o); listeners.push(() => el.removeEventListener(ev, fn, o)); };

  const ns = opt.storage;
  const store = {
    get(k, d) { if (!ns) return d; try { const v = localStorage.getItem(`${ns}.${k}`); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { if (!ns) return; try { localStorage.setItem(`${ns}.${k}`, JSON.stringify(v)); } catch {} },
  };
  // the host app's options are defaults; what the viewer saved in ⚙ wins
  let cfg = Object.assign(makeConfig(opt.algorithm), store.get("config", {}));
  let ui = Object.assign({ pleaOn: opt.message.show !== false, pleaText: opt.message.text || DEFAULT_MESSAGE }, store.get("ui", {}));
  let history = store.get("history", []);
  let extra = store.get("videos", []);
  let broken = new Set(store.get("broken", []));
  const BASE = (opt.videos || []).map(toVideo).filter(Boolean);
  let VIDEOS = [];
  const shown = [];
  let current = -1, stats = null, muted = true, loaded = null, destroyed = false;
  const recorded = new Set();
  const emit = (name, ...args) => { try { opt[name] && opt[name](...args); } catch (e) { console.error(e); } };
  function toast(t, ms = 1800) { const el = $("toast"); el.textContent = t; el.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove("show"), ms); }

  function rebuildVideos() {
    const seen = new Set(), list = [];
    for (const v of [...BASE, ...extra.map(toVideo).filter(Boolean)]) {
      if (seen.has(v.id) || broken.has(v.id)) continue;
      seen.add(v.id);
      list.push(VIDEOS.find((x) => x.id === v.id) || v);
    }
    VIDEOS = list;
  }
  async function enrich(v) { // TikTok's official oEmbed allows browser calls; fills caption + creator when it has them
    if (!opt.oembed) return;
    try {
      const r = await fetch("https://www.tiktok.com/oembed?url=" + encodeURIComponent(v.url));
      if (!r.ok) return;
      const d = await r.json();
      if (d.author_unique_id) v.author = d.author_unique_id;
      if (d.title) { v.description = d.title; v.hashtags = (d.title.match(/#(\w+)/gu) || []).map((h) => h.slice(1).toLowerCase()); }
      delete v._tokens;
    } catch {}
  }
  const recentAuthors = () => shown.slice(0, current + 1).map((s) => s.video.author).slice(-30);
  function pickNext() {
    if (!VIDEOS.length) return null;
    const seen = new Set(shown.map((s) => s.video.id));
    let cands = VIDEOS.filter((v) => !seen.has(v.id));
    if (!cands.length) {
      const keepOut = Math.max(1, Math.min(2, VIDEOS.length - 1)), lastFew = new Set(shown.slice(-keepOut).map((s) => s.video.id));
      cands = VIDEOS.filter((v) => !lastFew.has(v.id));
      if (!cands.length) cands = VIDEOS.slice();
    }
    let ranked;
    try { ranked = rankAll(cands, cfg, recentAuthors()); }
    catch (e) { toast("Formula error - using random order"); ranked = rankAll(cands, { ...cfg, preset: "random", weights: PRESETS.random.w }, []); }
    if (!ranked.length) return null;
    if (ranked.length > 1 && Math.random() < cfg.exploration) { const r = ranked[Math.floor(Math.random() * ranked.length)]; r.explored = true; return r; }
    return ranked[0];
  }

  /* ---------- official TikTok player ---------- */
  const send = (frame, type, value) => frame && frame.contentWindow && frame.contentWindow.postMessage({ type, value, "x-tiktok-player": true }, "*");
  const slideEl = (i) => feedEl.children[i];
  const frameOf = (i) => slideEl(i) && slideEl(i).querySelector("iframe");
  function addSlide(item) {
    const el = document.createElement("div");
    el.className = "slide";
    el.innerHTML = `<div class="wait">Loading TikTok…</div><div class="touch"></div><div class="paused">${ICON.play}</div><div class="plea"${ui.pleaOn ? "" : " hidden"}>${esc(ui.pleaText)}</div>`;
    el.querySelector(".touch").addEventListener("click", togglePause);
    feedEl.appendChild(el);
    shown.push(item);
  }
  function mountPlayer(i) {
    const el = slideEl(i);
    if (!el || el.querySelector("iframe")) return;
    const f = document.createElement("iframe");
    f.src = `https://www.tiktok.com/player/v1/${shown[i].video.id}?${PLAYER_PARAMS}`;
    f.allow = "autoplay; fullscreen; encrypted-media; picture-in-picture";
    f.title = "TikTok video by @" + shown[i].video.author;
    f.dataset.index = i;
    f.addEventListener("load", () => { const w = el.querySelector(".wait"); if (w) w.textContent = ""; });
    el.prepend(f);
  }
  const unmountPlayer = (i) => { const f = frameOf(i); if (f) f.remove(); };
  on(window, "message", (e) => {
    const d = e.data;
    if (!d || !d["x-tiktok-player"]) return;
    const f = $$(".slide iframe").find((x) => x.contentWindow === e.source);
    if (!f) return; // another MiniTok instance's player
    const i = +f.dataset.index, isCurrent = i === current, v = shown[i] && shown[i].video;
    const wantPlay = isCurrent && !slideEl(i).classList.contains("is-paused") && !$("settings").classList.contains("show");
    if (!f.dataset.ready) {
      f.dataset.ready = "1";
      const w = f.parentElement.querySelector(".wait"); if (w) w.remove();
      send(f, muted ? "mute" : "unMute");
      send(f, wantPlay ? "play" : "pause");
    }
    if (d.type === "onStateChange" && d.value === 1 && !wantPlay) send(f, "pause");
    else if (d.type === "onCurrentTime" && isCurrent && stats) {
      const t = d.value.currentTime, dur = d.value.duration;
      if (v && dur && !v.duration) v.duration = dur;
      if (t + 1 < stats.lastT) stats.loops++;
      stats.lastT = t; stats.watched = t;
    } else if (d.type === "onPlayerError" && v) {
      if (d.value && d.value.errorCode === 1001) { broken.add(v.id); store.set("broken", [...broken]); VIDEOS = VIDEOS.filter((x) => x.id !== v.id); }
      emit("onError", { video: v, code: d.value && d.value.errorCode, type: d.value && d.value.errorType });
      if (isCurrent) { toast("TikTok can't play this one here - skipping"); setTimeout(() => go(1), 1200); }
    }
  });

  function ensureAhead() { while (shown.length < current + 3) { const n = pickNext(); if (!n) break; addSlide(n); } }
  function leave(i) {
    const item = shown[i]; if (!item) return;
    send(frameOf(i), "pause");
    slideEl(i).classList.remove("is-paused");
    loaded = null; closeSheet(); // comments are only kept while the video is on screen
    if (stats && !recorded.has(i)) {
      recorded.add(i);
      const { _tokens, ...video } = item.video;
      const imp = { video, watched: stats.watched, loops: stats.loops, openedComments: !!stats.openedComments };
      history.push(imp); history = history.slice(-400); store.set("history", history);
      if (cfg.learn && cfg.preset !== "random" && cfg.preset !== "formula") { updateInterests(cfg.interests, imp); store.set("config", cfg); }
      emit("onWatch", imp);
    }
  }
  function arrive(i) {
    current = i; stats = { loops: 0, watched: 0, lastT: 0, openedComments: false };
    ensureAhead();
    for (let j = 0; j < shown.length; j++) (Math.abs(j - i) <= 1 ? mountPlayer(j) : unmountPlayer(j));
    const f = frameOf(i);
    send(f, muted ? "mute" : "unMute"); send(f, "seekTo", 0); send(f, "play");
    emit("onChange", shown[i].video, { index: i, score: shown[i].score, features: shown[i].features, explored: shown[i].explored });
  }
  let settleTimer = null;
  on(feedEl, "scroll", () => {
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => {
      const i = Math.round(feedEl.scrollTop / feedEl.clientHeight);
      if (i !== current && shown[i]) { leave(current); arrive(i); }
    }, 90);
  }, { passive: true });
  function go(d) { const i = current + d; if (i < 0 || !shown[i]) return; feedEl.scrollTo({ top: i * feedEl.clientHeight, behavior: "smooth" }); }
  function togglePause() {
    const el = slideEl(current); if (!el) return;
    if (muted) { setMuted(false); toast("Sound on", 900); return; }
    const paused = el.classList.toggle("is-paused");
    send(frameOf(current), paused ? "pause" : "play");
  }
  function setPaused(p) { const el = slideEl(current); if (!el) return; el.classList.toggle("is-paused", p); send(frameOf(current), p ? "pause" : "play"); }
  function setMuted(m) {
    muted = m;
    for (const f of $$(".slide iframe")) send(f, m ? "mute" : "unMute");
    $("soundIcon").innerHTML = m ? ICON.spkOff : ICON.spkOn;
    $("sound").classList.toggle("on", !m);
  }
  on($("sound"), "click", () => setMuted(!muted));
  const applyPlea = () => { for (const p of $$(".plea")) { p.textContent = ui.pleaText; p.hidden = !ui.pleaOn; } };

  /* ---------- comments (only when a comments provider is given) ---------- */
  const COLORS = ["#fe2c55", "#25a4ee", "#7c4dff", "#00b894", "#ff9f43", "#e84393", "#0984e3", "#6c5ce7"];
  const ago = (ts) => { if (!ts) return ""; const d = Date.now() / 1000 - ts;
    for (const [s, u] of [[31536000, "y"], [604800, "w"], [86400, "d"], [3600, "h"], [60, "m"]]) if (d >= s) return Math.floor(d / s) + u + " ago"; return "just now"; };
  if (typeof opt.comments === "function") $("cbtn").hidden = false;
  function renderComments(v, cs) {
    $("cTitle").textContent = `${cs.length}${v.comment_count > cs.length ? " of " + fmtCount(v.comment_count) : ""} comments`;
    $("clist").innerHTML = cs.length ? cs.map((c) => `<div class="c">
      <div class="av" style="background:${COLORS[(String(c.author).charCodeAt(0) + String(c.author).length) % COLORS.length]}">${esc(String(c.author)[0] || "?").toUpperCase()}</div>
      <div class="body"><div class="who">${esc(c.author)}</div><div class="txt">${esc(c.text)}</div><div class="meta">${ago(c.created)}</div></div>
      <div class="lk">${ICON.heartSm}${fmtCount(c.likes || 0)}</div></div>`).join("") : `<div class="empty">No comments on this video.</div>`;
  }
  async function openComments() {
    const item = shown[current]; if (!item || typeof opt.comments !== "function") return;
    stats.openedComments = true;
    $("scrim").classList.add("show"); $("sheet").classList.add("show");
    if (loaded) { renderComments(item.video, loaded); return; }
    $("cTitle").textContent = "Comments"; $("clist").innerHTML = `<div class="empty">Loading comments…</div>`;
    const idx = current;
    try {
      const cs = (await opt.comments(item.video)).slice(0, 30);
      if (idx !== current) return;
      loaded = cs; renderComments(item.video, cs);
    } catch (e) { if (idx === current) $("clist").innerHTML = `<div class="empty">Couldn't load comments.<br>${esc(e.message || e)}</div>`; }
  }
  function closeSheet() { $("scrim").classList.remove("show"); $("sheet").classList.remove("show"); $("expMenu").classList.remove("show"); }
  on($("cbtn"), "click", openComments);
  on($("cClose"), "click", closeSheet);
  on($("scrim"), "click", closeSheet);
  on($("expBtn"), "click", () => $("expMenu").classList.toggle("show"));
  on($("expMenu"), "click", (e) => {
    const fmt = e.target.dataset.fmt; if (!fmt || !loaded) return;
    const v = shown[current].video, type = { json: "application/json", csv: "text/csv", txt: "text/plain" }[fmt];
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([exportComments(v, loaded, fmt)], { type: type + ";charset=utf-8" }));
    a.download = `comments_${v.author || "video"}_${v.id}.${fmt}`;
    shadow.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    $("expMenu").classList.remove("show"); toast(`Exported ${loaded.length} comments`);
  });

  /* ---------- settings ---------- */
  let draft = null, draftUi = null, draftExtra = null, fitResult = null, exportSeeds = {}, exportStats = null;
  if (!opt.settings) $("gear").hidden = true;
  const sel = $("preset");
  for (const [k, p] of Object.entries(PRESETS)) sel.add(new Option(p.name, k));
  $("sliders").innerHTML = FN.map((f) => `<div class="row" title="${esc(FEATURES[f])}"><span>${f.replace("_", " ")}</span>
    <input type="range" min="-50" max="50" data-w="${f}"><output data-o="${f}"></output></div>`).join("");
  const W$ = (f) => shadow.querySelector(`[data-w="${f}"]`), O$ = (f) => shadow.querySelector(`[data-o="${f}"]`);
  function openSettings(tab = "algo") {
    draft = JSON.parse(JSON.stringify(cfg)); draftUi = { ...ui }; draftExtra = extra.slice();
    send(frameOf(current), "pause");
    fillSettings(); selectTab(tab); renderWhy();
    $("histInfo").textContent = `${history.length} videos watched here so far.`;
    $("settings").classList.add("show");
  }
  function closeSettings(save) {
    if (save) {
      readSettings();
      if (draft.preset === "formula") { try { const v = VIDEOS[0] || toVideo("1000000000"); new Formula(draft.formula).evaluate(features(v, { interests: {}, preferredDuration: 10, recentAuthors: [] }), v); }
        catch (e) { selectTab("algo"); $("formulaMsg").className = "msg err"; $("formulaMsg").textContent = "❌ " + e.message; return; } }
      cfg = draft; store.set("config", cfg);
      ui = draftUi; store.set("ui", ui); applyPlea();
      const added = draftExtra.filter((x) => !extra.includes(x));
      extra = draftExtra; store.set("videos", extra);
      rebuildVideos(); added.forEach((x) => { const t = toVideo(x); const v = t && VIDEOS.find((y) => y.id === t.id); if (v) enrich(v); });
      while (shown.length > current + 1) { shown.pop(); feedEl.lastElementChild.remove(); }
      ensureAhead(); mountPlayer(current + 1);
      if (current < 0 && shown.length) arrive(0);
      emit("onSettings", JSON.parse(JSON.stringify(cfg)));
    }
    $("settings").classList.remove("show");
    const el = slideEl(current); if (el && !el.classList.contains("is-paused")) send(frameOf(current), "play");
  }
  function selectTab(t) {
    $$(".s-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
    $$(".pane").forEach((p) => p.classList.toggle("on", p.dataset.pane === t));
  }
  on($("sTabs"), "click", (e) => { const b = e.target.closest("button"); if (b) selectTab(b.dataset.tab); });
  on($("gear"), "click", () => openSettings());
  on($("sCancel"), "click", () => closeSettings(false));
  on($("sSave"), "click", () => closeSettings(true));
  function fillSettings() {
    sel.value = draft.preset; $("presetDesc").textContent = PRESETS[draft.preset].desc;
    for (const f of FN) { const v = draft.weights[f] || 0; W$(f).value = Math.round(v * 10); O$(f).textContent = (v > 0 ? "+" : "") + v.toFixed(1); }
    $("formula").value = draft.formula; $("formulaMsg").textContent = "";
    $("exploration").value = Math.round(draft.exploration * 100); $("explorationOut").textContent = Math.round(draft.exploration * 100) + "%";
    $("diversity").value = Math.round(draft.diversity * 100); $("diversityOut").textContent = Math.round(draft.diversity * 100) + "%";
    $("prefLen").value = Math.round(draft.preferredDuration); $("learn").checked = draft.learn;
    $("formulaBox").style.display = draft.preset === "formula" ? "" : "none";
    $("weightsBox").style.display = draft.preset === "formula" ? "none" : "";
    $("pleaOn").checked = draftUi.pleaOn; $("pleaText").value = draftUi.pleaText; $("addMsg").textContent = "";
    renderVideoList(); renderTokens(); $("blocked").value = draft.blocked.join("\n"); $("rank").innerHTML = "";
  }
  function readSettings() {
    draft.preset = sel.value;
    for (const f of FN) draft.weights[f] = W$(f).value / 10;
    draft.formula = $("formula").value.trim();
    draft.exploration = $("exploration").value / 100; draft.diversity = $("diversity").value / 100;
    draft.preferredDuration = Math.max(3, +$("prefLen").value || 10); draft.learn = $("learn").checked;
    draft.interests = readTokens();
    draft.blocked = $("blocked").value.split("\n").map((s) => s.trim()).filter(Boolean);
    draftUi.pleaOn = $("pleaOn").checked; draftUi.pleaText = $("pleaText").value.trim() || DEFAULT_MESSAGE;
  }
  on(sel, "change", () => {
    readSettings();
    if (sel.value === "learned" && !draft.learnedWeights) toast("Run Find my algorithm first - using default weights for now", 2400);
    applyPreset(draft, sel.value); fillSettings();
  });
  on($("sliders"), "input", (e) => {
    const f = e.target.dataset.w; if (!f) return;
    const v = e.target.value / 10; O$(f).textContent = (v > 0 ? "+" : "") + v.toFixed(1);
    if (sel.value !== "custom") { sel.value = "custom"; $("presetDesc").textContent = PRESETS.custom.desc; }
  });
  on($("exploration"), "input", (e) => ($("explorationOut").textContent = e.target.value + "%"));
  on($("diversity"), "input", (e) => ($("diversityOut").textContent = e.target.value + "%"));
  on($("checkFormula"), "click", () => {
    try { const v = VIDEOS[0] || toVideo("1000000000"); new Formula($("formula").value).evaluate(features(v, { interests: {}, preferredDuration: 10, recentAuthors: [] }), v);
      $("formulaMsg").className = "msg ok"; $("formulaMsg").textContent = "✅ Formula is valid"; }
    catch (e) { $("formulaMsg").className = "msg err"; $("formulaMsg").textContent = "❌ " + e.message; }
  });
  on($("preview"), "click", () => {
    readSettings();
    try { $("rank").innerHTML = rankAll(VIDEOS, draft, recentAuthors()).map((s) => `<div><b>${(s.score >= 0 ? "+" : "") + s.score.toFixed(2)}</b>@${esc(s.video.author)}${s.video.description ? " · " + esc(s.video.description) : ""}</div>`).join(""); }
    catch (e) { $("rank").innerHTML = `<div class="msg err">❌ ${esc(e.message)}</div>`; }
  });
  function renderVideoList() {
    const mine = draftExtra.map((x, i) => { const v = toVideo(x) || {}; return `<div class="vrow"><span>@${esc(v.author || "?")} <small>· ${esc(v.id || x)}</small></span><button data-rm="${i}" aria-label="Remove">×</button></div>`; });
    const base = BASE.filter((v) => !broken.has(v.id)).map((v) => `<div class="vrow"><span>@${esc(v.author)} <small>· built in</small></span><span></span></div>`);
    $("vlist").innerHTML = [...mine, ...base].join("") || '<p class="hint">No videos yet.</p>';
  }
  on($("vlist"), "click", (e) => { const i = e.target.dataset.rm; if (i !== undefined) { draftExtra.splice(+i, 1); renderVideoList(); } });
  on($("addBtn"), "click", () => {
    const v = toVideo($("addUrl").value), msg = $("addMsg");
    if (!v) { msg.className = "msg err"; msg.textContent = "That doesn't look like a TikTok video link. Open it once and copy the full tiktok.com/@user/video/… address."; return; }
    if (draftExtra.some((x) => (toVideo(x) || {}).id === v.id) || BASE.some((x) => x.id === v.id)) { msg.className = "msg"; msg.textContent = "Already in your list."; return; }
    broken.delete(v.id); store.set("broken", [...broken]);
    draftExtra.unshift($("addUrl").value.trim()); $("addUrl").value = ""; renderVideoList();
    msg.className = "msg ok"; msg.textContent = "Added - press Save to put it in your feed.";
  });
  const tokRow = (t, v) => `<div class="tok"><input type="text" value="${esc(t)}" data-t><input type="number" step="0.1" min="-3" max="3" value="${(+v).toFixed(2)}" data-v><button data-del aria-label="Remove">×</button></div>`;
  function renderTokens() {
    const items = Object.entries(draft.interests).sort((a, b) => b[1] - a[1]);
    $("tokens").innerHTML = items.length ? items.map(([t, v]) => tokRow(t, v)).join("") : `<p class="hint">No interests yet - watch some videos or add one.</p>`;
  }
  function readTokens() {
    const out = {};
    for (const row of $$(".tok")) {
      const t = row.querySelector("[data-t]").value.trim().toLowerCase(), v = parseFloat(row.querySelector("[data-v]").value);
      if (t && t !== "#" && t !== "@" && Number.isFinite(v)) out[t] = Math.max(-3, Math.min(3, v));
    }
    return out;
  }
  on($("tokens"), "click", (e) => { if (e.target.dataset.del !== undefined) e.target.closest(".tok").remove(); });
  on($("addTok"), "click", () => {
    if (!shadow.querySelector(".tok")) $("tokens").innerHTML = "";
    $("tokens").insertAdjacentHTML("beforeend", tokRow("@", 1)); $("tokens").lastElementChild.querySelector("input").focus();
  });
  on($("resetTok"), "click", () => { draft.interests = {}; renderTokens(); });
  on($("exportFile"), "change", async (e) => {
    const file = e.target.files[0]; if (!file) return;
    try {
      const { data, stats: st, seeds } = parseExport(file.name, await file.text());
      exportSeeds = seeds; exportStats = st;
      const parts = Object.entries(data).filter(([, v]) => v.length).map(([k, v]) => `${v.length} ${k}`);
      $("exportInfo").className = "msg ok";
      $("exportInfo").textContent = parts.length ? "Loaded: " + parts.join(", ") + "." + (st ? ` Median ${Math.round(st.median)}s per video, ${Math.round(st.skip * 100)}% skipped within 3s.` : "") : "Nothing recognisable in that file.";
    } catch (err) { $("exportInfo").className = "msg err"; $("exportInfo").textContent = "Couldn't read that file: " + err.message; }
  });
  on($("fit"), "click", () => {
    if (!history.length && !Object.keys(exportSeeds).length) { $("report").textContent = "Watch some videos first (finish a few, skip a few), or load your TikTok export."; return; }
    fitResult = learn(history, exportSeeds, exportStats);
    $("report").textContent = fitResult.report.join("\n");
    $("applyFit").disabled = false;
  });
  on($("applyFit"), "click", () => {
    if (!fitResult) return;
    readSettings();
    if (fitResult.weights) draft.learnedWeights = fitResult.weights;
    draft.interests = fitResult.interests;
    if (fitResult.preferred) draft.preferredDuration = Math.round(fitResult.preferred);
    applyPreset(draft, fitResult.weights ? "learned" : draft.preset);
    if (fitResult.weights) draft.exploration = fitResult.exploration;
    fillSettings(); selectTab("algo"); toast("Applied - press Save to use it");
  });
  on($("clearHist"), "click", () => { history = []; store.set("history", []); $("histInfo").textContent = "0 videos watched here so far."; });
  function renderWhy() {
    const s = shown[current]; if (!s) { $("why").innerHTML = '<p class="hint">Nothing on screen yet.</p>'; return; }
    const rows = Object.entries(s.features).sort((a, b) => Math.abs(b[1] * (cfg.weights[b[0]] || 0)) - Math.abs(a[1] * (cfg.weights[a[0]] || 0)))
      .map(([k, v]) => { const w = cfg.weights[k] || 0;
        return `<tr><td>${k}</td><td>${v.toFixed(2)}</td>${cfg.preset === "formula" ? "<td></td>" : `<td>× ${(w >= 0 ? "+" : "") + w.toFixed(1)} = <b>${(v * w >= 0 ? "+" : "") + (v * w).toFixed(2)}</b></td>`}</tr>`; }).join("");
    const matched = tokens(s.video).filter((t) => t in cfg.interests).slice(0, 8).map((t) => `${esc(t)} (${cfg.interests[t] > 0 ? "+" : ""}${cfg.interests[t].toFixed(1)})`);
    $("why").innerHTML = `<p style="font-size:13px">@${esc(s.video.author)} · ${esc(PRESETS[cfg.preset].name)}<br>Score <b>${s.score.toFixed(2)}</b>
      ${s.explored ? `<br>🎲 Picked at random by exploration (${Math.round(cfg.exploration * 100)}% of the time).` : ""}</p>
      <table class="why">${rows}</table>${matched.length ? `<p class="hint">Matched interests: ${matched.join(", ")}</p>` : ""}
      ${cfg.preset === "formula" ? `<p class="hint">Formula: <code>${esc(cfg.formula)}</code></p>` : ""}
      <p class="hint">TikTok's player shares no view or like counts with apps, so popularity-type signals are low for every video.</p>`;
  }

  /* ---------- keyboard (only while the pointer or focus is in this instance) ---------- */
  host.tabIndex = host.tabIndex >= 0 ? host.tabIndex : 0;
  on(host, "keydown", (e) => {
    const path = e.composedPath ? e.composedPath() : [];
    if (path.some((n) => n && n.tagName && /^(INPUT|TEXTAREA|SELECT)$/.test(n.tagName))) return;
    const settingsOpen = $("settings").classList.contains("show");
    if (e.key === "Escape") { settingsOpen ? closeSettings(false) : closeSheet(); return; }
    if (settingsOpen) return;
    const k = e.key.toLowerCase();
    if (["arrowdown", "j", "pagedown"].includes(k)) { e.preventDefault(); go(1); }
    else if (["arrowup", "k", "pageup"].includes(k)) { e.preventDefault(); go(-1); }
    else if (k === " ") { e.preventDefault(); togglePause(); }
    else if (k === "m") setMuted(!muted);
    else if (k === "c") openComments();
    else if (k === "s" && opt.settings) openSettings();
    else if (k === "i" && opt.settings) openSettings("why");
  });

  /* ---------- start ---------- */
  setMuted(true);
  rebuildVideos();
  VIDEOS.forEach(enrich);
  if (VIDEOS.length) { ensureAhead(); arrive(0); } else toast("No playable videos - add one in ⚙ › Videos", 6000);

  return {
    next: () => go(1),
    previous: () => go(-1),
    play: () => setPaused(false),
    pause: () => setPaused(true),
    mute: () => setMuted(true),
    unmute: () => setMuted(false),
    openSettings: (tab) => openSettings(tab),
    closeSettings: (save = false) => closeSettings(save),
    openComments,
    get current() { return shown[current] ? shown[current].video : null; },
    get videos() { return VIDEOS.slice(); },
    get history() { return history.slice(); },
    get algorithm() { return JSON.parse(JSON.stringify(cfg)); },
    setAlgorithm(next = {}) {
      const { preset, ...rest } = next, c = JSON.parse(JSON.stringify(cfg)); // keeps learned interests
      if (preset) applyPreset(c, preset);
      cfg = Object.assign(c, rest);
      store.set("config", cfg);
      while (shown.length > current + 1) { shown.pop(); feedEl.lastElementChild.remove(); }
      ensureAhead(); mountPlayer(current + 1);
    },
    addVideo(link) {
      const v = toVideo(link); if (!v) return false;
      if (!extra.some((x) => (toVideo(x) || {}).id === v.id)) { extra.unshift(typeof link === "string" ? link : v.url); store.set("videos", extra); }
      broken.delete(v.id); rebuildVideos(); enrich(VIDEOS.find((x) => x.id === v.id));
      if (current < 0) { ensureAhead(); arrive(0); } else { while (shown.length > current + 1) { shown.pop(); feedEl.lastElementChild.remove(); } ensureAhead(); mountPlayer(current + 1); }
      return true;
    },
    setMessage({ show, text } = {}) { if (show !== undefined) ui.pleaOn = !!show; if (text) ui.pleaText = String(text); store.set("ui", ui); applyPlea(); },
    destroy() {
      if (destroyed) return; destroyed = true;
      leave(current);
      listeners.forEach((off) => off());
      shadow.innerHTML = "";
    },
  };
}

/* ============================================================ public API */
/** A full algorithm config from a partial one, e.g. {preset: "popular"} or {weights: {interest: 4}}. */
function makeConfig(partial = {}) {
  const { preset, ...rest } = partial || {};
  const c = defaultConfig();
  if (preset) {
    if (!PRESETS[preset]) throw new Error(`Unknown preset "${preset}". Use one of: ${Object.keys(PRESETS).join(", ")}`);
    applyPreset(c, preset);
  }
  if (rest.weights) rest.weights = { ...c.weights, ...rest.weights };
  return Object.assign(c, rest);
}

/** Rank videos (links, ids or video objects) with an algorithm; returns [{video, score, features, explored}]. */
function rank(videos, config = {}, recentAuthors = []) {
  const list = (videos || []).map((v) => (v && typeof v === "object" && v.url && v.id ? v : toVideo(v))).filter(Boolean);
  return rankAll(list, makeConfig(config), recentAuthors);
}

const MiniTok = {
  version: VERSION,
  mount,
  rank,
  makeConfig,
  presets: PRESETS,
  features: FEATURES,
  starterVideos: STARTER_VIDEOS.map((v) => ({ ...v })),
  Formula,
  FormulaError,
  learn,
  engagement,
  updateInterests,
  parseExport,
  toVideo,
  tokens,
  exportComments,
  commentsFrom,
};

return MiniTok;
});
