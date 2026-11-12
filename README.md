# python-disconnected: the `.llm` file type

Download an AI model as **one file**, like `example.llm`, then chat with it on
your own computer, with no internet needed. The conversation is saved **inside
the file**, so you can copy `example.llm` to a USB stick or another computer and
pick up where you left off.

## Install

```bash
pip install -e .          # installs the `llmfile` command and llama-cpp-python
```

`llama-cpp-python` compiles llama.cpp on install if there's no prebuilt wheel for
your system. That takes a few minutes and needs a C/C++ compiler.

## Use the website

```bash
llmfile serve
```

This opens `http://localhost:8765` in your browser:

1. **Find models**: search Hugging Face (e.g. "qwen", "llama", "smollm"), click a
   model, pick a size, name the file (`example.llm`) and press **Download**.
2. **My files**: every `.llm` file you have, with how many messages are saved in it.
3. **Chat**: talk to the model. Replies stream in, and every message is saved
   into the `.llm` file. **New chat** clears it. **Settings** changes the system prompt.

Files go in `~/llm-files` (change it with `--dir` or the `LLMFILE_DIR` environment variable).
The site only listens on your own computer.

## Use the terminal

```bash
llmfile search smollm2
llmfile download bartowski/SmolLM2-135M-Instruct-GGUF Q4_K_M -o example.llm
llmfile chat ~/llm-files/example.llm      # or just: llmfile example.llm
llmfile info ~/llm-files/example.llm
```

In a chat, type `/reset`, `/system <text>`, `/history` or `/exit`.

Other commands:

| Command | What it does |
|---|---|
| `llmfile files <repo>` | list the sizes you can download from a model |
| `llmfile set example.llm temperature 0.3` | change a setting stored in the file (`name`, `system_prompt`, `temperature`, `n_ctx`, `max_tokens`) |
| `llmfile pack model.gguf -o example.llm` | turn a `.gguf` you already have into an `.llm` |
| `llmfile unpack example.llm` | get the plain `.gguf` back out |
| `llmfile register` | make double-clicking an `.llm` file open a chat (Windows, Linux; `--remove` to undo) |
| `llmfile chat example.llm --gpu-layers -1` | run on your GPU, if llama-cpp-python was built with GPU support |

Gated models (e.g. Meta's Llama): accept the license on huggingface.co, then set `HF_TOKEN`.

## The `.llm` format

An `.llm` file is a [GGUF](https://github.com/ggml-org/ggml/blob/master/docs/gguf.md)
model with a JSON trailer added to the end:

```
+------------------------------+
| GGUF model bytes (unchanged) |   llama.cpp loads this directly
+------------------------------+
| JSON metadata (UTF-8)        |   name, source, settings, chat history
+------------------------------+
| uint64 LE   JSON length      |
| uint64 LE   model length     |
| 8 bytes     "LLMFILE1"       |
+------------------------------+
```

- GGUF readers find tensors by offsets in the GGUF header and ignore extra bytes
  at the end, so llama.cpp loads an `.llm` file as-is, without unpacking. Other
  apps may insist on a `.gguf` name; `llmfile unpack` makes one.
- Saving a chat rewrites only the small trailer at the end, never the gigabytes of model.
- The file is never truncated while saving: shorter metadata is padded with
  spaces. That keeps saving safe on Windows while the model is memory-mapped.
- A plain `.gguf` opens too. It gets a trailer the first time you save a chat.

Example metadata:

```json
{
 "format": "llm", "version": 1,
 "name": "example",
 "created": "2026-11-12T06:29:52Z",
 "source": {"repo": "bartowski/SmolLM2-135M-Instruct-GGUF", "file": "SmolLM2-135M-Instruct-Q4_K_M.gguf"},
 "system_prompt": "You are a helpful assistant.",
 "params": {"n_ctx": 4096, "temperature": 0.7, "max_tokens": 1024},
 "chat": [{"role": "user", "content": "What is the capital of France?"},
          {"role": "assistant", "content": "The capital of France is Paris."}]
}
```

## Limits

- Models split into several files (`-00001-of-00003.gguf`, usually 50 GB+) and
  vision add-ons (`mmproj`) aren't offered for download.
- The site keeps one model in memory at a time. Opening a different file unloads the last one.
- `llmfile register` doesn't support macOS (it needs an app bundle). Use `llmfile chat` there.

## Tests

```bash
python -m unittest discover -s tests
```
