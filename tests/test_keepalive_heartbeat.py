"""The keepalive stamps every completed pass (2026-09-04): a supervisor that quietly stops is
the failure nobody notices, and the stamp is the only evidence it still runs."""
import os, tempfile, time, unittest
from unittest import mock

os.environ.setdefault("AGENTSMON_STATE", tempfile.mkdtemp(prefix="agentsmon-test-state-"))
from agentsmon import keepalive  # noqa: E402


class Heartbeat(unittest.TestCase):
    def test_a_pass_writes_the_stamp(self):
        """Mutation: drop ``_heartbeat()`` from ``run`` → no stamp, module reports the keepalive dead."""
        cfg = {"keepalive": {"enabled": True, "interval_seconds": 60}, "agents": [], "daemons": []}
        p = keepalive.heartbeat_path()
        if p.exists():
            p.unlink()
        with mock.patch.object(keepalive.config, "load", return_value=cfg), \
             mock.patch.object(keepalive, "tick", return_value=0):
            keepalive.run(loop=False)
        self.assertTrue(p.exists(), "no heartbeat after a pass")
        self.assertLess(abs(time.time() - int(p.read_text())), 5)

    def test_disabled_keepalive_leaves_no_stamp(self):
        cfg = {"keepalive": {"enabled": False}, "agents": [], "daemons": []}
        p = keepalive.heartbeat_path()
        if p.exists():
            p.unlink()
        with mock.patch.object(keepalive.config, "load", return_value=cfg):
            keepalive.run(loop=False)
        self.assertFalse(p.exists(), "a disabled keepalive must not pretend to be alive")


if __name__ == "__main__":
    unittest.main()
