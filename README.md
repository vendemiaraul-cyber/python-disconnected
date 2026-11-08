# python-disconnected · LLM Stream

One streaming chat app for every way of running a language model:

| Backend | Where the model runs | What you need |
|---|---|---|
| **WebLLM** | inside your browser tab (WebGPU) | a recent Chrome / Edge |
| **Ollama** | on your machine | [Ollama](https://ollama.com) running |
| **llama.cpp** | on your machine | `llama-server` on your PATH (LLM Stream can start it for you) |
| **Hugging Face** | hosted Inference Providers, for models too big to run at home | an `HF_TOKEN` |

It also answers the question **"can I run this big model?"** Drag a model link from
huggingface.co or ollama.com (or a `.gguf` file from your desktop) onto the page. LLM Stream checks:

- **whether the download is accessible:** it exists, it isn't private, and for gated models (Llama, Gemma…) whether your token has been granted access
- **what you'd download:** every GGUF quantization with its real size, with split files grouped together
- **whether it fits:** your RAM and free disk compared with each file, plus a "best fit" pick
- **what to do if it doesn't:** published GGUF conversions of the model, or hosted inference

**It always asks first.** Nothing downloads until you pick a version and confirm the size and
destination. The server also refuses any download that wasn't checked and confirmed.

No third-party Python packages are needed. It uses only the standard library (Python 3.9+).

## Quick start

```bash
git clone https://github.com/vendemiaraul-cyber/python-disconnected
cd python-disconnected
python3 -m llmstream          # opens http://127.0.0.1:8765
```

Or install it as a command: `pip install .` then `llmstream`.

## Using the web UI

1. Pick a tab (WebLLM, Ollama, llama.cpp, Hugging Face) and click a model.
2. Chat. Replies stream token by token, and **Stop** cancels mid-answer.
3. To get a new model, **drag its link onto the page** (or paste it into *Run a big model?*):
   - `https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF`
   - `https://huggingface.co/…/blob/main/model-Q4_K_M.gguf` (one specific file)
   - `hf.co/unsloth/Qwen3-8B-GGUF:Q4_K_M` (Ollama-style quant tag)
   - `https://ollama.com/library/qwen3:8b`
   - a non-GGUF repo like `meta-llama/Llama-3.1-405B-Instruct`, which gets you GGUF conversions to try and hosted options
   - a `.gguf` file from your desktop. Only its header is read to check it, and you can then copy it into the models folder
4. Choose a version, confirm, and watch the progress card. When it finishes, press
   **Load in llama.cpp** or **Chat with it** (Ollama).

WebLLM models also ask before downloading. They're cached in the browser afterwards and work offline.

## Command line

```bash
llmstream inspect https://huggingface.co/Qwen/Qwen3-8B-GGUF   # check, pick a version, confirm [y/N]
llmstream inspect ./some-model.gguf                           # what is this file, will it fit?
llmstream models                                              # everything you can chat with
llmstream chat -b ollama -m llama3.2                          # interactive streaming chat
llmstream chat -b llamacpp -m any "one-shot question"
llmstream chat -b huggingface -m meta-llama/Llama-3.1-8B-Instruct
```

Downloads resume where they stopped. Press Ctrl+C, then run the same command again.

## Configuration

| Variable / flag | Default | Meaning |
|---|---|---|
| `--models-dir`, `LLMSTREAM_MODELS` | `~/.llmstream/models` | where GGUF files are saved |
| `--ollama-url`, `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server |
| `--llamacpp-url`, `LLAMACPP_URL` | `http://127.0.0.1:8080` | llama-server address (used and started here) |
| `LLAMA_SERVER` | `llama-server` | path to the llama.cpp server binary |
| `HF_TOKEN` | `~/.cache/huggingface/token` | Hugging Face token: gated or private downloads and hosted chat |
| `serve --port / --host / --no-browser` | `8765 / 127.0.0.1` | web UI |

## Safety

- The server listens on `127.0.0.1` only. It checks the `Host` and `Origin` headers, and every
  POST needs an `X-LLMStream` header, so other websites can't trigger downloads or start processes.
- Files are written only inside the models folder, and llama.cpp / Ollama import only load files from there.
- Downloads go to `*.part` files first and are renamed when complete. Free disk is re-checked
  before each one starts.

## Development

```bash
python3 -m unittest discover -s tests -t .
```

The tests use fake Ollama, OpenAI-compatible and file servers, so they run offline.

```
llmstream/
  server.py        local HTTP server + streaming endpoints
  probe.py         "can I download / run this?" checks (no downloading)
  downloads.py     confirmed plans: resumable downloads, ollama pull, imports
  sources.py       understands dropped links / ids / files
  gguf.py          reads GGUF headers (works on a partial file)
  system.py        RAM, disk, GPU hints
  backends/        ollama, llama.cpp (llama-server), Hugging Face router
  static/          the web UI (WebLLM runs here, loaded from jsDelivr)
```
