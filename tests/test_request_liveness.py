"""验证真实阻塞读取、取消竞态、断网反馈及浮层轻量打开。"""
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import vision_assist as VA


class RequestLivenessTests(unittest.TestCase):
    def open_stream(self, mode="stall"):
        entered, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b': connected\n\n')
                self.wfile.flush()
                entered.set()
                if mode == "stall":
                    release.wait(4)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        control, errors = VA.RequestControl(), []
        def request():
            try:
                VA._chat_request("http://127.0.0.1:%d" % server.server_port,
                                 "test-not-a-secret", {"stream": True}, 4,
                                 request_control=control)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(2))
        return control, worker, errors

    def test_cancel_real_buffered_read_returns_immediately_and_releases_reader(self):
        with patch.object(VA, "_network_disconnected", return_value=False):
            for _ in range(10):
                control, worker, errors = self.open_stream()
                time.sleep(.02)
                start = time.perf_counter()
                control.cancel()
                self.assertLess(time.perf_counter() - start, .1)
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors[0], VA.RequestCancelled)

    def test_close_that_waits_for_a_read_lock_cannot_block_caller(self):
        released, closed = threading.Event(), threading.Event()
        class LockedResponse:
            def close(self):
                released.wait(1)
                closed.set()
        control = VA.RequestControl()
        control.attach(LockedResponse())
        start = time.perf_counter()
        control.cancel()
        self.assertLess(time.perf_counter() - start, .1)
        released.set()
        self.assertTrue(closed.wait(1))

    def test_offline_midstream_interrupts_real_socket_and_reports_network(self):
        offline = threading.Event()
        with patch.object(VA, "_network_disconnected", side_effect=offline.is_set):
            control, worker, errors = self.open_stream()
            offline.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertFalse(control.cancelled)
            self.assertIsInstance(errors[0], VA.NetworkInterrupted)
            self.assertIn("网络已断开", str(errors[0]))

    def test_stalled_transport_has_bounded_idle_wait(self):
        with patch.object(VA, "_network_disconnected", return_value=False), \
             patch.object(VA.RequestControl, "IDLE_TIMEOUT", .6):
            control, worker, errors = self.open_stream()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(errors)
            self.assertIsInstance(errors[0], (VA.NetworkInterrupted, TimeoutError))

    def test_truncated_stream_is_not_returned_as_a_complete_answer(self):
        with patch.object(VA, "_network_disconnected", return_value=False):
            control, worker, errors = self.open_stream("truncate")
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(errors[0], VA.NetworkInterrupted)

    def test_cancel_before_response_attachment_still_aborts_late_response(self):
        closed = threading.Event()
        class LateResponse:
            def close(self):
                closed.set()
        control = VA.RequestControl()
        control.cancel()
        with self.assertRaises(VA.RequestCancelled):
            control.attach(LateResponse())
        self.assertTrue(closed.wait(1))

    def test_cancel_while_connecting_does_not_wait_for_headers(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        class Response:
            headers = {"Content-Type": "text/event-stream"}
            def close(self):
                closed.set()
        def connect(*args, **kwargs):
            entered.set()
            release.wait(2)
            return Response()
        control, errors = VA.RequestControl(), []
        def request():
            try:
                VA._chat_request("https://example.invalid", "fake", {}, 240,
                                 request_control=control)
            except Exception as exc:
                errors.append(exc)
        with patch.object(VA.urllib.request, "urlopen", side_effect=connect), \
             patch.object(VA, "_network_disconnected", return_value=False):
            worker = threading.Thread(target=request, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(1))
            control.cancel()
            worker.join(.5)
            self.assertFalse(worker.is_alive())
            self.assertIsInstance(errors[0], VA.RequestCancelled)
            release.set()
            self.assertTrue(closed.wait(1))

    def test_network_error_is_reported_in_chinese_without_an_automatic_retry(self):
        with patch.object(VA, "_api_settings", return_value=(True, "https://fake.invalid", "fake", "fake")), \
             patch.object(VA, "_image_data", return_value="data:image/png;base64,AA=="), \
             patch.object(VA, "_chat_request", side_effect=VA.urllib.error.URLError("offline")) as sent:
            result = VA.solve_question("unused.png", request_control=VA.RequestControl())
        self.assertIn("网络连接失败", result["reason"])
        self.assertEqual(sent.call_count, 1)


class ReferenceLivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import ref_panel as RP
        cls.RP = RP

    def setUp(self):
        import config as cfg
        self.cfg = cfg
        self.saved = cfg._path, cfg._cache
        self.tmp = tempfile.TemporaryDirectory()
        cfg._path = str(Path(self.tmp.name) / "config.json")
        cfg._cache = copy.deepcopy(cfg.DEFAULTS)
        self.panel = self.RP.RefAnswerPanel()
        self.question = {"id": 482, "imported": True, "source": "测试",
                         "reference_source": {"ocr_words": [{"text": "1"}]}}
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.panel._vision_thread = None
        self.panel._vision_health_timer.stop()
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.cfg._path, self.cfg._cache = self.saved
        self.tmp.cleanup()

    def test_repeated_open_never_enters_local_image_recognition(self):
        with patch.object(self.RP, "_refresh_cached_imported_geometry",
                          side_effect=AssertionError("不得在界面线程识图")):
            for _ in range(20):
                self.panel.set_question(self.question)
                self.panel.show()
                self.app.processEvents()
                self.panel.hide()

    def test_silence_and_network_failure_have_visible_feedback(self):
        control = VA.RequestControl()
        control.last_activity = time.monotonic() - 20
        control.network_started = True
        self.panel._q = self.question
        self.panel._vision_started_at = time.monotonic() - 20
        self.panel._vision_thread = type("Worker", (), {
            "qid": 482, "request_control": control, "isRunning": lambda _: True})()
        self.panel._update_vision_health()
        self.assertIn("未收到服务商数据", self.panel.vision_status.text())
        control.fail("网络已断开")
        self.panel._update_vision_health()
        self.assertIn("网络已断开", self.panel.vision_status.text())

    def test_cancelled_result_is_never_saved_even_if_success_arrives_late(self):
        self.panel._q = self.question
        with patch.object(self.panel, "_vision_update_cancelled", return_value=True), \
             patch.object(self.panel, "_persist_condition_assist") as save:
            self.panel._on_vision_completed(482, {
                "status": "ready", "direct_answer": {"summary": "迟到结果"}}, {}, "")
        save.assert_not_called()
        self.assertIn("已取消", self.panel.vision_status.text())

    def test_live_stream_does_not_repeatedly_dispatch_web_rendering(self):
        self.panel._q = self.question
        self.panel._renderer_loaded = True
        with patch.object(self.panel, "_render_pending_markdown") as render:
            for i in range(12):
                self.panel._on_vision_streamed(482, "解答 " * (i + 1))
        render.assert_not_called()

    def test_real_qthread_repeated_start_cancel_close_and_reopen(self):
        from PySide6.QtTest import QTest
        self.cfg._cache["vision_api_disclaimer_ack"] = True
        image = Path(self.tmp.name) / "q.png"
        image.write_bytes(b"local fixture")
        def solve(*args, request_control=None, **kwargs):
            while not request_control.cancelled:
                time.sleep(.005)
            return {"status": "cancelled"}
        with patch.object(self.RP.BD, "fig_path", return_value=str(image)), \
             patch.object(VA, "_api_settings", return_value=(True, "https://fake.invalid", "fake", "fake")), \
             patch.object(self.panel, "_refresh_billing_badge"), \
             patch.object(VA, "solve_question", side_effect=solve):
            for _ in range(20):
                self.panel.set_question(self.question)
                self.panel.show()
                self.panel.start_condition_vision()
                QTest.qWait(20)
                start = time.perf_counter()
                self.panel.cancel_condition_vision()
                self.assertLess(time.perf_counter() - start, .1)
                self.panel.hide()
                deadline = time.monotonic() + 1
                while self.panel._vision_thread is not None and time.monotonic() < deadline:
                    QTest.qWait(10)
                self.assertIsNone(self.panel._vision_thread)
                self.panel.show()
                self.app.processEvents()
                self.assertIn("已取消", self.panel.vision_status.text())

if __name__ == "__main__":
    unittest.main()
