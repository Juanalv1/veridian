"""Keep the local edge/.env (real credentials, git-ignored) out of the test process."""

import os
from pathlib import Path

import panel.config

# demo_app.main builds its app at import, and the SDK refuses to start without a key.
os.environ.setdefault("VERIDIAN_API_KEY", "test-key")

panel.config.DOTENV_PATH = Path("/nonexistent/.env")
