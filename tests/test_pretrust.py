"""Claude Code must not stop on its first-run prompts when it is launched detached into tmux.

Live case 2026-09-03: `agentsmon new` created a Claude agent on a freshly installed server, but
the agent sat on "Is this a project you created or one you trust?" and never appeared on the
dashboard. Each test names the mutation that turns it red.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from agentsmon.wizard import claude_is_logged_in, pretrust_claude


class FirstRunPromptsArePreAnswered(unittest.TestCase):
    def test_records_trust_and_onboarding_on_an_empty_home(self):
        """Mutation: drop the `hasTrustDialogAccepted` assignment → the dialog still blocks."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            zapsano = pretrust_claude("/home/domi", home)
            cfg = json.loads((home / ".claude.json").read_text("utf-8"))
            self.assertTrue(cfg["projects"]["/home/domi"]["hasTrustDialogAccepted"])
            self.assertTrue(cfg["hasCompletedOnboarding"])
            self.assertIn("trusted folder /home/domi", zapsano)

    def test_keeps_everything_else_in_the_config(self):
        """Mutation: write a fresh dict instead of the parsed one → the user loses their config."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".claude.json").write_text(json.dumps({
                "numStartups": 103, "customApiKeyResponses": {"approved": ["abc"]},
                "projects": {"/jiny": {"hasTrustDialogAccepted": True, "lastCost": 1.5}}}), "utf-8")
            pretrust_claude("/home/domi", home)
            cfg = json.loads((home / ".claude.json").read_text("utf-8"))
            self.assertEqual(cfg["numStartups"], 103)
            self.assertEqual(cfg["customApiKeyResponses"], {"approved": ["abc"]})
            self.assertEqual(cfg["projects"]["/jiny"]["lastCost"], 1.5)
            self.assertTrue(cfg["projects"]["/home/domi"]["hasTrustDialogAccepted"])

    def test_is_a_no_op_when_already_trusted(self):
        """Nothing to record → the file must not be touched at all. Written in COMPACT json on
        purpose: a needless rewrite would reformat it to indent=2, so byte equality is what proves
        the write was skipped (comparing the parsed value would pass either way).

        Mutation: drop the `if not zapsano: return ""` guard → the config is rewritten."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            puvodni = '{"hasCompletedOnboarding":true,"projects":{"/home/domi":{"hasTrustDialogAccepted":true}}}'
            (home / ".claude.json").write_text(puvodni, "utf-8")
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(
                '{"skipDangerousModePermissionPrompt":true}', "utf-8")
            self.assertEqual(pretrust_claude("/home/domi", home), "")
            self.assertEqual((home / ".claude.json").read_text("utf-8"), puvodni,
                             "the config was rewritten even though nothing changed")

    def test_records_the_bypass_mode_acknowledgement(self):
        """The third gate lives in a DIFFERENT file (~/.claude/settings.json). Answering only the
        trust dialog still left the agent stuck on a WARNING screen whose default is "No, exit".

        Mutation: drop the `_skip_bypass_warning` call → the agent stops on the warning."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            zapsano = pretrust_claude("/home/domi", home)
            nastaveni = json.loads((home / ".claude" / "settings.json").read_text("utf-8"))
            self.assertTrue(nastaveni["skipDangerousModePermissionPrompt"])
            self.assertIn("bypass-mode warning", zapsano)

    def test_keeps_other_settings_when_recording_the_bypass_acknowledgement(self):
        """Mutation: write a fresh dict → the user loses hooks, model and permissions."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".claude").mkdir()
            (home / ".claude" / "settings.json").write_text(
                json.dumps({"model": "opus", "permissions": {"allow": ["Read"]}}), "utf-8")
            pretrust_claude("/home/domi", home)
            s = json.loads((home / ".claude" / "settings.json").read_text("utf-8"))
            self.assertEqual(s["model"], "opus")
            self.assertEqual(s["permissions"], {"allow": ["Read"]})
            self.assertTrue(s["skipDangerousModePermissionPrompt"])

    def test_never_overwrites_a_config_it_cannot_parse(self):
        """A corrupt or half-written config must be left alone — rewriting it would lock the user
        out of Claude entirely.

        Mutation: treat a parse error as an empty dict → the file is replaced with `{...}`."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".claude.json").write_text("{ this is not json", "utf-8")
            self.assertEqual(pretrust_claude("/home/domi", home), "")
            self.assertEqual((home / ".claude.json").read_text("utf-8"), "{ this is not json")

    def test_config_is_owner_only(self):
        """The file holds API-key approvals and project history.

        Mutation: drop the chmod → it inherits the umask and can be world-readable."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            pretrust_claude("/home/domi", home)
            self.assertEqual((home / ".claude.json").stat().st_mode & 0o077, 0)


class LoginIsTheOneGateWeCannotAnswer(unittest.TestCase):
    """The agent starts fine without credentials but sits at "Not logged in" doing nothing —
    indistinguishable from a broken tool unless we say so."""

    def setUp(self):
        self._env = {k: os.environ.pop(k, None)
                     for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_a_home_without_credentials_is_not_logged_in(self):
        """Mutation: return True unconditionally → the warning never fires."""
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(claude_is_logged_in(Path(d)))

    def test_a_credentials_file_counts_as_logged_in(self):
        """Mutation: ignore the credentials file → every user is nagged to log in again."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").write_text("{}", "utf-8")
            self.assertTrue(claude_is_logged_in(home))

    def test_an_api_key_in_the_environment_counts(self):
        """Mutation: drop the env-var check → API-key users are told they are not logged in."""
        with tempfile.TemporaryDirectory() as d:
            os.environ["ANTHROPIC_API_KEY"] = "sk-test"
            self.assertTrue(claude_is_logged_in(Path(d)))


if __name__ == "__main__":
    unittest.main()
