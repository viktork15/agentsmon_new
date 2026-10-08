"""Pins local (non-upstream) behaviour carried through the upstream merge."""
import re
import unittest
from pathlib import Path

from agentsmon import detect

SRC = (Path(__file__).resolve().parents[1] / "agentsmon" / "dashboard.py").read_text("utf-8")


class ModelLabels(unittest.TestCase):
    def test_claude_models(self):
        for raw, want in {"claude-sonnet-5-5": "Sonnet 5.5", "claude-haiku-4-5": "Haiku 4.5",
                          "claude-sonnet-5": "Sonnet 5", "claude-opus-5": "Opus 5"}.items():
            self.assertEqual(detect.display_model(raw), want)

    def test_gpt_models_share_one_format(self):
        for raw, want in {"gpt-5.5": "GPT-5.5", "gpt-5.5-codex": "GPT-5.5 Codex",
                          "openai/gpt-5.7-pro": "GPT-5.7 Pro", "o3-pro": "o3 Pro"}.items():
            self.assertEqual(detect.display_model(raw), want)

    def test_ciri_daemon_uses_the_hermes_model_lookup(self):
        orig = detect._hermes_model
        detect._hermes_model = lambda: "gpt-5.5"
        try:
            self.assertEqual(detect.daemon_model("Ciri - Master"), "gpt-5.5")
        finally:
            detect._hermes_model = orig


class DashboardTemplate(unittest.TestCase):
    def _html(self):
        body = re.sub(r"<script(?![^>]*src)[^>]*>.*?</script>", "", SRC, flags=re.S)
        return body[body.index('<div id="toast"'):]

    def test_divs_are_balanced(self):
        # A merge once left the agents section's svc-body wrapper unclosed.
        h = self._html()
        self.assertEqual(len(re.findall(r"<div\b", h)), len(re.findall(r"</div>", h)))

    def test_server_health_and_dark_mode_survive(self):
        self.assertIn('data-svc="server-health"', SRC)
        self.assertIn("body.dark", SRC)
        self.assertIn("refreshHealth", SRC)


if __name__ == "__main__":
    unittest.main()
