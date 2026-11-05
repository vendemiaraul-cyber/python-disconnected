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

