import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hyprvault import utils


class ListSessionsOrderTest(unittest.TestCase):
    def test_most_recently_modified_session_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp) / "hyprvault" / "sessions"
            sessions_dir.mkdir(parents=True)
            for name in ("alpha.json", "beta.json", "gamma.json"):
                (sessions_dir / name).write_text("[]")

            # alpha oldest; beta and gamma share the newest mtime so the
            # tie-break (name) is exercised too. Alphabetical order would
            # be alpha, beta, gamma.
            os.utime(sessions_dir / "alpha.json", (1000, 1000))
            os.utime(sessions_dir / "beta.json", (3000, 3000))
            os.utime(sessions_dir / "gamma.json", (3000, 3000))

            with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
                self.assertEqual(utils.list_sessions(), ["beta", "gamma", "alpha"])
