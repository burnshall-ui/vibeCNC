"""AIWorker delivery thread (VC-11).

The worker body runs on a QThreadPool thread and its callback touches widgets,
so the signal has to be delivered on the thread that owns them. This checks the
contract rather than the connection flag: the callback must arrive on the main
thread, whatever Qt decides internally.

vibe_cnc.py sits next to the vibe_cnc package and is shadowed by it on import,
so it is loaded from its path.
"""
import importlib.util
import os
import threading
import time
import unittest

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_app_module():
    spec = importlib.util.spec_from_file_location("vibe_cnc_app",
                                                  os.path.join(ROOT, "vibe_cnc.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AIWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.module = load_app_module()

    def _run(self, fn, args=()):
        """Runs one worker and pumps the event loop until its callback lands."""
        seen = {}

        def callback(ok, resp):
            seen["thread"] = threading.current_thread()
            seen["ok"] = ok
            seen["resp"] = resp

        worker = self.module.AIWorker(fn, args, callback)
        QThreadPool.globalInstance().start(worker)

        deadline = time.monotonic() + 5.0
        while "thread" not in seen and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

        self.assertIn("thread", seen, "callback never arrived")
        return seen

    def test_callback_runs_on_the_main_thread(self):
        seen = self._run(lambda: (True, "done"))

        self.assertIs(seen["thread"], threading.main_thread())

    def test_result_is_passed_through(self):
        seen = self._run(lambda value: (True, f"got {value}"), ("42",))

        self.assertTrue(seen["ok"])
        self.assertEqual(seen["resp"], "got 42")

    def test_worker_body_runs_off_the_main_thread(self):
        # Otherwise the queued delivery above would prove nothing.
        where = {}

        def fn():
            where["thread"] = threading.current_thread()
            return True, "ok"

        self._run(fn)

        self.assertIsNot(where["thread"], threading.main_thread())

    def test_exception_in_the_worker_becomes_a_failed_result(self):
        def boom():
            raise ValueError("kaputt")

        seen = self._run(boom)

        self.assertFalse(seen["ok"])
        self.assertIn("ValueError", seen["resp"])
        self.assertIn("kaputt", seen["resp"])
