"""Model labels read the same way whatever the maker (reported 2026-09-04).

Claude ids were already rendered as "Fable 5.1", but OpenAI ids came out as the whole id
upper-cased: "GPT-6-ASTRA" next to "Fable 5.1" looked like two different dashboards.
The label must come out identical whether it was read from the rollout, from argv, or
from a daemon's config — that is why every path is exercised here.
"""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentsmon import detect


class OpenAIModelLabels(unittest.TestCase):
    def test_codename_after_the_version_is_a_word(self):
        self.assertEqual(detect._pretty_model("gpt-6-astra"), "GPT-6 Astra")
        self.assertEqual(detect._pretty_model("openai/gpt-5.6-sol"), "GPT-5.6 Sol")
        self.assertEqual(detect._pretty_model("gpt-5.6-sol-mini"), "GPT-5.6 Sol Mini")

    def test_plain_versions_keep_the_dash(self):
        self.assertEqual(detect._pretty_model("gpt-5.5"), "GPT-5.5")
        self.assertEqual(detect._pretty_model("gpt-4o"), "GPT-4o")
        self.assertEqual(detect._pretty_model("o3-mini"), "O3 Mini")

    def test_non_openai_ids_are_left_alone(self):
        self.assertEqual(detect._pretty_model("SomeLocalThing"), "SomeLocalThing")

    def test_argv_path_matches_rollout_path(self):
        """`--model gpt-6-astra` on the command line and `"model":"gpt-6-astra"` in the
        rollout head must render the same label — the dashboard flips between the two
        sources during the first minute of a session."""
        argv = detect._model_from_argv(["codex resume 0123 --model gpt-6-astra -c x=y"])
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
            fh.write('{"type":"session_meta","payload":{"cwd":"/home/x"}}\n')
            fh.write('{"type":"turn_context","payload":{"model":"gpt-6-astra","model_provider":"openai"}}\n')
            path = fh.name
        try:
            rollout = detect._rollout_model(path)
        finally:
            os.unlink(path)
        self.assertEqual(argv, "GPT-6 Astra")
        self.assertEqual(rollout, "GPT-6 Astra")

    def test_claude_and_gemini_routes_unchanged(self):
        self.assertEqual(detect.prettify_model("claude-fable-5-1"), "Fable 5.1")
        self.assertEqual(detect.prettify_model("gemini-3-flash"), "Gemini 3 Flash")

    def test_pretty_label_still_colours_as_openai(self):
        self.assertEqual(detect.vendor_for_model("GPT-6 Astra"), "openai")
        self.assertEqual(detect.vendor_for_model("GPT-5.6 Sol"), "openai")


if __name__ == "__main__":
    unittest.main()
