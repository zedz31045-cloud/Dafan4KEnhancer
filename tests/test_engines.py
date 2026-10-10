import hashlib
import threading
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401
from PIL import Image

from dafan import engine_image, engine_video, i18n, media, toolchain
from dafan.config import Settings
from dafan.util import Cancelled, ProcessingError

i18n.set_language("en")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EngineBase(helpers.TempCase):
    def setUp(self):
        super().setUp()
        self.tools = helpers.make_tools(self.tmp / "tools")
        patcher = helpers.env_with(DAFAN_TOOLS_DIR=self.tools)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tc = toolchain.locate()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.out = self.tmp / "out"
        self.cancel = threading.Event()
        self.progress_log: list[float] = []


class ImageEngineTests(EngineBase, unittest.TestCase):
    def run_image(self, src, **settings):
        params = engine_image.ImageParams.from_settings(Settings(device="cpu", **settings))
        return engine_image.enhance_image(src, self.out, params, self.tc, self.cancel,
                                          self.progress_log.append, self.work)

    def test_x2_with_x4_model_downsizes_and_keeps_original(self):
        src = self.tmp / "photo.png"
        Image.new("RGB", (40, 30), (200, 10, 10)).save(src)
        before = sha(src)
        out = self.run_image(src, image_scale=2)
        self.assertEqual(Image.open(out).size, (80, 60))
        self.assertEqual(out.name, "photo_dafan_2x.png")
        self.assertEqual(sha(src), before)                       # original untouched
        self.assertEqual(self.progress_log[-1], 1.0)
        self.assertEqual(list(self.work.iterdir()), [])          # temp files cleaned
        self.assertFalse(list(self.out.glob("*.partial*")))

    def test_x4_and_never_overwrites_existing_output(self):
        src = self.tmp / "a.png"
        Image.new("RGB", (10, 10)).save(src)
        first = self.run_image(src, image_scale=4)
        second = self.run_image(src, image_scale=4)
        self.assertEqual(Image.open(first).size, (40, 40))
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())

    def test_native_x2_model_for_fast_preset(self):
        src = self.tmp / "a.png"
        Image.new("RGB", (10, 10)).save(src)
        out = self.run_image(src, image_scale=2, image_preset="fast")
        self.assertEqual(Image.open(out).size, (20, 20))

    def test_formats_alpha_16bit_and_exif(self):
        rgba = self.tmp / "alpha.png"
        Image.new("RGBA", (12, 12), (255, 0, 0, 128)).save(rgba)
        out = self.run_image(rgba, image_scale=2, image_format="jpg")
        im = Image.open(out)
        self.assertEqual((im.format, im.mode), ("JPEG", "RGB"))
        out = self.run_image(rgba, image_scale=2, image_format="webp")
        self.assertEqual(Image.open(out).format, "WEBP")
        out = self.run_image(rgba, image_scale=2, image_format="tiff")
        self.assertEqual(Image.open(out).format, "TIFF")
        # 16-bit grayscale PNG
        import numpy as np
        g16 = self.tmp / "g16.png"
        Image.fromarray((np.arange(100, dtype=np.uint16).reshape(10, 10) * 600)).save(g16)
        self.assertEqual(Image.open(self.run_image(g16, image_scale=2)).size, (20, 20))
        # EXIF orientation 6 (rotate 90): 20x10 stored -> 10x20 displayed
        exif = Image.Exif()
        exif[274] = 6
        jpg = self.tmp / "rot.jpg"
        Image.new("RGB", (20, 10), (1, 2, 3)).save(jpg, exif=exif)
        self.assertEqual(Image.open(self.run_image(jpg, image_scale=2)).size, (20, 40))

    def test_all_input_extensions(self):
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"):
            src = self.tmp / f"x{ext}"
            Image.new("RGB", (8, 8), (9, 9, 9)).save(src)
            out = self.run_image(src, image_scale=2)
            self.assertEqual(Image.open(out).size, (16, 16), ext)

    def test_denoise_path_runs(self):
        import numpy as np
        src = self.tmp / "noisy.png"
        rng = np.random.default_rng(0)
        Image.fromarray(rng.integers(0, 255, (24, 24, 3), dtype=np.uint8)).save(src)
        out = self.run_image(src, image_scale=2, image_preset="old")   # medium denoise
        self.assertEqual(Image.open(out).size, (48, 48))

    def test_errors_are_clear(self):
        src = self.tmp / "a.png"
        Image.new("RGB", (8, 8)).save(src)
        with helpers.env_with(DAFAN_STUB_MODE="oom"):
            with self.assertRaises(ProcessingError) as cm:
                self.run_image(src)
        self.assertIn("Out of memory", str(cm.exception))
        with helpers.env_with(DAFAN_STUB_MODE="crash"):
            with self.assertRaises(ProcessingError) as cm:
                self.run_image(src)
        self.assertIn("code 3", str(cm.exception))
        bad = self.tmp / "bad.png"
        bad.write_bytes(b"garbage")
        with self.assertRaises(ProcessingError):
            self.run_image(bad)
        (self.tools / "realesrgan" / "models" / "realesrgan-x4plus.bin").unlink()
        with self.assertRaises(ProcessingError) as cm:
            self.run_image(src)
        self.assertIn("realesrgan-x4plus", str(cm.exception))
        self.assertEqual(list(self.work.iterdir()), [])          # nothing left behind after failures

    def test_too_large_output_refused(self):
        src = self.tmp / "big.png"
        Image.new("L", (12000, 9000)).save(src)                  # 108 MP * 16 = way over the limit
        with self.assertRaises(ProcessingError) as cm:
            self.run_image(src, image_scale=4)
        self.assertIn("too large", str(cm.exception))

    def test_cancel_stops_process_and_cleans(self):
        src = self.tmp / "a.png"
        Image.new("RGB", (8, 8)).save(src)
        timer = helpers.cancel_after(self.cancel, 0.6)
        self.addCleanup(timer.cancel)
        start = time.time()
        with helpers.env_with(DAFAN_STUB_SLEEP="2"):
            with self.assertRaises(Cancelled):
                self.run_image(src)
        self.assertLess(time.time() - start, 6)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse(list(self.out.glob("*.png")))

    def test_gpu_listing_parses_startup_log(self):
        self.assertEqual(toolchain.list_gpus(self.tc), ["0: Fake GPU"])

    def test_estimate_helpers(self):
        self.assertEqual(engine_image.output_size(100, 50, 4), (400, 200))
        self.assertGreater(engine_image.estimate_output_bytes(100, 100, 4, "png", ".jpg"),
                           engine_image.estimate_output_bytes(100, 100, 4, "jpg", ".png"))


