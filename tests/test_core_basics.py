import ast
import json
import re
import unittest
import zipfile
from fractions import Fraction
from pathlib import Path

import helpers  # noqa: F401  (sets sys.path)
from dafan import config, i18n, media, toolchain
from dafan.util import ProcessingError, friendly_failure, last_percent


class SettingsTests(helpers.TempCase, unittest.TestCase):
    def test_roundtrip_and_validation(self):
        p = self.tmp / "s.json"
        s = config.Settings(language="en", image_scale=4, max_concurrent=9, jpg_quality=10, video_crf=99)
        config.save_settings(s, p)
        loaded = config.load_settings(p)
        self.assertEqual(loaded.language, "en")
        self.assertEqual(loaded.image_scale, 4)
        self.assertEqual(loaded.max_concurrent, 3)      # clamped
        self.assertEqual(loaded.jpg_quality, 60)        # clamped
        self.assertEqual(loaded.video_crf, 35)          # clamped

    def test_corrupt_and_unknown_keys(self):
        p = self.tmp / "s.json"
        p.write_text("{not json", encoding="utf-8")
        self.assertEqual(config.load_settings(p).language, "ar")
        p.write_text(json.dumps({"language": "en", "bogus": 1, "image_scale": "oops"}), encoding="utf-8")
        s = config.load_settings(p)
        self.assertEqual(s.language, "en")
        self.assertEqual(s.image_scale, 2)

    def test_tile_sizes(self):
        self.assertLess(config.Settings(memory="low").tile_size(), config.Settings(memory="high").tile_size())


class I18nTests(unittest.TestCase):
    def test_both_languages_complete_and_placeholders_match(self):
        for key, (ar, en) in i18n.TEXT.items():
            self.assertTrue(ar.strip() and en.strip(), key)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", ar)), sorted(re.findall(r"\{(\w+)\}", en)), key)

    def test_every_key_used_in_source_exists(self):
        prefixes = ("nav_", "grp_", "lbl_", "btn_", "err_", "st_", "stat_", "preset_", "dn_", "tool_", "msg_",
                    "skip_", "mode_", "vm_", "fmt_", "fps_", "au_", "dev_", "mem_", "est_", "dl_", "dlg_",
                    "cmp_", "compare_", "preview_", "queue_", "kind_")
        used = set()
        for path in (helpers.SRC / "dafan").rglob("*.py"):
            if path.name == "i18n.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "t" and node.args:
                    a = node.args[0]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        used.add(a.value)
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    v = node.value
                    if re.fullmatch(r"[a-z]+(_[a-z0-9]+)+", v) and v.startswith(prefixes):
                        used.add(v)
        dynamic = {f"st_{s}" for s in ("pending", "running", "done", "failed", "cancelled")}
        dynamic |= {f"stat_{s}" for s in ("pending", "running", "done", "failed")}
        dynamic |= {f"preset_{k}" for k in ("general", "portrait", "landscape", "old", "anime", "fast")}
        dynamic |= {f"dn_{k}" for k in ("auto", "none", "light", "medium", "strong")}
        dynamic |= {f"tool_{k}" for k in ("esrgan", "ffmpeg", "models")}
        dynamic |= {"image_sub", "video_sub", "history_sub", "settings_sub", "about_sub",
                    "out_default", "temp_default", "skip_type", "skip_missing", "skip_duplicate",
                    "err_memory", "err_disk", "err_vulkan", "lbl_version", "rights"}
        # strings that merely look like keys but are widget options or file names
        ignore = {"dn_", "dl_done", "preview_err", "est_lbl", "tool_lbls"}  # event names / attribute names
        missing = sorted(k for k in (used | dynamic) - ignore if k not in i18n.TEXT)
        self.assertEqual(missing, [])

    def test_language_switch_and_format(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("added_n", n=3), "3 file(s) added")
        i18n.set_language("ar")
        self.assertIn("3", i18n.t("added_n", n=3))
        self.assertEqual(i18n.t("no_such_key"), "no_such_key")


class MediaTests(helpers.TempCase, unittest.TestCase):
    def test_kinds_and_sizes(self):
        self.assertEqual(media.kind_of("a.JPG"), "image")
        self.assertEqual(media.kind_of("a.tiff"), "image")
        self.assertEqual(media.kind_of("a.webm"), "video")
        self.assertIsNone(media.kind_of("a.txt"))
        self.assertEqual(media.human_size(1536), "1.5 KB")
        self.assertEqual(media.human_time(3725), "1:02:05")
        self.assertEqual(media.human_time(None), "--:--")

    def test_unique_path_and_safe_stem(self):
        (self.tmp / "x.png").write_text("a")
        self.assertEqual(media.unique_path(self.tmp, "x", ".png").name, "x_2.png")
        self.assertEqual(media.safe_stem('a<b>:"c"'), "a_b___c_")

    def test_video_target_sizes(self):
        self.assertEqual(media.video_target_size(1280, 720, "2160p", 0, 0), (3840, 2160, False))
        self.assertEqual(media.video_target_size(720, 1280, "1080p", 0, 0), (1080, 1920, False))
        self.assertEqual(media.video_target_size(640, 480, "1080p", 0, 0), (1440, 1080, False))
        self.assertEqual(media.video_target_size(640, 480, "custom", 1921, 1081), (1920, 1080, True))

    def test_ai_scale_and_target_checks(self):
        self.assertEqual(media.ai_scale_for(1920, 1080, 3840, 2160), 2)
        self.assertEqual(media.ai_scale_for(1280, 720, 3840, 2160), 3)
        self.assertEqual(media.ai_scale_for(640, 360, 3840, 2160), 4)
        info = media.VideoInfo(1920, 1080, Fraction(30), 10, 300, "h264", True, "aac")
        with self.assertRaises(ProcessingError):
            media.check_video_target(info, 1920, 1080)
        with self.assertRaises(ProcessingError):
            media.check_video_target(info, 9000, 5000)
        media.check_video_target(info, 3840, 2160)

    def test_collect_media_files(self):
        (self.tmp / "sub").mkdir()
        for n in ("a.png", "b.txt", "sub/c.mp4", "sub/d.JPEG"):
            (self.tmp / n).write_bytes(b"x")
        found = sorted(p.name for p in media.collect_media_files([self.tmp]))
        self.assertEqual(found, ["a.png", "c.mp4", "d.JPEG"])
        self.assertEqual(len(media.collect_media_files([self.tmp / "b.txt"])), 1)  # reported later as unsupported

    def test_probe_image_and_corrupt(self):
        from PIL import Image
        Image.new("RGB", (30, 20)).save(self.tmp / "a.png")
        self.assertEqual(media.probe_image(self.tmp / "a.png"), (30, 20, "PNG"))
        (self.tmp / "bad.png").write_bytes(b"not an image")
        with self.assertRaises(ProcessingError):
            media.probe_image(self.tmp / "bad.png")


