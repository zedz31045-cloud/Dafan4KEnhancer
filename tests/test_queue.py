import time
import unittest

import helpers  # noqa: F401
from PIL import Image

from dafan import i18n
from dafan.config import Settings
from dafan.history import History
from dafan.jobs import QueueManager, Status

i18n.set_language("en")


def wait_for(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class QueueTests(helpers.TempCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tools = helpers.make_tools(self.tmp / "tools")
        p = helpers.env_with(DAFAN_TOOLS_DIR=self.tools)
        p.start()
        self.addCleanup(p.stop)
        self.settings = Settings(device="cpu", output_dir=str(self.tmp / "out"),
                                 temp_dir=str(self.tmp / "work"), image_scale=2)
        self.mgr = QueueManager(self.settings, History(self.tmp / "hist.jsonl"))

    def img(self, name="a.png", size=(10, 10)):
        p = self.tmp / name
        Image.new("RGB", size, (5, 5, 5)).save(p)
        return p

    def test_add_rejects_bad_and_duplicate(self):
        a = self.img()
        (self.tmp / "t.txt").write_text("x")
        added, skipped = self.mgr.add_paths([a, a, self.tmp / "t.txt", self.tmp / "nope.png"])
        self.assertEqual(len(added), 1)
        self.assertEqual(sorted(r for _, r in skipped), ["skip_duplicate", "skip_missing", "skip_type"])
        self.assertTrue(wait_for(lambda: added[0].width == 10))
        self.assertEqual(added[0].fmt, "PNG")

    def test_corrupt_image_is_flagged_not_crashing(self):
        bad = self.tmp / "bad.png"
        bad.write_bytes(b"junk")
        (job,), _ = self.mgr.add_paths([bad])
        self.assertTrue(wait_for(lambda: job.probe_error != ""))
        self.mgr.start_all()
        self.assertTrue(wait_for(lambda: job.status == Status.FAILED))
        self.assertTrue(job.message)

    def test_process_all_with_stats_history_and_no_double_run(self):
        files = [self.img(f"i{n}.png") for n in range(4)]
        self.mgr.settings.max_concurrent = 3
        jobs, _ = self.mgr.add_paths(files)
        self.mgr.start_all(self.mgr.settings)
        self.mgr.start_all(self.mgr.settings)          # accidental second press
        self.mgr.start_all(self.mgr.settings)
        self.assertTrue(wait_for(lambda: self.mgr.stats()[Status.DONE] == 4))
        outs = [j.output for j in jobs]
        self.assertEqual(len(set(outs)), 4)             # each file produced exactly one distinct output
        self.assertEqual(len(list((self.tmp / "out").glob("*"))), 4)
        self.assertEqual(sum(1 for r in self.mgr.history.load() if r["status"] == "done"), 4)
        self.assertTrue(wait_for(lambda: not self.mgr.has_running()))
        # finished files may be added again
        again, skipped = self.mgr.add_paths([files[0]])
        self.assertEqual((len(again), len(skipped)), (1, 0))

    def test_failure_then_retry(self):
        a = self.img()
        (job,), _ = self.mgr.add_paths([a])
        with helpers.env_with(DAFAN_STUB_MODE="oom"):
            self.mgr.start_all()
            self.assertTrue(wait_for(lambda: job.status == Status.FAILED))
        self.assertIn("Out of memory", job.message)
        self.assertEqual(self.mgr.retry_failed(), 1)
        self.assertEqual(job.status, Status.PENDING)
        self.assertTrue(wait_for(lambda: not self.mgr._threads or not any(t.is_alive() for t in self.mgr._threads)))
        self.mgr.start_all()
        self.assertTrue(wait_for(lambda: job.status == Status.DONE))
        self.assertTrue(job.output.exists())

    def test_cancel_current_then_queue_continues(self):
        a, b = self.img("a.png"), self.img("b.png")
        ja, jb = self.mgr.add_paths([a, b])[0]
        with helpers.env_with(DAFAN_STUB_SLEEP="3"):
            self.mgr.start_all()
            self.assertTrue(wait_for(lambda: ja.status == Status.RUNNING))
            time.sleep(0.5)
            self.mgr.cancel_current()
            self.assertTrue(wait_for(lambda: ja.status == Status.CANCELLED, 15))
            self.mgr.stop_all()
            self.assertTrue(wait_for(lambda: not self.mgr.has_running(), 15))
        self.assertIn(jb.status, (Status.PENDING, Status.CANCELLED))

    def test_stop_all_keeps_pending_and_cancel_pending(self):
        a, b = self.img("a.png"), self.img("b.png")
        ja, jb = self.mgr.add_paths([a, b])[0]
        self.mgr.cancel(jb.id)
        self.assertEqual(jb.status, Status.CANCELLED)
        self.assertTrue(self.mgr.retry(jb.id))
        self.assertEqual(jb.status, Status.PENDING)
        self.assertTrue(self.mgr.remove(jb.id))
        self.assertEqual(len(self.mgr.jobs), 1)

    def test_missing_tools_fail_with_clear_message(self):
        import shutil
        shutil.rmtree(self.tools / "realesrgan")
        (job,), _ = self.mgr.add_paths([self.img()])
        self.mgr.start_all()
        self.assertTrue(wait_for(lambda: job.status == Status.FAILED))
        self.assertIn("realesrgan", job.message.lower())

    def test_unwritable_output_dir(self):
        blocker = self.tmp / "file_not_dir"
        blocker.write_text("x")
        self.mgr.settings.output_dir = str(blocker / "sub")
        (job,), _ = self.mgr.add_paths([self.img()])
        self.mgr.start_all(self.mgr.settings)
        self.assertTrue(wait_for(lambda: job.status == Status.FAILED))
        self.assertIn("Cannot write", job.message)

    def test_summary_estimates(self):
        (job,), _ = self.mgr.add_paths([self.img(size=(100, 100))])
        self.assertTrue(wait_for(lambda: job.width == 100))
        files, est, unknown = self.mgr.summary(self.mgr.settings)
        self.assertEqual((files, unknown), (1, 0))
        self.assertGreater(est, 0)

    @unittest.skipUnless(helpers.ffmpeg_ok("libx264"), "FFmpeg not available")
    def test_video_job_end_to_end(self):
        vid = helpers.make_video(self.tmp / "v.mp4")
        self.mgr.settings.video_mode = "classic"
        self.mgr.settings.video_target = "1080p"
        self.mgr.settings.video_crf = 30
        (job,), _ = self.mgr.add_paths([vid])
        self.assertTrue(wait_for(lambda: job.video_info is not None))
        self.assertEqual((job.width, job.height), (64, 48))
        self.mgr.start_all(self.mgr.settings)
        self.assertTrue(wait_for(lambda: job.status in (Status.DONE, Status.FAILED), 60))
        self.assertEqual(job.status, Status.DONE, job.message)
        self.assertEqual((job.out_w, job.out_h), (1440, 1080))


class HistoryTests(helpers.TempCase, unittest.TestCase):
    def test_add_load_trim_clear(self):
        h = History(self.tmp / "h.jsonl")
        for i in range(5):
            h.add(file=f"f{i}", status="done")
        rows = h.load()
        self.assertEqual(rows[0]["file"], "f4")                  # newest first
        (self.tmp / "h.jsonl").write_text("{bad\n" + (self.tmp / "h.jsonl").read_text())
        self.assertEqual(len(h.load()), 5)                       # corrupt line skipped
        h.clear()
        self.assertEqual(h.load(), [])


if __name__ == "__main__":
    unittest.main()
