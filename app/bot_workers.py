from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests

from app.config import get_settings
from app.services import (
    process_feishu_long_connection_event,
    process_telegram_polling_update,
)


_telegram_thread: threading.Thread | None = None
_telegram_stop = threading.Event()
_feishu_thread: threading.Thread | None = None
_feishu_stop = threading.Event()
_worker_lock = threading.Lock()


def start_bot_receivers() -> None:
    settings = get_settings()
    print(
        "[bot] receiver configuration "
        f"deployment_mode={settings.deployment_mode} "
        f"telegram={settings.telegram_receive_mode if settings.telegram_enabled else 'disabled'} "
        f"feishu={settings.feishu_receive_mode if settings.feishu_enabled else 'disabled'}"
    )
    if settings.telegram_enabled and settings.telegram_receive_mode == "polling":
        start_telegram_polling_worker()
    if settings.feishu_enabled and settings.feishu_receive_mode == "long_connection":
        start_feishu_long_connection_worker()


def stop_bot_receivers() -> None:
    _telegram_stop.set()
    _feishu_stop.set()
    for thread in (_telegram_thread, _feishu_thread):
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)


def start_telegram_polling_worker() -> None:
    global _telegram_thread
    with _worker_lock:
        if _telegram_thread is not None and _telegram_thread.is_alive():
            return
        _telegram_stop.clear()
        _telegram_thread = threading.Thread(target=_telegram_polling_loop, name="telegram-polling", daemon=True)
        _telegram_thread.start()


def start_feishu_long_connection_worker() -> None:
    global _feishu_thread
    with _worker_lock:
        if _feishu_thread is not None and _feishu_thread.is_alive():
            return
        _feishu_stop.clear()
        _feishu_thread = threading.Thread(target=_feishu_long_connection_loop, name="feishu-long-connection", daemon=True)
        _feishu_thread.start()


def _telegram_api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _telegram_polling_loop() -> None:
    offset: int | None = None
    session = requests.Session()
    while not _telegram_stop.is_set():
        settings = get_settings()
        if not settings.telegram_enabled or settings.telegram_receive_mode != "polling" or not settings.telegram_bot_token:
            _telegram_stop.wait(5)
            continue
        try:
            session.post(
                _telegram_api_url(settings.telegram_bot_token, "deleteWebhook"),
                json={"drop_pending_updates": False},
                timeout=max(settings.default_timeout, 15),
            ).raise_for_status()
            payload: dict[str, Any] = {
                "timeout": max(int(settings.telegram_poll_interval), 1),
                "allowed_updates": ["message"],
            }
            if offset is not None:
                payload["offset"] = offset
            response = session.post(
                _telegram_api_url(settings.telegram_bot_token, "getUpdates"),
                json=payload,
                timeout=max(settings.default_timeout, settings.telegram_poll_interval + 10),
            )
            response.raise_for_status()
            data = response.json()
            updates = data.get("result") if isinstance(data.get("result"), list) else []
            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = int(update.get("update_id") or 0)
                if update_id:
                    offset = update_id + 1
                process_telegram_polling_update(update)
        except Exception as error:
            print(f"[telegram] polling failed: {error}")
            _telegram_stop.wait(5)
            continue
        _telegram_stop.wait(max(int(settings.telegram_poll_interval), 1))


def _feishu_long_connection_loop() -> None:
    while not _feishu_stop.is_set():
        settings = get_settings()
        if not settings.feishu_enabled or settings.feishu_receive_mode != "long_connection":
            _feishu_stop.wait(5)
            continue
        try:
            _run_feishu_ws_client()
        except Exception as error:
            print(f"[feishu] long connection failed: {error}")
            _feishu_stop.wait(5)


def _run_feishu_ws_client() -> None:
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
    except ImportError as error:
        raise RuntimeError("飞书长连接需要安装 lark-oapi 依赖") from error

    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise RuntimeError("飞书 App ID / App Secret 未配置")

    def on_message(data: P2ImMessageReceiveV1) -> None:
        raw = json.loads(lark.JSON.marshal(data))
        process_feishu_long_connection_event(raw)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = lark.ws.Client(settings.feishu_app_id, settings.feishu_app_secret, event_handler=event_handler)
    client.start()
