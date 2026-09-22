"""移动广告合规实验室的 HTTP 入口。

路由层只做参数解析与错误映射，业务规则全部在 lab_service / spec_rules 中。
默认内存运行；指定 --ledger 时将账本镜像到 JSONL 文件，重启后重放恢复。
"""

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lab_service import Lab, LabError

SERVICE_ID = "mobile-ad-audit"
SERVICE_NAME = "移动广告合规实验室"

LAB = Lab()


def health_payload():
    """返回稳定的服务身份信息。"""
    return {"status": "ok", "service": SERVICE_ID, "name": SERVICE_NAME}


# (方法, 路径模式, 处理函数)；路径参数以 <name> 占位
ROUTES = [
    ("GET", "/health", lambda lab, m, b: health_payload()),
    ("POST", "/parties", lambda lab, m, b: lab.register_party(b["type"], b["name"])),
    ("POST", "/apps", lambda lab, m, b: lab.register_app(b["name"], b["operator_party_id"])),
    ("POST", "/builds", lambda lab, m, b: lab.register_build(
        b["app_id"], b["version"], b["submitted_at"], b.get("sdk_map"))),
    ("POST", "/devices", lambda lab, m, b: lab.register_device(b["model"], b["os_version"])),
    ("POST", "/tasks", lambda lab, m, b: lab.create_task(
        b["app_id"], b["build_id"], b["device_id"], b["script_version"],
        b.get("accessibility_mode", "off"), b.get("spec_version"), b.get("retest_of"))),
    ("POST", "/sessions", lambda lab, m, b: lab.start_session(
        b["task_id"], b["track"], b["started_at"])),
    ("POST", "/sessions/<session_id>/events", lambda lab, m, b: lab.ingest_events(
        m["session_id"], b["events"])),
    ("POST", "/tasks/<task_id>/evaluate", lambda lab, m, b: {
        "task_id": m["task_id"], "matches": lab.evaluate_task(m["task_id"])}),
    ("GET", "/findings", lambda lab, m, b: {"findings": lab.all_findings()}),
    ("GET", "/findings/<finding_id>", lambda lab, m, b: lab.get_finding(m["finding_id"])),
    ("POST", "/findings/<finding_id>/decisions", lambda lab, m, b: lab.record_decision(
        m["finding_id"], b["decision"], b["reviewer"], b.get("rationale", ""))),
    ("POST", "/findings/<finding_id>/notifications", lambda lab, m, b: lab.issue_notification(
        m["finding_id"], b["issuer"])),
    ("POST", "/tasks/<task_id>/retests", lambda lab, m, b: lab.record_retest(
        m["task_id"], b["verdict"], b["reviewer"])),
    ("GET", "/sessions/<session_id>/jumps", lambda lab, m, b: lab.session_jump_report(
        m["session_id"])),
    ("GET", "/parties/<party_id>/overview", lambda lab, m, b: lab.party_overview(
        m["party_id"])),
    ("GET", "/ledger/verify", lambda lab, m, b: lab.verify_ledger()),
]

_COMPILED = [
    (method, re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", path) + "$"), handler)
    for method, path, handler in ROUTES
]


class Handler(BaseHTTPRequestHandler):
    """提供健康检查与业务 API，供本地联调和运维巡检使用。"""

    server_version = "MobileAdAudit/1.0"

    def _dispatch(self, method):
        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                self._reply(400, {"error": "bad_json", "message": "请求体不是合法 JSON"})
                return
        path = self.path.split("?", 1)[0]
        for route_method, pattern, handler in _COMPILED:
            if route_method != method:
                continue
            match = pattern.match(path)
            if not match:
                continue
            try:
                result = handler(LAB, match.groupdict(), body or {})
            except LabError as error:
                self._reply(error.status, {"error": error.code, "message": error.message})
                return
            except (KeyError, TypeError) as error:
                self._reply(400, {"error": "bad_request",
                                  "message": f"缺少或非法字段：{error}"})
                return
            status = 201 if method == "POST" else 200
            self._reply(status, result)
            return
        self.send_error(404)

    def _reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def log_message(self, *_args):
        return


def main():
    parser = argparse.ArgumentParser(description=SERVICE_NAME)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--ledger", help="账本 JSONL 文件路径，提供后启用持久化")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        assert health_payload()["service"] == SERVICE_ID
        assert Lab().verify_ledger()["ok"]
        print("基础检查通过")
        return
    global LAB
    LAB = Lab(args.ledger)
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
