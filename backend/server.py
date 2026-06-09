#!/usr/bin/env python3
"""
DanmakuHime — the browser-facing web server.

The whole HTTP/SSE surface in one place: EventHub (the in-process fan-out that
every event flows through) plus the Flask app that serves the selected frontend
folder and streams events to it over Server-Sent Events. `_sse` is the SSE wire
encoding and is intentionally private to this module — EventHub deals in event
objects; only the server turns them into `data: …` frames.
"""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Tuple

from flask import Flask, Response, request, send_from_directory
from flask_cors import CORS

from initialization import AppConfig


class EventHub:
    # The monotonic `id` assigned here is load-bearing: the frontend uses it to
    # sort, dedupe (by type:id), and reconcile the replayed history a late
    # subscriber receives on connect. Always let publish() assign it (or preserve
    # an existing one) — never mint ids elsewhere.
    def __init__(self, history_size: int, subscriber_queue_size: int):
        self._history: Deque[Dict[str, Any]] = deque(maxlen=history_size)
        self._subscribers: List[queue.Queue] = []
        self._subscriber_queue_size = subscriber_queue_size
        self._lock = threading.Lock()
        self._next_id = 0
        self._init_event: Dict[str, Any] = {}

    def set_init(self, event: Dict[str, Any]) -> None:
        with self._lock:
            self._init_event = dict(event)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            self._offer(subscriber, self._init_event)

    def publish(self, event: Dict[str, Any]) -> None:
        with self._lock:
            if "id" not in event:
                self._next_id += 1
                event = {**event, "id": self._next_id}
            else:
                self._next_id = max(self._next_id, int(event["id"]))
            self._history.append(event)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            self._offer(subscriber, event)

    def subscribe(self) -> Tuple[queue.Queue, Dict[str, Any], List[Dict[str, Any]]]:
        subscriber: queue.Queue = queue.Queue(maxsize=self._subscriber_queue_size)
        with self._lock:
            init_event = dict(self._init_event)
            history = list(self._history)
            self._subscribers.append(subscriber)
        return subscriber, init_event, history

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    @staticmethod
    def _offer(subscriber: queue.Queue, event: Dict[str, Any]) -> None:
        try:
            subscriber.put_nowait(event)
        except queue.Full:
            try:
                subscriber.get_nowait()
            except queue.Empty:
                pass
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                pass


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_flask_app(config: AppConfig, frontend_dir, hub: EventHub) -> Flask:
    # Everything under the selected frontend folder is served at the web root,
    # so index.html's relative refs (vendor/, fonts/, danmaku-feed.jsx) resolve
    # unchanged. The folder was integrity-checked at startup (check_frontend).
    app = Flask(__name__, static_folder=str(frontend_dir), static_url_path="")
    CORS(app)

    @app.route("/")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @app.after_request
    def _babel_mimetype(response):
        # JSX is transpiled in the browser, so any .jsx served from the
        # frontend folder must come back as text/babel (by extension — the
        # filenames are no longer hardcoded).
        if request.path.endswith(".jsx"):
            response.headers["Content-Type"] = "text/babel; charset=utf-8"
        return response

    @app.route("/stream")
    def stream():
        subscriber, init_event, history = hub.subscribe()

        def generate():
            try:
                if init_event:
                    yield _sse(init_event)
                for event in history:
                    yield _sse(event)
                while True:
                    try:
                        event = subscriber.get(timeout=config.sse_heartbeat_seconds)
                        yield _sse(event)
                    except queue.Empty:
                        yield ": heartbeat\n\n"
            except GeneratorExit:
                pass
            finally:
                hub.unsubscribe(subscriber)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.route("/health")
    def health():
        return {
            "status": "ok",
            "room_id": config.room_id,
        }

    return app
