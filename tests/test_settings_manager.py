"""Configuration seeding (VC-21).

config.yaml is written by the settings dialog and is therefore not under
version control — keeping it tracked meant every save produced a diff carrying
one machine's paths and limits. config.example.yaml is the tracked template and
is copied on first start.

Needs PyYAML, so it runs in the dependency-installing CI job.
"""
import os
import shutil
import tempfile
import unittest

import yaml

from vibe_cnc.settings_manager import SettingsManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "config.example.yaml")


class ConfigSeedingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name
        shutil.copyfile(EXAMPLE, os.path.join(self.root, "config.example.yaml"))
        self.config = os.path.join(self.root, "config.yaml")

    def test_a_fresh_checkout_gets_a_config(self):
        self.assertFalse(os.path.exists(self.config))

        cfg = SettingsManager(self.config)

        self.assertTrue(os.path.exists(self.config))
        self.assertIn("machine", cfg.data)

    def test_an_existing_config_is_left_alone(self):
        with open(self.config, "w", encoding="utf-8") as handle:
            yaml.safe_dump({"machine": {"chuck_diameter": 210.0}}, handle)

        cfg = SettingsManager(self.config)

        self.assertEqual(cfg.data["machine"]["chuck_diameter"], 210.0)

    def test_without_a_template_it_says_so_rather_than_starting_blank(self):
        os.remove(os.path.join(self.root, "config.example.yaml"))

        with self.assertRaises(FileNotFoundError):
            SettingsManager(self.config)

    def test_colours_survive_the_round_trip(self):
        cfg = SettingsManager(self.config)

        colors = cfg.colors()
        self.assertEqual(colors["CRT_GREEN"], "#6CFF6C")
        self.assertEqual(set(colors), {"BG_DARK", "BG_BLACK", "FANUC_YELLOW",
                                       "CRT_GREEN", "CRT_GREEN_DIM", "CYAN", "WHITE"})


class TemplateContentTests(unittest.TestCase):
    """What the template ships with matters — it is what a new machine starts from."""

    @classmethod
    def setUpClass(cls):
        with open(EXAMPLE, "r", encoding="utf-8") as handle:
            cls.raw = handle.read()
        cls.data = yaml.safe_load(cls.raw)

    def test_chuck_diameter_ships_unset(self):
        # Unset blocks every diameter behind the chuck face: a false alarm
        # rather than a missed crash. A guessed number would do the opposite.
        self.assertIsNone(self.data["machine"]["chuck_diameter"])

    def test_the_template_keeps_its_comments(self):
        # The whole point of a separate template: yaml.dump strips comments, so
        # the file the dialog writes can never document itself.
        self.assertIn("#", self.raw)
        self.assertIn("MEASURE IT", self.raw)

    def test_the_template_covers_what_the_app_reads(self):
        for section in ("ui", "paths", "machine", "policies", "ai"):
            self.assertIn(section, self.data)
