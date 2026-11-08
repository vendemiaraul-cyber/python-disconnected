// Read a GGUF header in the browser (only the first few MB of a file are needed). Port of gguf.py.

export const FILE_TYPES = {
  0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M",
  13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS",
  21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
  29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0",
};
const QUANT_RE = /(?<![A-Za-z0-9])(I?Q\d(?:_[0-9A-Z]+)*|TQ\d_\d|BF16|F16|FP16|F32|MXFP4)(?![A-Za-z0-9])/gi;
const KEEP_ARRAY_ITEMS = 16;

export class GGUFError extends Error {}
class Truncated extends Error {}

export function parseHeader(buffer) {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length < 4 || String.fromCharCode(...bytes.slice(0, 4)) !== "GGUF") throw new GGUFError("not a GGUF file (missing 'GGUF' magic bytes)");
  let pos = 4;
  const need = (n) => { if (pos + n > bytes.length) throw new Truncated(); };
  const u32 = () => { need(4); const v = view.getUint32(pos, true); pos += 4; return v; };
  const u64 = () => { need(8); const v = Number(view.getBigUint64(pos, true)); pos += 8; return v; };
  const version = u32();
  if (![1, 2, 3].includes(version)) throw new GGUFError(`unsupported GGUF version ${version}`);
  const len = version === 1 ? u32 : u64;
  const dec = new TextDecoder();
  const str = () => { const n = len(); if (n > 2 ** 30) throw new GGUFError("corrupt string length"); need(n); const s = dec.decode(bytes.subarray(pos, pos + n)); pos += n; return s; };
  const scalar = {
    0: () => { need(1); return view.getUint8(pos++); },
    1: () => { need(1); return view.getInt8(pos++); },
    2: () => { need(2); const v = view.getUint16(pos, true); pos += 2; return v; },
    3: () => { need(2); const v = view.getInt16(pos, true); pos += 2; return v; },
    4: u32,
    5: () => { need(4); const v = view.getInt32(pos, true); pos += 4; return v; },
    6: () => { need(4); const v = view.getFloat32(pos, true); pos += 4; return v; },
    7: () => { need(1); return view.getUint8(pos++) !== 0; },
    10: u64,
    11: () => { need(8); const v = Number(view.getBigInt64(pos, true)); pos += 8; return v; },
    12: () => { need(8); const v = view.getFloat64(pos, true); pos += 8; return v; },
  };
  const value = (t) => {
    if (scalar[t]) return scalar[t]();
    if (t === 8) return str();
    if (t === 9) {
      const it = u32();
      const count = len();
      const items = [];
      for (let i = 0; i < count; i++) { const v = value(it); if (i < KEEP_ARRAY_ITEMS) items.push(v); }
      return count > KEEP_ARRAY_ITEMS ? { array_length: count, items } : items;
    }
    throw new GGUFError(`unknown GGUF value type ${t}`);
  };
  const tensorCount = len();
  const kvCount = len();
  const metadata = {};
  let truncated = false;
  try {
    for (let i = 0; i < kvCount; i++) { const k = str(); metadata[k] = value(u32()); }
  } catch (e) {
    if (e instanceof Truncated) truncated = true; else throw e;
  }
  return { version, tensor_count: tensorCount, kv_count: kvCount, metadata, truncated };
}

export function quantFromName(name) {
  const base = String(name).split("/").pop();
  const m = base.match(QUANT_RE);
  return m ? m[m.length - 1].toUpperCase() : null;
}

export function summarize(header, fileSize = null, filename = null) {
  const md = header.metadata || {};
  const arch = md["general.architecture"];
  const a = (k) => (arch ? md[`${arch}.${k}`] : undefined);
  const ft = md["general.file_type"];
  let quant = typeof ft === "number" ? FILE_TYPES[ft] : null;
  if (!quant && filename) quant = quantFromName(filename);
  let nHead = a("attention.head_count"), nKv = a("attention.head_count_kv");
  if (Array.isArray(nHead)) nHead = Math.max(...nHead);
  if (Array.isArray(nKv)) nKv = Math.max(...nKv);
  return {
    name: md["general.name"] || (filename ? filename.replace(/\.gguf$/i, "") : null),
    architecture: arch || null,
    size_label: md["general.size_label"] || null,
    parameters: md["general.parameter_count"] || null,
    quantization: quant || null,
    context_length: a("context_length") || null,
    layers: a("block_count") || null,
    embedding_length: a("embedding_length") || null,
    head_count: nHead || null,
    head_count_kv: nKv || nHead || null,
    has_chat_template: "tokenizer.chat_template" in md,
    split_count: md["split.count"] || null,
    file_size: fileSize,
    header_truncated: !!header.truncated,
  };
}

export function kvCacheBytes(s, context = 4096) {
  const { layers, embedding_length: e, head_count: h, head_count_kv: kv } = s;
  if (![layers, e, h, kv].every((x) => Number.isInteger(x) && x > 0)) return 0;
  return 2 * layers * context * kv * Math.floor(e / h) * 2;
}
