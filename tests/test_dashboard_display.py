"""Display regressions reported on a live server, 2026-09-01.

1. The SAME model rendered a green tag as a pinned daemon and a grey one as a tmux
   agent, because only daemons fell back to the model name when deriving the vendor.
2. The Session ID column showed "— none" for an agent whose id we do know: it sits in
   the configured resume command.

Both are display-only, but a dashboard that shows the same thing two different ways is
a dashboard people stop trusting.
"""
import sys, os, types, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agentsmon import detect, dashboard

SID = "01a05e2f-ebcd-7a22-bd6e-6e0a449f6395"
RESTART = f"codex --dangerously-bypass-approvals-and-sandbox resume {SID}"


class VendorColour(unittest.TestCase):
    def test_model_maps_to_maker(self):
        self.assertEqual(detect.vendor_for_model("GPT-5.6-SOL"), "openai")
        self.assertEqual(detect.vendor_for_model("Claude Opus 5"), "anthropic")
        self.assertEqual(detect.vendor_for_model("Gemini 3.5 Flash"), "google")
        self.assertIsNone(detect.vendor_for_model(None))

    def test_builtin_kind_still_wins(self):
        """A recognised kind keeps its own colour even if the label says otherwise."""
        self.assertEqual(detect.KIND_VENDOR.get("codex"), "openai")
        self.assertEqual(detect.KIND_VENDOR.get("claude-code"), "anthropic")

    def test_same_model_same_colour_daemon_vs_tmux(self):
        """The reported bug: identical model, two different tag colours.

        Both rows now go through vendor_for_agent(), so this fails the moment the two
        paths drift apart again.
        """
        model = "GPT-5.6-SOL"
        daemon = detect.vendor_for_agent(None, model)          # pinned daemon row
        tmux = detect.vendor_for_agent("custom-match", model)  # user-matched tmux row
        self.assertEqual(daemon, tmux)
        self.assertEqual(tmux, "openai")

    def test_unknown_kind_and_unknown_model_stays_uncoloured(self):
        self.assertIsNone(detect.vendor_for_agent("custom-match", "SomeLocalThing"))

    def test_builtin_kind_beats_a_misleading_label(self):
        self.assertEqual(detect.vendor_for_agent("claude-code", "GPT-5.6-SOL"), "anthropic")


