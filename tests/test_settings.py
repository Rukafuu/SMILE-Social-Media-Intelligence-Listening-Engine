import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.settings import load_local_env


class SettingsTests(unittest.TestCase):
    def test_loads_supported_key_without_overriding_process(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("MASTODON_TOKEN=file-token\nUNSUPPORTED=value\n", encoding="utf-8")
            with patch.dict(os.environ, {"MASTODON_BASE_URL": "https://existing.example"}, clear=True):
                load_local_env(str(env_file))
                self.assertEqual("file-token", os.environ["MASTODON_TOKEN"])
                self.assertEqual("https://existing.example", os.environ["MASTODON_BASE_URL"])
                self.assertNotIn("UNSUPPORTED", os.environ)


if __name__ == "__main__":
    unittest.main()
