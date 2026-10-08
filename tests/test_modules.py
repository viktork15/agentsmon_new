"""Local check modules: any executable in ``modules_dir`` is a service card (2026-09-04).

Contract: exit 0 = up; optional JSON on stdout with up / latency_ms / detail; optional header
``# agentsmon: name=…, latency_label=…, timeout_seconds=…``. Hand-written services keep priority,
helpers (``_``/``.`` prefixed, non-executable) are ignored, and the system card judges a module by
its latest recorded sample instead of running it a second time.
"""
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("AGENTSMON_STATE", tempfile.mkdtemp(prefix="agentsmon-test-state-"))
from agentsmon import config, db, probe   # noqa: E402  (state dir must be set before import)


def _exe(path: Path, body: str) -> Path:
    path.write_text(body, "utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class CommandProbe(unittest.TestCase):
    def test_exit_code_decides_and_wall_time_is_the_latency(self):
        ok, lat, detail = probe._command("echo fine")
        self.assertTrue(ok); self.assertIsNotNone(lat); self.assertEqual(detail, "fine")
        ok, _, detail = probe._command("echo broken >&2; exit 3")
        self.assertFalse(ok); self.assertEqual(detail, "broken")

    def test_json_output_overrides_exit_code_latency_and_detail(self):
        ok, lat, detail = probe._command('echo \'{"up": false, "latency_ms": 250, "detail": "tunnel down"}\'')
        self.assertFalse(ok, "JSON up=false must win over exit code 0")
        self.assertEqual(lat, 0.25); self.assertEqual(detail, "tunnel down")
        ok, lat, _ = probe._command('echo \'{"up": true, "latency_ms": 12}\'')
        self.assertTrue(ok); self.assertEqual(lat, 0.012)

    def test_a_hanging_module_is_down_not_a_hung_dashboard(self):
        t0 = time.time()
        ok, lat, detail = probe._command("sleep 5", timeout=0.5)
        self.assertFalse(ok); self.assertIsNone(lat); self.assertIn("timeout", detail)
        self.assertLess(time.time() - t0, 3)


class ModuleDiscovery(unittest.TestCase):
    def test_executables_become_services_with_header_settings(self):
        with tempfile.TemporaryDirectory() as d:
            _exe(Path(d) / "vpn_do_stovky.sh", "#!/bin/sh\n# agentsmon: name=VPN do Stovky, latency_label=round trip, timeout_seconds=7\necho ok\n")
            _exe(Path(d) / "plain-check.py", "#!/usr/bin/env python3\nprint('ok')\n")
            (Path(d) / "_helper.sh").write_text("#!/bin/sh\n", "utf-8")            # helper: underscore
            (Path(d) / "notes.txt").write_text("not a module", "utf-8")            # not executable
            found = config.discover_modules({"modules_dir": d, "services": []})
            names = {m["name"]: m for m in found}
            self.assertEqual(set(names), {"VPN do Stovky", "Plain Check"})
            self.assertEqual(names["VPN do Stovky"]["latency_label"], "round trip")
            self.assertEqual(names["VPN do Stovky"]["timeout_seconds"], 7.0)
            self.assertEqual(names["VPN do Stovky"]["source"], "module")
            self.assertTrue(names["Plain Check"]["command"].endswith("plain-check.py"))

    def test_a_hand_written_service_keeps_priority_over_a_module_of_the_same_name(self):
        with tempfile.TemporaryDirectory() as d:
            _exe(Path(d) / "vpn.sh", "#!/bin/sh\n# agentsmon: name=VPN\necho ok\n")
            cfg = {"modules_dir": d, "services": [{"name": "VPN", "health_url": "http://x/health"}]}
            self.assertEqual(config.discover_modules(cfg), [])

    def test_load_appends_modules_to_services(self):
        """Mutation: drop the ``discover_modules`` call from ``load`` → modules never get a card."""
        with tempfile.TemporaryDirectory() as d:
            _exe(Path(d) / "m.sh", "#!/bin/sh\necho ok\n")
            cfgp = Path(d) / "config.json"
            cfgp.write_text(json.dumps({"modules_dir": d, "services": [{"name": "Real", "process": "x"}]}), "utf-8")
            cfg = config.load(cfgp)
            self.assertEqual([s["name"] for s in cfg["services"]], ["Real", "M"])

    def test_missing_modules_dir_is_fine(self):
        self.assertEqual(config.discover_modules({"modules_dir": "/nonexistent/agentsmon", "services": []}), [])


class ModuleSamples(unittest.TestCase):
    def test_probe_records_the_modules_sample(self):
        with tempfile.TemporaryDirectory() as d:
            _exe(Path(d) / "m.sh", "#!/bin/sh\necho '{\"up\": true, \"latency_ms\": 40, \"detail\": \"tailscale=up\"}'\n")
            cfg = {"modules_dir": d, "services": [], "probe": {"interval_seconds": 60}}
            cfg["services"] = config.discover_modules(cfg)
            probe.probe_once(cfg)
            cur = db.last("M")
            self.assertTrue(cur["up"]); self.assertEqual(cur["latency"], 0.04); self.assertEqual(cur["detail"], "tailscale=up")

    def test_system_card_flags_a_keepalive_that_stopped_passing(self):
        """Mutation: drop the heartbeat check from ``_system_health`` → a dead supervisor is invisible."""
        from agentsmon import keepalive
        p = keepalive.heartbeat_path()
        cfg = {"agents": [], "daemons": [], "pinned_daemons": [], "services": [],
               "probe": {"interval_seconds": 60}, "keepalive": {"enabled": True, "interval_seconds": 60}}
        p.write_text(str(int(time.time())), "utf-8")
        os.utime(p, (time.time() - 3600, time.time() - 3600))       # last pass an hour ago
        up, _, detail = probe._system_health(cfg)
        self.assertFalse(up); self.assertIn("keepalive", detail)
        p.write_text(str(int(time.time())), "utf-8")                    # fresh pass → fine again
        up, _, _ = probe._system_health(cfg)
        self.assertTrue(up)
        cfg["keepalive"]["enabled"] = False                             # disabled → not judged
        os.utime(p, (time.time() - 3600, time.time() - 3600))
        self.assertTrue(probe._system_health(cfg)[0])

    def test_command_detail_leads_and_process_is_mentioned_only_when_down(self):
        with tempfile.TemporaryDirectory() as d:
            _exe(Path(d) / "m.sh", "#!/bin/sh\necho 'Master=up Sol=up'\n")
            cfg = {"services": [{"name": "Bridge X", "process": "definitely-not-running-zzz", "command": str(Path(d) / "m.sh")}],
                   "probe": {"interval_seconds": 60}}
            probe.probe_once(cfg)
            cur = db.last("Bridge X")
            self.assertFalse(cur["up"]); self.assertEqual(cur["detail"], "Master=up Sol=up proc=down")

    def test_system_card_uses_the_latest_sample_instead_of_rerunning_the_module(self):
        """Mutation: remove the ``command`` branch in ``_system_health`` → the module is treated
        like a process pattern (none) and counts as up even when its last sample says down."""
        name = "Down Module " + str(int(time.time() * 1000))
        db.record(name, False, None, "tunnel down")
        cfg = {"agents": [], "daemons": [], "pinned_daemons": [],
               "services": [{"name": name, "command": "exit 0"}], "probe": {"interval_seconds": 60}}
        up, _, detail = probe._system_health(cfg)
        self.assertFalse(up); self.assertIn(name, detail)


if __name__ == "__main__":
    unittest.main()
