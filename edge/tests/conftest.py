"""Keep the local edge/.env (real credentials, git-ignored) out of the test process."""

from pathlib import Path

import panel.config

panel.config.DOTENV_PATH = Path("/nonexistent/.env")
