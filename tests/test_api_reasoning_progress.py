"""验证真实 SSE 推理阶段会报告进度，并且不会进入可见答案。"""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import vision_assist as VA


class Response(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


class ReasoningProgressTests(unittest.TestCase):
    def response(self):
        events = [
            {"choices": [{"delta": {"reasoning_content": "内部推理"}}]},
            {"choices": [{"delta": {"content": '{"complete":true}'}, "finish_reason": "stop"}]},
        ]
        data = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
        return Response(data + b"data: [DONE]\n\n")

    def test_reasoning_reports_count_without_leaking_text(self):
        progress, visible = [], []
        with patch.object(VA.urllib.request, "urlopen", return_value=self.response()):
            body = VA._chat_request("https://example.invalid", "test", {"stream": True}, 1,
                                    reasoning_callback=progress.append, stream_callback=visible.append)
        self.assertEqual(progress, [4])
        self.assertEqual(body["choices"][0]["message"]["content"], '{"complete":true}')
        self.assertNotIn("内部推理", "".join(visible))

    def test_progress_handler_failure_does_not_abort_answer(self):
        def fail(_count):
            raise RuntimeError("界面回调失败")
        with patch.object(VA.urllib.request, "urlopen", return_value=self.response()):
            body = VA._chat_request("https://example.invalid", "test", {"stream": True}, 1,
                                    reasoning_callback=fail)
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")


if __name__ == "__main__":
    unittest.main()