@unittest.skipUnless(helpers.ffmpeg_ok("libx264"), "FFmpeg with libx264 not available")
class VideoEngineTests(EngineBase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.src = helpers.make_video(self.tmp / "clip.mp4")             # 64x48, 10 fps, 2 s, aac audio
        self.info = media.probe_video(self.src, self.tc.ffprobe)

    def run_video(self, **settings):
        params = engine_video.VideoParams.from_settings(Settings(device="cpu", **settings))
        return engine_video.enhance_video(self.src, self.out, self.info, params, self.tc, self.cancel,
                                          self.progress_log.append, self.work)

    def test_probe(self):
        self.assertEqual((self.info.width, self.info.height), (64, 48))
        self.assertEqual(float(self.info.fps), 10.0)
        self.assertTrue(self.info.has_audio)
        self.assertEqual(self.info.audio_codec, "aac")
        self.assertAlmostEqual(self.info.duration, 2.0, delta=0.2)

    def test_classic_mode_is_plain_resize_with_audio(self):
        before = sha(self.src)
        out = self.run_video(video_mode="classic", video_target="1080p", video_crf=30)
        data = helpers.ffprobe_json(out)
        v = next(s for s in data["streams"] if s["codec_type"] == "video")
        self.assertEqual((v["width"], v["height"]), (1440, 1080))
        self.assertTrue(any(s["codec_type"] == "audio" for s in data["streams"]))
        self.assertIn("classic", out.name)
        self.assertEqual(sha(self.src), before)
        self.assertEqual(self.progress_log[-1], 1.0)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_ai_mode_multi_chunk_keeps_frames_and_audio(self):
        out = self.run_video(video_mode="ai", video_target="1080p", chunk_frames=8, video_crf=30)
        data = helpers.ffprobe_json(out)
        v = next(s for s in data["streams"] if s["codec_type"] == "video")
        self.assertEqual((v["width"], v["height"]), (1440, 1080))
        self.assertEqual(helpers.count_frames(out), 20)          # 2 s * 10 fps over three chunks (8+8+4)
        self.assertTrue(any(s["codec_type"] == "audio" for s in data["streams"]))
        self.assertAlmostEqual(float(data["format"]["duration"]), 2.0, delta=0.25)
        self.assertIn("_ai_", out.name)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertTrue(all(b >= a for a, b in zip(self.progress_log, self.progress_log[1:])))  # monotonic

    def test_audio_modes_and_container(self):
        out = self.run_video(video_mode="classic", audio_mode="none", video_crf=30)
        self.assertFalse(any(s["codec_type"] == "audio" for s in helpers.ffprobe_json(out)["streams"]))
        out = self.run_video(video_mode="classic", audio_mode="aac", container="mkv", video_crf=30)
        self.assertTrue(out.name.endswith(".mkv"))
        self.assertTrue(any(s["codec_type"] == "audio" for s in helpers.ffprobe_json(out)["streams"]))

    def test_opus_audio_falls_back_to_aac_for_mp4(self):
        if not helpers.ffmpeg_ok("libopus"):
            self.skipTest("libopus encoder not available")
        webm = helpers.make_video(self.tmp / "o.webm", vcodec="libvpx-vp9", acodec="libopus")
        self.src = webm
        self.info = media.probe_video(webm, self.tc.ffprobe)
        out = self.run_video(video_mode="classic", audio_mode="copy", container="mp4", video_crf=30)
        a = next(s for s in helpers.ffprobe_json(out)["streams"] if s["codec_type"] == "audio")
        self.assertEqual(a["codec_name"], "aac")

    def test_fps_change_and_custom_size_with_padding(self):
        out = self.run_video(video_mode="classic", video_target="custom", custom_w=320, custom_h=320,
                             video_fps="25", video_crf=30)
        v = next(s for s in helpers.ffprobe_json(out)["streams"] if s["codec_type"] == "video")
        self.assertEqual((v["width"], v["height"]), (320, 320))
        self.assertEqual(v["r_frame_rate"], "25/1")

    def test_bitrate_mode(self):
        out = self.run_video(video_mode="classic", video_bitrate_kbps=500)
        self.assertTrue(out.exists())

    def test_target_not_larger_and_too_big_rejected(self):
        with self.assertRaises(ProcessingError) as cm:
            self.run_video(video_mode="classic", video_target="custom", custom_w=64, custom_h=48)
        self.assertIn("not smaller", str(cm.exception))
        self.assertEqual(list(self.work.iterdir()), [])

    def test_ai_failure_reports_and_cleans(self):
        with helpers.env_with(DAFAN_STUB_MODE="oom"):
            with self.assertRaises(ProcessingError) as cm:
                self.run_video(video_mode="ai", chunk_frames=8)
        self.assertIn("Out of memory", str(cm.exception))
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse(list(self.out.glob("*")))

    def test_cancel_ai_mid_run_leaves_nothing(self):
        timer = helpers.cancel_after(self.cancel, 1.5)
        self.addCleanup(timer.cancel)
        with helpers.env_with(DAFAN_STUB_SLEEP="0.4"):
            with self.assertRaises(Cancelled):
                self.run_video(video_mode="ai", chunk_frames=8)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse(list(self.out.glob("*")))

    def test_missing_model_for_ai(self):
        (self.tools / "realesrgan" / "models" / "realesr-animevideov3-x4.param").unlink()
        with self.assertRaises(ProcessingError) as cm:
            self.run_video(video_mode="ai", video_target="2160p")
        self.assertIn("realesr-animevideov3", str(cm.exception))

    def test_helpers(self):
        p = engine_video.VideoParams.from_settings(Settings())
        self.assertIn("libx264", engine_video.video_encode_args(p))
        p.codec, p.bitrate_kbps = "h265", 4000
        args = engine_video.video_encode_args(p)
        self.assertIn("libx265", args)
        self.assertIn("4000k", args)
        self.assertEqual(engine_video._concat_line(Path("/a/it's.mkv")), "file '/a/it'\\''s.mkv'\n")


if __name__ == "__main__":
    unittest.main()
