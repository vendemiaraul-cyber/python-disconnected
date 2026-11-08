import tempfile
import unittest
from pathlib import Path

from llmstream import gguf
from llmstream.probe import _gguf_groups, _recommend, assess_fit
from llmstream.sources import SourceError, parse
from llmstream.system import GB, human_size
from tests.fakes import make_gguf

SYSTEM = {"memory_total": 16 * GB, "memory_available": 12 * GB, "disk_free": 100 * GB}


class SourceTests(unittest.TestCase):
    def check(self, text, **expected):
        src = parse(text)
        for k, v in expected.items():
            self.assertEqual(getattr(src, k), v, f"{text!r}: {k}")
        return src

    def test_huggingface_forms(self):
        self.check("https://huggingface.co/Qwen/Qwen3-8B-GGUF", kind="hf", repo="Qwen/Qwen3-8B-GGUF", filename=None)
        self.check("huggingface.co/Qwen/Qwen3-8B-GGUF/tree/main", kind="hf", repo="Qwen/Qwen3-8B-GGUF")
        self.check("https://huggingface.co/bartowski/X-GGUF/blob/main/X-Q4_K_M.gguf", kind="hf", repo="bartowski/X-GGUF", filename="X-Q4_K_M.gguf")
        self.check("https://huggingface.co/a/b/resolve/dev/sub/f.gguf?download=true", repo="a/b", revision="dev", filename="sub/f.gguf")
        self.check("hf.co/unsloth/DeepSeek-R1-GGUF:Q4_K_M", kind="hf", repo="unsloth/DeepSeek-R1-GGUF", quant="Q4_K_M")
        self.check("meta-llama/Llama-3.1-8B-Instruct", kind="hf", repo="meta-llama/Llama-3.1-8B-Instruct")
        self.check("hf://org/model", kind="hf", repo="org/model")
        self.check("https://huggingface.co/models/org/model", kind="hf", repo="org/model")

    def test_ollama_forms(self):
        self.check("https://ollama.com/library/llama3.2:3b", kind="ollama", model="llama3.2", tag="3b")
        self.check("ollama.com/library/qwen3", kind="ollama", model="qwen3", tag="latest")
        self.check("https://ollama.com/someone/custom", kind="ollama", model="someone/custom")
        self.check("llama3.2", kind="ollama", model="llama3.2")
        self.check("gemma3:27b", kind="ollama", model="gemma3", tag="27b")
        self.check("ollama:mistral", kind="ollama", model="mistral")

    def test_other_forms(self):
        self.check("https://example.com/files/model.Q4_K_M.gguf", kind="url", filename="model.Q4_K_M.gguf")
        self.check("~/models/thing.gguf", kind="local")
        self.check("FROM llama3.2\nPARAMETER temperature 1", kind="ollama", model="llama3.2")
        self.check("Check this out: https://huggingface.co/org/cool-model ok?", kind="hf", repo="org/cool-model")

    def test_rejects(self):
        for bad in ("", "https://huggingface.co/datasets/a/b", "https://huggingface.co/spaces/a/b", "not a model at all !!"):
            with self.assertRaises(SourceError, msg=bad):
                parse(bad)


