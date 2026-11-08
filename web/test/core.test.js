import { test } from "node:test";
import assert from "node:assert/strict";
import { parse, SourceError } from "../public/js/sources.js";
import { parseHeader, summarize, quantFromName, kvCacheBytes, GGUFError } from "../public/js/gguf.js";
import { assessFit, groupGGUF, recommend, applyFit } from "../public/js/inspect.js";

const GB = 1024 ** 3;
const M = { ramBytes: 16 * GB, diskBytes: 100 * GB };

test("parses Hugging Face, Ollama and direct links", () => {
  assert.deepEqual(
    (({ kind, repo, filename }) => ({ kind, repo, filename }))(parse("https://huggingface.co/bartowski/X-GGUF/blob/main/X-Q4_K_M.gguf")),
    { kind: "hf", repo: "bartowski/X-GGUF", filename: "X-Q4_K_M.gguf" });
  assert.equal(parse("hf.co/unsloth/DeepSeek-R1-GGUF:Q4_K_M").quant, "Q4_K_M");
  assert.equal(parse("meta-llama/Llama-3.1-8B-Instruct").repo, "meta-llama/Llama-3.1-8B-Instruct");
  const o = parse("https://ollama.com/library/llama3.2:3b");
  assert.equal(o.kind, "ollama"); assert.equal(o.model, "llama3.2"); assert.equal(o.tag, "3b");
  assert.equal(parse("gemma3:27b").tag, "27b");
  assert.equal(parse("https://example.com/m.Q4_0.gguf").kind, "url");
  assert.equal(parse("FROM llama3.2\nPARAMETER x 1").model, "llama3.2");
  assert.equal(parse("see https://huggingface.co/org/cool-model ok").repo, "org/cool-model");
  for (const bad of ["", "https://huggingface.co/datasets/a/b", "not a model !!"]) assert.throws(() => parse(bad), SourceError);
});

function makeGGUF(meta) {
  const enc = new TextEncoder();
  const parts = [];
  const u32 = (v) => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, v, true); parts.push(b); };
  const u64 = (v) => { const b = new Uint8Array(8); new DataView(b.buffer).setBigUint64(0, BigInt(v), true); parts.push(b); };
  const str = (s) => { const b = enc.encode(s); u64(b.length); parts.push(b); };
  parts.push(enc.encode("GGUF")); u32(3); u64(3); u64(Object.keys(meta).length);
  for (const [k, v] of Object.entries(meta)) {
    str(k);
    if (typeof v === "number") { u32(4); u32(v); }
    else if (typeof v === "string") { u32(8); str(v); }
    else if (Array.isArray(v)) { u32(9); u32(8); u64(v.length); v.forEach(str); }
  }
  const out = new Uint8Array(parts.reduce((a, p) => a + p.length, 0));
  let off = 0; for (const p of parts) { out.set(p, off); off += p.length; }
  return out;
}

test("reads GGUF headers, even truncated ones", () => {
  const meta = {
    "general.architecture": "llama", "general.name": "Tiny", "general.file_type": 15,
    "llama.context_length": 8192, "llama.block_count": 16, "llama.embedding_length": 2048,
    "llama.attention.head_count": 32, "llama.attention.head_count_kv": 8,
    "tokenizer.ggml.tokens": Array.from({ length: 100 }, (_, i) => `t${i}`),
  };
  const data = makeGGUF(meta);
  const h = parseHeader(data);
  assert.equal(h.truncated, false);
  assert.equal(h.metadata["tokenizer.ggml.tokens"].array_length, 100);
  const s = summarize(h, 5 * GB, "tiny.gguf");
  assert.equal(s.quantization, "Q4_K_M");
  assert.equal(kvCacheBytes(s, 4096), 2 * 16 * 4096 * 8 * 64 * 2);
  const t = parseHeader(data.slice(0, 120));
  assert.equal(t.truncated, true);
  assert.equal(t.metadata["general.architecture"], "llama");
  assert.throws(() => parseHeader(new TextEncoder().encode("PK zip")), GGUFError);
  assert.equal(quantFromName("x/Llama-3.2-1B-Instruct-IQ3_XXS.gguf"), "IQ3_XXS");
});

test("fit levels and disk blocking", () => {
  assert.equal(assessFit(4 * GB, M).level, "ok");
  assert.equal(assessFit(12 * GB, M).level, "warn");
  assert.equal(assessFit(20 * GB, M).level, "bad");
  const f = assessFit(4 * GB, { ramBytes: 16 * GB, diskBytes: 2 * GB });
  assert.equal(f.blocked, true);
  assert.equal(assessFit(4 * GB, { ramBytes: 0 }).level, "warn");
});

test("groups split files and recommends a sensible quant", () => {
  const groups = groupGGUF([
    { rfilename: "M-Q2_K.gguf", size: 3 * GB },
    { rfilename: "M-Q4_K_M.gguf", size: 5 * GB },
    { rfilename: "M-F16-00001-of-00002.gguf", size: 9 * GB },
    { rfilename: "M-F16-00002-of-00002.gguf", size: 8 * GB },
    { rfilename: "Q6_K/M-00001-of-00002.gguf", size: 4 * GB },
    { rfilename: "Q6_K/M-00002-of-00002.gguf", size: 3 * GB },
  ]);
  const q = Object.fromEntries(groups.map((g) => [g.quant, g]));
  assert.deepEqual(Object.keys(q).sort(), ["F16", "Q2_K", "Q4_K_M", "Q6_K"]);
  assert.equal(q.F16.size, 17 * GB);
  const fits = Object.fromEntries(groups.map((g) => [g.key, assessFit(g.size, M)]));
  assert.equal(recommend(groups, fits), q.Q4_K_M.key);
});

test("applyFit picks a verdict and a best option", () => {
  const r = {
    title: "x", access: { ok: true }, pickBest: true,
    options: [{ key: "a", title: "Q4_K_M", quant: "Q4_K_M", size: 5 * GB, actions: [] }, { key: "b", title: "F16", quant: "F16", size: 30 * GB, actions: [] }],
  };
  applyFit(r, M);
  assert.equal(r.verdict.level, "ok");
  assert.equal(r.options[0].recommended, true);
  assert.equal(r.options[1].fit.level, "bad");
});
