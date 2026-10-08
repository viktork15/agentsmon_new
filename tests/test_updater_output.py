"""`agentsmon update` output.

The pull's raw diffstat was printed and cut at 400 characters, so a wide update ended
mid-word ("delet") — which reads like a crash to someone watching a live demo.
"""
import os
import subprocess
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentsmon import updater


class Summary(unittest.TestCase):
    def setUp(self):
        self.orig = subprocess.run

    def tearDown(self):
        subprocess.run = self.orig

    def _stub(self, head_after, changed_files):
        def fake(args, **kw):
            if "rev-parse" in args:
                return types.SimpleNamespace(returncode=0, stdout=head_after + "\n", stderr="")
            if "diff" in args:
                return types.SimpleNamespace(returncode=0, stdout="\n".join(changed_files), stderr="")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        subprocess.run = fake

    def test_reports_the_range_and_the_count(self):
        self._stub("23bf1d2", ["agentsmon/detect.py", "tests/x.py"])
        out = updater._summary("/x", "0138d3b", "")
        self.assertIn("0138d3b", out)
        self.assertIn("23bf1d2", out)
        self.assertIn("2 files changed", out)

    def test_singular_for_one_file(self):
        self._stub("23bf1d2", ["agentsmon/detect.py"])
        self.assertIn("1 file changed", updater._summary("/x", "0138d3b", ""))

    def test_unchanged_head_says_so(self):
        self._stub("0138d3b", [])
        self.assertIn("up to date", updater._summary("/x", "0138d3b", ""))

    def test_never_truncates_mid_word(self):
        """The old code cut at 400 chars; the summary must stay one short line."""
        self._stub("23bf1d2", [f"agentsmon/file{i}.py" for i in range(40)])
        out = updater._summary("/x", "0138d3b", "")
        self.assertLess(len(out), 90)
        self.assertTrue(out.endswith("."))
        self.assertIn("40 files changed", out)
