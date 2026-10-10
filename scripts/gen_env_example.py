"""Writes .env.example from the settings registry (forge/settings.py). Run: uv run python scripts/gen_env_example.py"""

from pathlib import Path

from forge.settings import env_example

if __name__ == "__main__":
    path = Path(__file__).resolve().parents[1] / ".env.example"
    path.write_text(env_example())
    print(f"wrote {path}")
