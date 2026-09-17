from __future__ import annotations

from pathlib import Path

from agently import Agently
from dotenv import find_dotenv, load_dotenv


PACKAGE_ROOT = Path(__file__).parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def load_model_settings() -> None:
    project_env = PROJECT_ROOT / ".env"
    load_dotenv(
        project_env if project_env.is_file() else find_dotenv(usecwd=True)
    )
    Agently.load_settings(
        "yaml_file",
        str(PACKAGE_ROOT / "model_settings.yaml"),
        auto_load_env=True,
        raise_empty=True,
    )