class SessionIdFallback(unittest.TestCase):
    def _state(self, discovered):
        cfg = {"agents": [{"name": "Domi - Master", "restart": RESTART, "tag": "GPT-5.6-SOL"}],
               "pinned_daemons": []}
        orig = detect.discover_agents
        detect.discover_agents = lambda *a, **k: discovered
        try:
            return {a["name"]: a for a in dashboard._agents_state(cfg)}
        finally:
            detect.discover_agents = orig

    def test_id_taken_from_resume_when_process_lost_it(self):
        agents = self._state([{"name": "Domi - Master", "kind": "codex", "label": "GPT-5.6-SOL",
                               "session_id": None, "vendor": "openai", "alive": True,
                               "age": 60, "resume_cmd": None, "pids": [1]}])
        self.assertEqual(agents["Domi - Master"]["session_id"], SID)

    def test_detected_id_is_not_overwritten(self):
        jiny = "ffffffff-1111-2222-3333-444444444444"
        agents = self._state([{"name": "Domi - Master", "kind": "codex", "label": "GPT-5.6-SOL",
                               "session_id": jiny, "vendor": "openai", "alive": True,
                               "age": 60, "resume_cmd": None, "pids": [1]}])
        self.assertEqual(agents["Domi - Master"]["session_id"], jiny)

    def test_no_uuid_in_restart_stays_none(self):
        cfg = {"agents": [{"name": "X", "restart": "codex resume"}], "pinned_daemons": []}
        orig = detect.discover_agents
        detect.discover_agents = lambda *a, **k: [{"name": "X", "kind": "codex", "label": "m",
                                                   "session_id": None, "vendor": None, "alive": True,
                                                   "age": 1, "resume_cmd": None, "pids": [1]}]
        try:
            got = {a["name"]: a for a in dashboard._agents_state(cfg)}
        finally:
            detect.discover_agents = orig
        self.assertIsNone(got["X"]["session_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class DashboardUrl(unittest.TestCase):
    """`agentsmon status` has to tell you where the report lives — the URL used to be
    printed once by the wizard and never again."""

    def setUp(self):
        from agentsmon import status
        self.status = status

    def test_exposed_host_shows_both_urls(self):
        out = "\n".join(self.status._dashboard_lines(
            {"dashboard": {"host": "5.10.251.171", "port": 8765}}))
        self.assertIn("http://5.10.251.171:8765", out)
        self.assertIn("local: http://127.0.0.1:8765", out)

    def test_localhost_only_shows_one(self):
        out = "\n".join(self.status._dashboard_lines(
            {"dashboard": {"host": "127.0.0.1", "port": 9000}}))
        self.assertIn("http://127.0.0.1:9000", out)
        self.assertNotIn("local:", out)

    def test_wildcard_host_resolves_to_a_real_address(self):
        out = "\n".join(self.status._dashboard_lines(
            {"dashboard": {"host": "0.0.0.0", "port": 8765}}))
        self.assertNotIn("0.0.0.0", out)

    def test_auth_is_mentioned(self):
        out = "\n".join(self.status._dashboard_lines(
            {"dashboard": {"host": "1.2.3.4", "port": 8765, "auth": {"user": "domi"}}}))
        self.assertIn("domi", out)

    def test_missing_config_falls_back_to_defaults(self):
        out = "\n".join(self.status._dashboard_lines({}))
        self.assertIn("8765", out)

    def test_probe_targets_the_bound_interface(self):
        """A dashboard bound to one specific IP must not be reported dead just because
        nothing listens on 127.0.0.1 (the setup on the machine this was written on)."""
        import socket
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(1)
        try:
            out = "\n".join(self.status._dashboard_lines(
                {"dashboard": {"host": "127.0.0.1", "port": port}}))
            self.assertIn("🟢", out)
            self.assertNotIn("not responding", out)
        finally:
            srv.close()


class ModelFromArgv(unittest.TestCase):
    """A freshly started Claude Code agent has no transcript yet, so the model has to come
    off the command line — otherwise the dashboard shows a bare 'Claude Code' for minutes."""

    def test_fable_is_recognised(self):
        self.assertEqual(
            detect._model_from_argv(["claude --resume abc --model claude-fable-5-1"]),
            "Fable 5.1")

    def test_opus_and_sonnet(self):
        self.assertEqual(detect._model_from_argv(["claude --model claude-opus-5"]), "Opus 5")
        self.assertEqual(detect._model_from_argv(["claude --model claude-sonnet-5"]), "Sonnet 5")

    def test_equals_form(self):
        self.assertEqual(detect._model_from_argv(["claude --model=claude-opus-4-8"]), "Opus 4.8")

    def test_quoted_value(self):
        self.assertEqual(detect._model_from_argv(['claude --model "claude-fable-5-1"']), "Fable 5.1")

    def test_no_flag_returns_none(self):
        self.assertIsNone(detect._model_from_argv(["claude --resume abc"]))
        self.assertIsNone(detect._model_from_argv([]))

    def test_unknown_id_passes_through(self):
        self.assertEqual(detect._model_from_argv(["claude --model something-new"]), "something-new")

    def test_scans_every_command_in_the_tree(self):
        self.assertEqual(
            detect._model_from_argv(["bash -lc wrapper", "claude --model claude-fable-5-1"]),
            "Fable 5.1")


class ClaudeLabelDecision(unittest.TestCase):
    """The whole decision path, so removing the argv fallback fails the suite."""

    def test_transcript_wins_over_argv(self):
        self.assertEqual(detect.label_for_claude(
            "Opus 5", ["claude --model claude-fable-5-1"], "Claude Code"), "Opus 5")

    def test_argv_used_when_transcript_is_silent(self):
        self.assertEqual(detect.label_for_claude(
            None, ["claude --model claude-fable-5-1"], "Claude Code"), "Fable 5.1")

    def test_generic_label_only_as_last_resort(self):
        self.assertEqual(detect.label_for_claude(
            None, ["claude --resume abc"], "Claude Code"), "Claude Code")


class FreshSessionId(unittest.TestCase):
    """Session id must be there from the first second. It used to be read by scanning
    transcript CONTENT for a matching `cwd`, which only appears once the session has had a
    user message — so a young agent showed "— none" for minutes."""

    def setUp(self):
        import tempfile, pathlib
        self.tmp = tempfile.mkdtemp()
        self.home = pathlib.Path(self.tmp)
        self._orig = detect.Path.home
        detect.Path.home = staticmethod(lambda: self.home)

    def tearDown(self):
        detect.Path.home = self._orig
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def _transcript(self, cwd, sid, lines):
        d = self.home / ".claude" / "projects" / cwd.replace("/", "-")
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return f

    def test_project_dir_mapping(self):
        names = [d.name for d in detect.claude_project_dirs("/Users/asistent/dev/app")]
        self.assertIn("-Users-asistent-dev-app", names)

    def test_symlinked_path_tries_both_forms(self):
        """On macOS /home/x resolves elsewhere; the literal form must still be tried."""
        names = [d.name for d in detect.claude_project_dirs("/home/domi")]
        self.assertIn("-home-domi", names)

    def test_id_found_before_any_user_message(self):
        """Only a startup record — no `cwd` anywhere yet."""
        sid = "11111111-2222-3333-4444-555555555555"
        self._transcript("/home/domi", sid,
                         ['{"type":"permission-mode","permissionMode":"default","sessionId":"%s"}' % sid])
        got_sid, got_model = detect._claude_info_for_cwd("/home/domi")
        self.assertEqual(got_sid, sid)
        self.assertIsNone(got_model)   # no assistant turn yet → model comes from argv

    def test_model_read_once_the_session_answers(self):
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self._transcript("/home/domi", sid, [
            '{"type":"permission-mode","sessionId":"%s"}' % sid,
            '{"type":"assistant","message":{"model":"claude-fable-5-1"}}'])
        got_sid, got_model = detect._claude_info_for_cwd("/home/domi")
        self.assertEqual(got_sid, sid)
        self.assertEqual(got_model, "Fable 5.1")

    def test_two_agents_in_one_cwd_get_different_ids(self):
        import time
        a = "aaaaaaaa-1111-2222-3333-444444444444"
        b = "bbbbbbbb-1111-2222-3333-444444444444"
        fa = self._transcript("/home/domi", a, ['{"type":"permission-mode"}'])
        time.sleep(0.02)
        fb = self._transcript("/home/domi", b, ['{"type":"permission-mode"}'])
        first, _ = detect._claude_info_for_cwd("/home/domi")
        second, _ = detect._claude_info_for_cwd("/home/domi", {first})
        self.assertNotEqual(first, second)
        self.assertEqual({first, second}, {a, b})

    def test_unknown_cwd_returns_nothing(self):
        self.assertEqual(detect._claude_info_for_cwd("/nope/nowhere"), (None, None))


class AntigravityPresence(unittest.TestCase):
    """A brand-new `agy` has no entry in cache/last_conversations.json yet — that file is
    written when the conversation is persisted. The presence lock it holds open is named
    after the conversation and exists from startup."""

    def test_id_read_from_the_lock_a_process_holds(self):
        orig = detect.open_files
        detect.open_files = lambda pids: [
            "/home/domi/.gemini/antigravity-cli/log/cli.log",
            "/home/domi/.gemini/antigravity-cli/presence/3d06ea18-306c-47f4-a173-3ed9a452dc05.lock",
        ]
        try:
            self.assertEqual(detect._antigravity_sid_from_presence([1]),
                             "3d06ea18-306c-47f4-a173-3ed9a452dc05")
        finally:
            detect.open_files = orig

    def test_no_lock_means_no_id(self):
        orig = detect.open_files
        detect.open_files = lambda pids: ["/tmp/whatever.log"]
        try:
            self.assertIsNone(detect._antigravity_sid_from_presence([1]))
        finally:
            detect.open_files = orig

    def test_a_lock_without_a_uuid_is_ignored(self):
        orig = detect.open_files
        detect.open_files = lambda pids: ["/x/presence/not-a-uuid.lock"]
        try:
            self.assertIsNone(detect._antigravity_sid_from_presence([1]))
        finally:
            detect.open_files = orig

    def test_open_files_survives_a_missing_lsof(self):
        """Must degrade to an empty list, never raise — it is only ever a fallback."""
        self.assertEqual(detect.open_files([]), [])
        self.assertIsInstance(detect.open_files([999999999]), list)


class AntigravityDecisionAndParsing(unittest.TestCase):
    """The wiring and the lsof parsing, so breaking either one fails the suite."""

    LOCK = "/h/.gemini/antigravity-cli/presence/3d06ea18-306c-47f4-a173-3ed9a452dc05.lock"

    def test_map_wins_when_present(self):
        orig = detect.open_files
        detect.open_files = lambda pids: [self.LOCK]
        try:
            self.assertEqual(detect.antigravity_sid("aaaaaaaa-1111-2222-3333-444444444444", [1]),
                             "aaaaaaaa-1111-2222-3333-444444444444")
        finally:
            detect.open_files = orig

    def test_presence_used_when_map_is_empty(self):
        orig = detect.open_files
        detect.open_files = lambda pids: [self.LOCK]
        try:
            self.assertEqual(detect.antigravity_sid(None, [1]),
                             "3d06ea18-306c-47f4-a173-3ed9a452dc05")
        finally:
            detect.open_files = orig

    def test_lsof_field_output_is_parsed(self):
        """`lsof -Fn` prints one field per line, names prefixed with n."""
        import types
        orig_run, orig_which = detect._run, detect.shutil.which
        detect.shutil.which = lambda x: "/usr/sbin/lsof"
        detect._run = lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="p755\nfcwd\nn/Users/me\nftxt\nn" + self.LOCK + "\n", stderr="")
        try:
            got = detect.open_files([755])
            self.assertIn(self.LOCK, got)
            self.assertIn("/Users/me", got)
            self.assertNotIn("p755", got)
        finally:
            detect._run, detect.shutil.which = orig_run, orig_which


class DiscoverAgentsWiring(unittest.TestCase):
    """End-to-end through discover_agents, so the call sites are covered too — testing the
    decision helpers alone left the one-line wiring free to be deleted silently."""

    LOCK = "/h/.gemini/antigravity-cli/presence/3d06ea18-306c-47f4-a173-3ed9a452dc05.lock"

    def setUp(self):
        self.orig = {n: getattr(detect, n) for n in
                     ("tmux_sessions", "_pane_pids", "_proc_table", "_codex_model",
                      "_session_cwd", "open_files", "_antigravity_info_for_cwd")}
        detect.tmux_sessions = lambda: [{"name": "Domi - Agy", "created": 0}]
        detect._pane_pids = lambda name: [42]
        detect._proc_table = lambda: ({42: "agy --dangerously-skip-permissions"}, {})
        detect._codex_model = lambda: None
        detect._session_cwd = lambda name: "/home/domi"
        detect.open_files = lambda pids: [self.LOCK]
        # The workspace map has nothing yet — the situation right after launch.
        detect._antigravity_info_for_cwd = lambda cwd: (None, None)

    def tearDown(self):
        for n, f in self.orig.items():
            setattr(detect, n, f)

    def test_fresh_agy_still_gets_its_conversation_id(self):
        agents = {a["name"]: a for a in detect.discover_agents(now=1000)}
        self.assertEqual(agents["Domi - Agy"]["session_id"],
                         "3d06ea18-306c-47f4-a173-3ed9a452dc05")


class AntigravityModel(unittest.TestCase):
    """The model for an Antigravity row. Two silent failures lived here: the conversation
    store was opened in a mode that cannot work on a LIVE (WAL) database — i.e. on every
    running agent, the only kind we ask about — and the id was needed before it was known."""

    def setUp(self):
        import tempfile, pathlib, sqlite3
        self.tmp = tempfile.mkdtemp()
        base = pathlib.Path(self.tmp) / ".gemini" / "antigravity-cli"
        (base / "conversations").mkdir(parents=True)
        self.sid = "3d06ea18-306c-47f4-a173-3ed9a452dc05"
        db = base / "conversations" / f"{self.sid}.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE gen_metadata (idx INTEGER, data BLOB, size INTEGER)")
        con.execute("INSERT INTO gen_metadata VALUES (1, ?, 0)", (b'{"m":"gemini-backup"}',))
        con.execute("INSERT INTO gen_metadata VALUES (2, ?, 0)",
                    (b'{"model":"gemini-3.6-flash-medium"}',))
        con.execute("PRAGMA journal_mode=WAL")   # how a live store actually looks
        con.commit(); con.close()
        self.orig = detect._antigravity_base
        detect._antigravity_base = lambda: base

    def tearDown(self):
        detect._antigravity_base = self.orig
        import shutil; shutil.rmtree(self.tmp, ignore_errors=True)

    def test_model_read_from_a_live_wal_database(self):
        self.assertEqual(detect._antigravity_model_from_db(self.sid), "Gemini 3.6 Flash")

    def test_junk_strings_are_not_mistaken_for_models(self):
        """`gemini-backup` and `gemini-20260425-144749` both matched the old pattern."""
        self.assertIsNone(detect._model_like("gemini-backup"))
        self.assertIsNone(detect._model_like("gemini-20260425-144749"))
        self.assertEqual(detect._model_like("gemini-3.6-flash-medium"), "gemini-3.6-flash-medium")

    def test_label_order_settings_then_db_then_argv(self):
        self.assertEqual(detect.antigravity_label("Gemini 3.7 Flash", self.sid, [], "Antigravity"),
                         "Gemini 3.7 Flash")
        self.assertEqual(detect.antigravity_label(None, self.sid, [], "Antigravity"),
                         "Gemini 3.6 Flash")
        self.assertEqual(detect.antigravity_label(None, None, ["agy --model gemini-3-flash"],
                                                  "Antigravity"), "Gemini 3 Flash")
        self.assertEqual(detect.antigravity_label(None, None, ["agy"], "Antigravity"),
                         "Antigravity")
