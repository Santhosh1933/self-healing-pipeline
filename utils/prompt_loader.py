"""Load and render externalized YAML prompts."""

from pathlib import Path
from typing import Any
import yaml
from jinja2 import Environment, StrictUndefined


class PromptLoader:
    """Render named system and human prompts from a YAML file."""

    def __init__(self, path: Path | None = None) -> None:
        """Load prompt definitions from ``path``."""
        prompt_path = path or Path(__file__).parents[1] / "config" / "prompts.yaml"
        self._prompts: dict[str, dict[str, str]] = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
        self._environment = Environment(undefined=StrictUndefined, autoescape=False)

    def render(self, key: str, **values: Any) -> tuple[str, str]:
        """Render and return the system and human prompt for ``key``."""
        try:
            prompts = self._prompts[key]
            return tuple(self._environment.from_string(prompts[name]).render(**values) for name in ("system", "human_template"))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Unknown or invalid prompt: {key}") from exc
