import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def http_stub():
    servers = []

    def make(body, status=200, delay=0.0, drip=False, drip_interval=0.25):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                if delay:
                    time.sleep(delay)
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                # drip 模式承诺一个远超实际发送量的 Content-Length，然后每隔
                # drip_interval 只滴 1 个字节：连接始终活着（单次 socket 超时永不
                # 触发），但响应在墙钟意义上永不完成——专门打穿「timeout 只约束
                # 单次 socket 操作」的写法。
                content_length = 10 ** 6 if drip else len(payload)
                self.send_header("Content-Length", str(content_length))
                self.end_headers()
                if drip:
                    try:
                        while True:
                            time.sleep(drip_interval)
                            self.wfile.write(payload[:1])
                    except OSError:            # gate 进程退出后管道断开，收线程
                        return
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/decide"

    yield make
    for server in servers:
        server.shutdown()
