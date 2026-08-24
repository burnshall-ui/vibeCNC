import os
import shutil

import yaml

EXAMPLE_NAME = "config.example.yaml"


class SettingsManager:
    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(self.path):
            self._create_from_example()
        with open(self.path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}

    def _create_from_example(self):
        """Seeds config.yaml from the template on first start.

        config.yaml is not under version control — the settings dialog writes
        it, so keeping it tracked meant every save produced a diff carrying one
        machine's paths and limits. A fresh clone has only the template.
        """
        example = os.path.join(os.path.dirname(self.path) or ".", EXAMPLE_NAME)
        if not os.path.exists(example):
            raise FileNotFoundError(
                f"Neither {self.path} nor {example} found — cannot start without a configuration."
            )
        shutil.copyfile(example, self.path)
        print(f"[Config] {os.path.basename(self.path)} created from {EXAMPLE_NAME}")

    def colors(self):
        ui = self.data.get('ui', {})
        return {
            "BG_DARK": ui.get("dark_bg", "#1A1A1A"),
            "BG_BLACK": ui.get("black_bg", "#000000"),
            "FANUC_YELLOW": ui.get("fanuc_yellow", "#FFC800"),
            "CRT_GREEN": ui.get("crt_green", "#6CFF6C"),
            "CRT_GREEN_DIM": ui.get("crt_green_dim", "#4ACF4A"),
            "CYAN": ui.get("cyan", "#56D6FF"),
            "WHITE": ui.get("white", "#EDEDED"),
        }
