// Small, locked-down helper for the model checker. Browsers can't call the
// Ollama registry directly (no CORS), or read headers of arbitrary links, so
// this function does exactly four read-only things:
//   op=manifest  GET an Ollama registry manifest           (registry.ollama.ai only)
//   op=blob      GET a small Ollama config blob (< 64 KB)  (registry.ollama.ai only)
//   op=head      HEAD any https URL, return status + size headers as JSON
//   op=range     GET the first 8 MB of a .gguf URL, to read its header
// It never forwards cookies or auth headers, and never streams whole files.

const REGISTRY = "registry.ollama.ai";
const MAX_BLOB = 64 * 1024;
const RANGE_BYTES = 8 * 1024 * 1024;
const UA = "llm-stream-web/0.1";

const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json", "cache-control": "public, max-age=300", ...extra },
  });

async function readCapped(res, cap) {
  const reader = res.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > cap) { await reader.cancel(); throw new Error("too large"); }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) { out.set(c, off); off += c.length; }
  return out;
}

export async function onRequestGet({ request }) {
  const params = new URL(request.url).searchParams;
  const op = params.get("op");
  let target;
  try {
    target = new URL(params.get("url") || "");
  } catch {
    return json({ error: "bad url" }, 400);
  }
  if (target.protocol !== "https:") return json({ error: "https only" }, 400);

  if (op === "manifest" || op === "blob") {
    if (target.hostname !== REGISTRY || !target.pathname.startsWith("/v2/")) return json({ error: "registry.ollama.ai only" }, 400);
    if (op === "manifest" && !/\/manifests\/[\w.\-]+$/.test(target.pathname)) return json({ error: "not a manifest" }, 400);
    if (op === "blob" && !/\/blobs\/sha256:[0-9a-f]{64}$/.test(target.pathname)) return json({ error: "not a blob" }, 400);
    const res = await fetch(target, {
      headers: { "user-agent": UA, accept: "application/vnd.docker.distribution.manifest.v2+json" },
      cf: { cacheTtl: 600, cacheEverything: true },
    });
    if (!res.ok) return json({ error: `registry said ${res.status}` }, res.status === 404 ? 404 : 502);
    try {
      const body = await readCapped(res, MAX_BLOB);
      return new Response(body, { headers: { "content-type": "application/json", "cache-control": "public, max-age=600" } });
    } catch {
      return json({ error: "blob too large" }, 413);
    }
  }

  if (op === "head") {
    try {
      const res = await fetch(target, { method: "HEAD", headers: { "user-agent": UA }, redirect: "follow" });
      const len = res.headers.get("content-length");
      return json({
        status: res.status,
        content_length: len && /^\d+$/.test(len) ? Number(len) : null,
        accept_ranges: res.headers.get("accept-ranges"),
        content_type: res.headers.get("content-type"),
      });
    } catch (e) {
      return json({ status: 0, error: String(e) });
    }
  }

  if (op === "range") {
    if (!/\.gguf$/i.test(target.pathname)) return json({ error: ".gguf links only" }, 400);
    const res = await fetch(target, { headers: { "user-agent": UA, range: `bytes=0-${RANGE_BYTES - 1}` }, redirect: "follow" });
    if (res.status !== 206) {
      if (res.body) await res.body.cancel();
      return json({ error: "server doesn't support partial downloads" }, 502);
    }
    try {
      const body = await readCapped(res, RANGE_BYTES);
      return new Response(body, { headers: { "content-type": "application/octet-stream", "cache-control": "public, max-age=3600" } });
    } catch {
      return json({ error: "range too large" }, 502);
    }
  }

  return json({ error: "unknown op" }, 400);
}
