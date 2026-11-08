# LLM Stream: website edition

Live at **https://llm-stream-chat.pages.dev** (Cloudflare Pages).

This is the browser-only version of LLM Stream. There's no server to run: everything happens in the visitor's browser.

| Backend | How the website reaches it |
|---|---|
| WebLLM | runs inside the tab with WebGPU (Chrome / Edge) |
| Ollama | the browser calls the visitor's own Ollama on `localhost:11434`. Start Ollama with `OLLAMA_ORIGINS="https://llm-stream-chat.pages.dev" ollama serve` |
| llama.cpp | the browser calls the visitor's own `llama-server` on `localhost:8080` |
| Hugging Face | the browser calls `router.huggingface.co` with the visitor's own token, kept in their browser's localStorage |

The **drag-and-drop model checker** works as in the Python app: it checks whether the download is
accessible, lists every quantization with its size, and compares it with the visitor's RAM and free disk.
Browsers can't read those numbers reliably, so they're entered once in Settings. Nothing downloads
without a confirmation. After confirming, a file downloads through the browser, or goes into the visitor's
Ollama with `/api/pull`. A dropped `.gguf` file is read in the browser. Only its header is read, and it's never uploaded.

The only server-side code is `functions/api/proxy.js`, a read-only helper for the two things browsers
can't do directly: read Ollama registry manifests (no CORS there), and read the size and header of
arbitrary download links. It never forwards cookies or tokens.

## Develop

```bash
cd web
npm test                 # unit tests (node:test, no install needed)
npm run dev              # wrangler pages dev on http://localhost:8788
```

## Deploy

`npm run deploy` packs `public/` and the proxy function into a single Pages worker (`dist/_worker.js`)
and uploads it in one API call. It needs `CLOUDFLARE_ACCOUNT_ID` and a `CLOUDFLARE_API_TOKEN` with
*Cloudflare Pages: Edit*. `npx wrangler pages deploy public` also works with a normal token.