class GGUFTests(unittest.TestCase):
    META = {
        "general.architecture": "llama",
        "general.name": "Tiny Llama",
        "general.file_type": 15,
        "general.size_label": "1B",
        "llama.context_length": 8192,
        "llama.block_count": 16,
        "llama.embedding_length": 2048,
        "llama.attention.head_count": 32,
        "llama.attention.head_count_kv": 8,
        "tokenizer.ggml.tokens": [f"t{i}" for i in range(100)],
        "tokenizer.chat_template": "{{ x }}",
    }

    def test_parse_and_summarize(self):
        data = make_gguf(self.META)
        header = gguf.parse_header(data)
        self.assertFalse(header["truncated"])
        self.assertEqual(header["metadata"]["tokenizer.ggml.tokens"]["array_length"], 100)
        s = gguf.summarize(header, 10 * GB, "tiny.gguf")
        self.assertEqual(s["quantization"], "Q4_K_M")
        self.assertEqual(s["context_length"], 8192)
        self.assertEqual(s["head_count_kv"], 8)
        self.assertTrue(s["has_chat_template"])
        # 2 * layers * ctx * kv_heads * head_dim * 2 bytes
        self.assertEqual(gguf.kv_cache_bytes(s, 4096), 2 * 16 * 4096 * 8 * 64 * 2)

    def test_truncated_header_keeps_what_it_read(self):
        data = make_gguf(self.META)
        header = gguf.parse_header(data[:200])
        self.assertTrue(header["truncated"])
        self.assertEqual(header["metadata"]["general.architecture"], "llama")

    def test_not_gguf(self):
        with self.assertRaises(gguf.GGUFError):
            gguf.parse_header(b"PK\x03\x04 zip file")

    def test_quant_from_name(self):
        self.assertEqual(gguf.quant_from_name("Llama-3.2-1B-Instruct-Q4_K_M.gguf"), "Q4_K_M")
        self.assertEqual(gguf.quant_from_name("model-IQ3_XXS.gguf"), "IQ3_XXS")
        self.assertEqual(gguf.quant_from_name("model.f16.gguf"), "F16")
        self.assertIsNone(gguf.quant_from_name("model.gguf"))

    def test_read_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.gguf"
            p.write_bytes(make_gguf(self.META) + b"\0" * 1000)
            self.assertEqual(gguf.read_file_header(p)["metadata"]["general.name"], "Tiny Llama")


class FitTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(assess_fit(4 * GB, SYSTEM)["level"], "ok")
        self.assertEqual(assess_fit(12 * GB, SYSTEM)["level"], "warn")
        self.assertEqual(assess_fit(20 * GB, SYSTEM)["level"], "bad")
        self.assertEqual(assess_fit(200 * GB, {**SYSTEM, "disk_free": 1000 * GB})["level"], "bad")
        self.assertEqual(assess_fit(None, SYSTEM)["level"], "warn")

    def test_disk_blocks(self):
        fit = assess_fit(4 * GB, {**SYSTEM, "disk_free": 2 * GB})
        self.assertEqual(fit["level"], "bad")
        self.assertTrue(fit["blocked"])
        self.assertIn("disk", fit["messages"][0])

    def test_groups_and_recommendation(self):
        files = [
            {"rfilename": "M-Q2_K.gguf", "size": 3 * GB},
            {"rfilename": "M-Q4_K_M.gguf", "size": 5 * GB},
            {"rfilename": "M-Q8_0.gguf", "size": 9 * GB},
            {"rfilename": "M-F16-00001-of-00002.gguf", "size": 9 * GB},
            {"rfilename": "M-F16-00002-of-00002.gguf", "size": 8 * GB},
            {"rfilename": "Q6_K/M-00001-of-00002.gguf", "size": 4 * GB},
            {"rfilename": "Q6_K/M-00002-of-00002.gguf", "size": 3 * GB},
            {"rfilename": "README.md", "size": 10},
        ]
        groups = _gguf_groups(files)
        by_quant = {g["quant"]: g for g in groups}
        self.assertEqual(set(by_quant), {"Q2_K", "Q4_K_M", "Q8_0", "F16", "Q6_K"})
        self.assertEqual(by_quant["F16"]["size"], 17 * GB)
        self.assertEqual(len(by_quant["Q6_K"]["files"]), 2)
        fits = {g["key"]: assess_fit(g["size"], SYSTEM) for g in groups}
        self.assertEqual(_recommend(groups, fits), by_quant["Q4_K_M"]["key"])
        small = {"memory_total": 4 * GB, "memory_available": 3 * GB, "disk_free": 100 * GB}
        fits = {g["key"]: assess_fit(g["size"], small) for g in groups}
        self.assertEqual(_recommend(groups, fits), by_quant["Q2_K"]["key"])  # nothing fits: smallest

    def test_human_size(self):
        self.assertEqual(human_size(1536), "2 KB")
        self.assertEqual(human_size(5 * GB), "5.0 GB")


if __name__ == "__main__":
    unittest.main()
