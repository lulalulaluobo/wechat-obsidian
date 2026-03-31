import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import (  # noqa: E402
    _invoke_isolated_worker,
    execute_single_conversion,
    get_sync_store,
    get_task_history_store,
)


class ServicesIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        runtime_path = Path(self.temp_dir.name) / "runtime-config.json"
        self.env_patcher = patch.dict(
            os.environ,
            {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_USERNAME": "admin",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
                "WECHAT_MD_SINGLE_CONVERSION_ISOLATION_ENABLED": "true",
                "WECHAT_MD_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS": "1",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_invoke_isolated_worker_returns_result(self):
        result = _invoke_isolated_worker(
            "_isolated_echo_worker",
            {"value": "ok"},
            timeout_seconds=1,
        )

        self.assertEqual(result, {"value": "ok"})

    def test_invoke_isolated_worker_times_out(self):
        with self.assertRaisesRegex(TimeoutError, "硬超时"):
            _invoke_isolated_worker(
                "_isolated_sleep_worker",
                {"seconds": 2},
                timeout_seconds=1,
            )

    def test_execute_single_conversion_marks_task_error_when_isolation_times_out(self):
        with patch(
            "app.services._run_single_conversion_isolated",
            side_effect=TimeoutError("单篇转换硬超时（1s）"),
        ):
            with self.assertRaisesRegex(TimeoutError, "硬超时"):
                execute_single_conversion(
                    url="https://mp.weixin.qq.com/s/example",
                    timeout=30,
                    save_html=False,
                    output_target="local",
                )

        tasks = get_task_history_store().list_tasks(limit=10)
        self.assertEqual(tasks["total"], 1)
        task = tasks["items"][0]
        self.assertEqual(task["status"], "error")
        self.assertIn("硬超时", task["error_message"])

        article = get_sync_store().get_article_by_url("https://mp.weixin.qq.com/s/example")
        self.assertIsNotNone(article)
        self.assertEqual(article["process_status"], "error")
        self.assertIn("硬超时", article["last_error"])
