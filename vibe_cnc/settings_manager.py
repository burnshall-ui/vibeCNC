import os, yaml

class SettingsManager:
    def __init__(self, path:str):
        self.path = path
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"config.yaml not found: {self.path}")
        with open(self.path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}
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

