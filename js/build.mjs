// Builds dist/minitok.js (works with a plain <script> tag, CommonJS and AMD-free loaders)
// and dist/minitok.mjs (ES module) from src/. No dependencies: `node build.mjs`.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(here, "package.json"), "utf8"));
const src = ["core.js", "widget.js", "index.js"].map((f) => readFileSync(join(here, "src", f), "utf8")).join("\n");
const banner = `/*! MiniTok ${pkg.version} - a TikTok-style feed with an algorithm you control. MIT License. ${pkg.homepage} */`;
const body = `"use strict";\nconst VERSION = ${JSON.stringify(pkg.version)};\n${src}\nreturn MiniTok;`;

const umd = `${banner}
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.MiniTok = factory();
})(typeof self !== "undefined" ? self : this, function () {
${body}
});
`;
const names = ["mount", "rank", "makeConfig", "presets", "features", "starterVideos", "Formula", "FormulaError", "learn",
  "engagement", "updateInterests", "parseExport", "toVideo", "tokens", "exportComments", "commentsFrom", "version"];
const esm = `${banner}
const MiniTok = (function () {
${body}
})();
export default MiniTok;
export const { ${names.join(", ")} } = MiniTok;
`;
mkdirSync(join(here, "dist"), { recursive: true });
writeFileSync(join(here, "dist", "minitok.js"), umd);
writeFileSync(join(here, "dist", "minitok.mjs"), esm);
// the project page's browser preview runs on this same build
const site = join(here, "..", "docs", "app");
try {
  writeFileSync(join(site, "minitok.js"), umd);
  writeFileSync(join(site, "..", "llms.txt"), readFileSync(join(here, "llms.txt"), "utf8")); // where AI tools look
} catch { /* no docs/ next to the library */ }
console.log(`built MiniTok ${pkg.version}: dist/minitok.js (${(umd.length / 1024).toFixed(0)} KB), dist/minitok.mjs`);
