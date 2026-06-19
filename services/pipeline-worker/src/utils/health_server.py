"""Cloud Run 워커 컨테이너용 최소 health 엔드포인트.

Cloud Run 서비스는 컨테이너가 `$PORT`에서 연결을 받아줘야 정상으로 본다.
순수 큐 소비자는 포트를 열지 않아 startup probe(기동 점검)가 실패한다.
이 모듈은 작은 HTTP 서버를 데몬 스레드에서 돌려, 소비자는 메인 asyncio 루프에서
그대로 돌면서 포트는 열린 상태를 유지하게 한다.

범위 메모: 지금은 "컨테이너가 떠 있음"(startup probe)만 증명한다.
소비자가 실제로 일하는지(liveness)는 아직 반영하지 않으며, 그건 별도 견고화 단계다.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8080


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:
        """표준 핸들러가 요청마다 stderr에 찍는 로그를 끈다."""


def resolve_port() -> int:
    return int(os.environ.get("PORT", str(DEFAULT_PORT)))


def start_health_server(port: int | None = None) -> ThreadingHTTPServer:
    """health 서버를 데몬 스레드에서 시작하고 서버 객체를 돌려준다.

    메인 asyncio 루프 밖에서 돌기 때문에, 소비자 작업이 루프를 막아도
    probe 응답이 멈추지 않는다.
    """

    server = ThreadingHTTPServer(("0.0.0.0", port or resolve_port()), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    return server
