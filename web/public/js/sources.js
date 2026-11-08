// Work out what someone dropped or pasted: a Hugging Face repo/file, an Ollama
// model, a direct download link, or a local file name. (Port of sources.py.)

const HF_HOSTS = new Set(["huggingface.co", "www.huggingface.co", "hf.co"]);
const OLLAMA_HOSTS = new Set(["ollama.com", "www.ollama.com", "ollama.ai", "www.ollama.ai", "registry.ollama.ai"]);
const HF_ID = /^([A-Za-z0-9][\w.\-]*)\/([A-Za-z0-9][\w.\-]*)(?::([\w.\-]+))?$/;
const OLLAMA_ID = /^([a-z0-9][\w.\-]*)(?::([\w.\-]+))?$/i;
const URL_IN_TEXT = /https?:\/\/[^\s<>"')\]]+/g;
const HF_SKIP_FIRST = new Set(["datasets", "spaces", "docs", "blog", "models", "papers", "collections", "organizations", "settings", "join", "login"]);

export class SourceError extends Error {}

export function extractCandidate(text) {
  text = (text || "").trim();
  if (!text) throw new SourceError("nothing to inspect");
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  if (lines.length === 1 && !lines[0].includes(" ")) return lines[0];
  const urls = text.match(URL_IN_TEXT) || [];
  for (const u of urls) {
    let host = "";
    try { host = new URL(u).host.toLowerCase(); } catch { continue; }
    if (HF_HOSTS.has(host) || OLLAMA_HOSTS.has(host) || u.toLowerCase().split("?")[0].endsWith(".gguf")) return u;
  }
  if (urls.length) return urls[0];
  for (const ln of lines) {
    if (ln.toLowerCase().startsWith("from ")) return ln.slice(5).trim();
    if (!ln.includes(" ")) return ln;
  }
  throw new SourceError("couldn't find a model link or name in the dropped text");
}

const normQuant = (q) => (!q || q.toLowerCase() === "latest" ? null : q.toUpperCase());

function ollama(raw, spec) {
  spec = spec.replace(/^\/+|\/+$/g, "");
  if (spec.startsWith("library/")) spec = spec.slice(8);
  const [name, tag] = spec.split(":");
  if (!name) throw new SourceError("empty Ollama model name");
  return { kind: "ollama", raw, model: name.toLowerCase(), tag: tag || "latest" };
}

export function parse(text) {
  const raw = extractCandidate(text);
  let c = raw.trim().replace(/^<|>$/g, "").replace(/\/+$/, "");

  if (!/^https?:\/\//.test(c) && /\.gguf$/i.test(c)) return { kind: "local", raw, path: c };
  for (const p of ["hf://", "huggingface://"]) if (c.startsWith(p)) c = "https://huggingface.co/" + c.slice(p.length);
  if (c.startsWith("ollama://")) c = "ollama:" + c.slice(9);
  if (c.startsWith("hf.co/") || c.startsWith("huggingface.co/")) c = "https://" + c;
  if (c.startsWith("ollama.com/") || c.startsWith("www.ollama.com/")) c = "https://" + c;

  if (/^https?:\/\//.test(c)) return parseUrl(raw, c);
  if (c.toLowerCase().startsWith("ollama:")) return ollama(raw, c.split(":").slice(1).join(":"));
  let m = c.match(HF_ID);
  if (m) return { kind: "hf", raw, repo: `${m[1]}/${m[2]}`, revision: "main", filename: null, quant: normQuant(m[3]) };
  if (OLLAMA_ID.test(c)) return ollama(raw, c);
  throw new SourceError(`don't know how to fetch "${raw}"; try a Hugging Face or Ollama link`);
}

function parseUrl(raw, url) {
  const u = new URL(url);
  const host = u.host.toLowerCase();
  let parts = u.pathname.split("/").filter(Boolean).map(decodeURIComponent);

  if (HF_HOSTS.has(host)) {
    if (!parts.length || HF_SKIP_FIRST.has(parts[0])) {
      if (parts[0] === "models" && parts.length >= 3) parts = parts.slice(1);
      else throw new SourceError("that Hugging Face link isn't a model page");
    }
    if (parts.length < 2) throw new SourceError("that Hugging Face link doesn't name a model (expected huggingface.co/owner/model)");
    let [owner, name] = parts;
    let quant = null;
    if (name.includes(":")) { [name, quant] = name.split(":"); quant = normQuant(quant); }
    const src = { kind: "hf", raw, repo: `${owner}/${name}`, revision: "main", filename: null, quant };
    if (parts.length >= 4 && ["blob", "resolve", "tree"].includes(parts[2])) {
      src.revision = parts[3];
      if (parts[2] !== "tree" && parts.length >= 5) src.filename = parts.slice(4).join("/");
    }
    return src;
  }

  if (OLLAMA_HOSTS.has(host)) {
    if (host === "registry.ollama.ai" && parts.length >= 4 && parts[0] === "v2") {
      const [, ns, name] = parts;
      return { kind: "ollama", raw, model: ns === "library" ? name : `${ns}/${name}`, tag: parts[4] || "latest" };
    }
    if (!parts.length) throw new SourceError("that Ollama link doesn't name a model");
    if (parts[0] === "library" && parts.length >= 2) return ollama(raw, parts[1]);
    if (["search", "blog", "download", "docs", "models", "settings", "public"].includes(parts[0])) throw new SourceError("that Ollama link isn't a model page");
    if (parts.length >= 2 && parts[1] !== "tags") return ollama(raw, `${parts[0]}/${parts[1]}`);
    return ollama(raw, parts[0]);
  }

  return { kind: "url", raw, url, filename: parts[parts.length - 1] || "download.bin" };
}

export function label(src) {
  if (src.kind === "hf") return src.repo + (src.filename ? `/${src.filename}` : "") + (src.quant ? `:${src.quant}` : "");
  if (src.kind === "ollama") return `${src.model}:${src.tag}`;
  return src.url || src.path || src.raw;
}
