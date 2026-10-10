import json
import re
import unittest

import helpers

ROOT = helpers.ROOT


class ProjectFilesTests(unittest.TestCase):
    def test_required_files_exist(self):
        for name in ("app.py", "requirements.txt", "README.md", ".gitignore", "Dafan4KEnhancer.spec",
                     ".github/workflows/build-windows.yml", "tools_manifest.json", "THIRD_PARTY_NOTICES.md",
                     "assets/icon.ico", "scripts/fetch_tools.py", "scripts/verify_tools.py", "scripts/package.py"):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_requirements_are_pinned(self):
        for f in ("requirements.txt", "requirements-dev.txt"):
            for line in (ROOT / f).read_text().splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "-r")):
                    self.assertRegex(line, r"^[A-Za-z0-9_.\-]+==\d[\w.]*$", line)

    def test_workflow_references_existing_scripts(self):
        text = (ROOT / ".github/workflows/build-windows.yml").read_text()
        for script in re.findall(r"python (scripts/\w+\.py)", text):
            self.assertTrue((ROOT / script).is_file(), script)
        self.assertIn("upload-artifact", text)
        self.assertIn("--selftest", text)

    def test_spec_bundles_needed_resources(self):
        spec = (ROOT / "Dafan4KEnhancer.spec").read_text()
        for needle in ("customtkinter", "tkinterdnd2", "tools_manifest.json", "assets/icon.ico"):
            self.assertIn(needle, spec)

    def test_manifest_json(self):
        data = json.loads((ROOT / "tools_manifest.json").read_text())
        self.assertEqual(sorted(k for k in data if not k.startswith("_")), ["ffmpeg", "realesrgan"])


if __name__ == "__main__":
    unittest.main()
