import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.task_history import TaskHistoryStore


class TaskHistoryStoreTests(unittest.TestCase):
    def test_create_update_and_list_tasks_returns_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskHistoryStore(Path(temp_dir) / "task-history.jsonl")
            task = store.create_task(
                trigger_channel="web",
                source_type="wechat",
                source_url="https://mp.weixin.qq.com/s/example",
            )
            updated = store.update_task(
                task["task_id"],
                status="success",
                note_title="示例笔记",
                sync_path="00_Inbox/微信公众号/示例笔记.md",
            )
            listing = store.list_tasks()

        self.assertEqual(updated["status"], "success")
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["task_id"], task["task_id"])
        self.assertEqual(listing["items"][0]["note_title"], "示例笔记")
        self.assertEqual(listing["items"][0]["sync_path"], "00_Inbox/微信公众号/示例笔记.md")

    def test_list_tasks_supports_filters_and_pagination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskHistoryStore(Path(temp_dir) / "task-history.jsonl")
            first = store.create_task(
                trigger_channel="web",
                source_type="wechat",
                source_url="https://mp.weixin.qq.com/s/a",
            )
            store.update_task(first["task_id"], status="success", note_title="A")
            second = store.create_task(
                trigger_channel="telegram",
                source_type="zhihu",
                source_url="https://www.zhihu.com/question/1/answer/2",
            )
            store.update_task(second["task_id"], status="error", error_message="boom")
            third = store.create_task(
                trigger_channel="feishu",
                source_type="web",
                source_url="https://example.com/post",
            )
            store.update_task(third["task_id"], status="running")

            filtered = store.list_tasks(source_type="zhihu", status="error")
            paged = store.list_tasks(limit=1, offset=1)

        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["task_id"], second["task_id"])
        self.assertEqual(len(paged["items"]), 1)
        self.assertEqual(paged["items"][0]["task_id"], second["task_id"])

    def test_store_is_thread_safe_for_concurrent_creates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "task-history.jsonl"
            store = TaskHistoryStore(history_path)
            threads = []

            def worker(index: int):
                task = store.create_task(
                    trigger_channel="web",
                    source_type="web",
                    source_url=f"https://example.com/{index}",
                )
                store.update_task(task["task_id"], status="success", note_title=f"task-{index}")

            for index in range(8):
                thread = threading.Thread(target=worker, args=(index,))
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

            listing = store.list_tasks(limit=20)
            raw_lines = history_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(listing["total"], 8)
        self.assertEqual(len(raw_lines), 16)
        for line in raw_lines:
            payload = json.loads(line)
            self.assertIn("task_id", payload)
            self.assertIn("status", payload)

    def test_create_task_supports_rerun_link(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TaskHistoryStore(Path(temp_dir) / "task-history.jsonl")
            original = store.create_task(
                trigger_channel="web",
                source_type="web",
                source_url="https://example.com/first",
            )
            rerun = store.create_task(
                trigger_channel="web",
                source_type="web",
                source_url="https://example.com/first",
                rerun_of_task_id=original["task_id"],
            )

        self.assertEqual(rerun["rerun_of_task_id"], original["task_id"])
