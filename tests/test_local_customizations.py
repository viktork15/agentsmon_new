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

    def test_newer_gpt_and_gemini_ids_are_formatted(self):
        self.assertEqual(detect.display_model("gpt-6.1-sol"), "GPT-6.1 Sol")
        self.assertEqual(detect.display_model("gemini-3.5-flash"), "Gemini 3.5 Flash")

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

    def test_service_cards_keep_the_compact_one_row_layout(self):
        tpl = SRC[SRC.index('<template id="svc-tpl">'):SRC.index("</template>")]
        self.assertIn("grid-cols-6", tpl)
        self.assertNotIn("grid-cols-3 gap-3", tpl)
        self.assertIn("svc-body", tpl)          # ...and stays collapsible

    def test_server_health_is_collapsible(self):
        sec = SRC[SRC.index('data-svc="server-health"'):SRC.index('<div id="services">')]
        self.assertIn("svc-head", sec)
        self.assertIn("svc-chev", sec)
        self.assertIn('<div class="svc-body">', sec)
        self.assertIn('initCollapse(root,"server-health"', SRC)

    def test_every_section_header_uses_the_same_small_gap(self):
        # Server Health uses mb-2 under its header; the agents/service headers (rewritten by JS on
        # every poll) must not drift back to the larger mb-3.
        self.assertNotIn("svc-head flex items-center gap-2.5 mb-3", SRC)

    def test_agents_table_has_no_explanatory_caption(self):
        self.assertNotIn("tmux sessions running an agent", SRC)

    def test_server_health_and_dark_mode_survive(self):
        self.assertIn('data-svc="server-health"', SRC)
        self.assertIn("body.dark", SRC)
        self.assertIn("refreshHealth", SRC)


if __name__ == "__main__":
    unittest.main()
