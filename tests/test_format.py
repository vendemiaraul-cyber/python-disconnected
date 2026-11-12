import os
import struct
import tempfile
import unittest

from llmfile.format import FOOTER, LLMFileError, new_metadata, open_llm, pack, unpack, write_trailer

FAKE_GGUF = b"GGUF" + struct.pack("<I", 3) + os.urandom(4096)


class FormatTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.gguf = os.path.join(self.dir, "m.gguf")
        with open(self.gguf, "wb") as f:
            f.write(FAKE_GGUF)

    def test_pack_roundtrip(self):
        out = os.path.join(self.dir, "example.llm")
        pack(self.gguf, out, new_metadata("example", {"repo": "a/b"}))
        f = open_llm(out)
        self.assertEqual(f.name, "example")
        self.assertEqual(f.model_size, len(FAKE_GGUF))
        self.assertEqual(f.meta["source"]["repo"], "a/b")
        with open(out, "rb") as fh:  # the model bytes are untouched at the start
            self.assertEqual(fh.read(len(FAKE_GGUF)), FAKE_GGUF)
        back = os.path.join(self.dir, "back.gguf")
        unpack(out, back)
        with open(back, "rb") as fh:
            self.assertEqual(fh.read(), FAKE_GGUF)

    def test_chat_history_saved_in_file(self):
        out = os.path.join(self.dir, "example.llm")
        pack(self.gguf, out, new_metadata("example"))
        f = open_llm(out)
        f.messages.append({"role": "user", "content": "héllo " * 500})
        f.save()
        self.assertEqual(open_llm(out).messages[0]["content"], "héllo " * 500)
        size_big = os.path.getsize(out)
        f.reset()  # shrinking pads instead of truncating
        self.assertEqual(open_llm(out).messages, [])
        self.assertEqual(os.path.getsize(out), size_big)
        f.messages.append({"role": "user", "content": "x" * 10000})
        f.save()  # and it can grow again
        self.assertEqual(len(open_llm(out).messages[0]["content"]), 10000)
        self.assertEqual(open_llm(out).model_size, len(FAKE_GGUF))

    def test_plain_gguf_opens(self):
        f = open_llm(self.gguf)
        self.assertFalse(f.has_trailer)
        self.assertEqual(f.model_size, len(FAKE_GGUF))
        f.messages.append({"role": "user", "content": "hi"})
        f.save()
        self.assertTrue(open_llm(self.gguf).has_trailer)

    def test_rejects_non_model(self):
        bad = os.path.join(self.dir, "bad.llm")
        with open(bad, "wb") as fh:
            fh.write(b"not a model")
        with self.assertRaises(LLMFileError):
            open_llm(bad)

    def test_footer_layout(self):
        out = os.path.join(self.dir, "example.llm")
        pack(self.gguf, out, new_metadata("example"))
        with open(out, "rb") as fh:
            fh.seek(-FOOTER.size, os.SEEK_END)
            json_len, model_len, magic = FOOTER.unpack(fh.read())
        self.assertEqual(magic, b"LLMFILE1")
        self.assertEqual(model_len + json_len + FOOTER.size, os.path.getsize(out))


if __name__ == "__main__":
    unittest.main()
