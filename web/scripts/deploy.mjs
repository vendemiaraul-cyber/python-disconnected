// Deploy dist/_worker.js to Cloudflare Pages with one API call.
// Needs CLOUDFLARE_ACCOUNT_ID and a CLOUDFLARE_API_TOKEN with "Pages: Edit".
// Usage: node scripts/build-worker.mjs && node scripts/deploy.mjs [project] [branch]
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";

const root = new URL("..", import.meta.url).pathname;
const [project = "llm-stream-chat", branch = "main"] = process.argv.slice(2);
const account = process.env.CLOUDFLARE_ACCOUNT_ID;
const token = process.env.CLOUDFLARE_API_TOKEN;
if (!account || !token) throw new Error("set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN");

// The worker bundle is itself a multipart body: metadata + the module.
const fd = new FormData();
fd.set("metadata", JSON.stringify({ main_module: "_worker.js" }));
fd.set("_worker.js", new File([readFileSync(join(root, "dist/_worker.js"))], "_worker.js", { type: "application/javascript+module" }));
const bundle = Buffer.from(await (await new Response(fd).blob()).arrayBuffer());
const tmp = mkdtempSync(join(tmpdir(), "pages-"));
writeFileSync(join(tmp, "_worker.bundle"), bundle);

let commit = "";
try { commit = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(); } catch { /* not a repo */ }

// curl respects HTTPS_PROXY, which Node's fetch doesn't by default.
const out = execFileSync("curl", [
  "-sS", "-X", "POST",
  `https://api.cloudflare.com/client/v4/accounts/${account}/pages/projects/${project}/deployments`,
  "-H", `Authorization: Bearer ${token}`,
  "-F", "manifest={}",
  "-F", `branch=${branch}`,
  ...(commit ? ["-F", `commit_hash=${commit}`, "-F", "commit_message=Deploy LLM Stream"] : []),
  "-F", `_worker.bundle=@${join(tmp, "_worker.bundle")};filename=_worker.bundle`,
], { encoding: "utf8" });
const res = JSON.parse(out);
if (!res.success) { console.error(JSON.stringify(res.errors, null, 2)); process.exit(1); }
console.log(`deployed: ${res.result.url}`);
console.log(`stage: ${res.result.latest_stage?.name} ${res.result.latest_stage?.status}`);