class UtilTests(unittest.TestCase):
    def test_last_percent_and_failure_hints(self):
        self.assertEqual(last_percent("0.00%\n25.00%\n50.50%"), 50.5)
        self.assertIsNone(last_percent("nothing"))
        self.assertEqual(friendly_failure("vkAllocateMemory failed -2"), "err_memory")
        self.assertEqual(friendly_failure("No space left on device"), "err_disk")
        self.assertEqual(friendly_failure("something else"), "")


class ToolchainTests(helpers.TempCase, unittest.TestCase):
    def test_model_files(self):
        self.assertEqual(toolchain.model_files("realesrgan-x4plus", 4)[0], "realesrgan-x4plus.param")
        self.assertEqual(toolchain.model_files("realesr-animevideov3", 2)[1], "realesr-animevideov3-x2.bin")
        self.assertTrue(toolchain.has_native_scale("realesr-animevideov3", 2))
        self.assertFalse(toolchain.has_native_scale("realesrgan-x4plus", 2))

    def test_zip_extraction_flattens_and_blocks_traversal(self):
        z = self.tmp / "t.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("pkg/tool.exe", "x")
            zf.writestr("pkg/models/m.bin", "y")
        target = self.tmp / "out"
        toolchain._extract_zip(z, target, "tool.exe")
        self.assertTrue((target / "tool.exe").is_file())
        self.assertTrue((target / "models" / "m.bin").is_file())
        evil = self.tmp / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("tool.exe", "x")
            zf.writestr("../../escape.txt", "boom")
        with self.assertRaises(ProcessingError):
            toolchain._extract_zip(evil, self.tmp / "out2", "tool.exe")
        self.assertFalse((self.tmp.parent / "escape.txt").exists())
        with self.assertRaises(ProcessingError):
            toolchain._extract_zip(z, self.tmp / "out3", "missing.exe")

    def test_missing_tools_reporting(self):
        tools = helpers.make_tools(self.tmp / "tools")
        tc = toolchain.Toolchain(tools / "realesrgan" / "realesrgan-ncnn-vulkan", None, None)
        self.assertEqual(toolchain.missing_tools("image", tc, [("realesrgan-x4plus", 4)]), [])
        self.assertEqual(toolchain.missing_tools("image", tc, [("nope", 4)]), ["model:nope-x4"])
        self.assertEqual(toolchain.missing_tools("video", tc, []), ["ffmpeg"])
        empty = toolchain.Toolchain(None, None, None)
        self.assertIn("realesrgan", toolchain.missing_tools("image", empty))
        self.assertNotIn("realesrgan", toolchain.missing_tools("video", empty, []))

    def test_manifest_is_valid(self):
        m = toolchain.load_manifest()
        for key in ("realesrgan", "ffmpeg"):
            self.assertTrue(m[key]["url"].startswith("https://github.com/"))


class DownloadTests(helpers.TempCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        import functools
        import http.server
        import threading
        self.payload = b"0123456789" * 50_000
        (self.tmp / "www").mkdir()
        (self.tmp / "www" / "f.bin").write_bytes(self.payload)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(self.tmp / "www"))
        handler.log_message = lambda *a, **k: None
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/f.bin"

    def test_download_progress_and_hash(self):
        import hashlib
        seen = []
        dest = self.tmp / "dl" / "f.bin"
        digest = toolchain.download_file(self.url, dest, lambda d, t: seen.append((d, t)))
        self.assertEqual(digest, hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(dest.read_bytes(), self.payload)
        self.assertEqual(seen[-1], (len(self.payload), len(self.payload)))
        self.assertFalse(dest.with_suffix(".bin.part").exists())

    def test_hash_mismatch_removes_partial(self):
        dest = self.tmp / "dl" / "f.bin"
        with self.assertRaises(ProcessingError):
            toolchain.download_file(self.url, dest, expected_sha256="0" * 64)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_suffix(".bin.part").exists())

    def test_cancel_download(self):
        import threading
        from dafan.util import Cancelled
        ev = threading.Event()
        ev.set()
        with self.assertRaises(Cancelled):
            toolchain.download_file(self.url, self.tmp / "dl" / "f.bin", cancel=ev)
        self.assertFalse((self.tmp / "dl" / "f.bin.part").exists())


if __name__ == "__main__":
    unittest.main()
