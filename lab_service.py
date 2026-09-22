"""实验室核心业务：采集、判定、复核、整改与回潮。

设计要点：
- 原始事件与处置动作只追加，判定结论是从账本重放的纯函数读模型，
  因此"复测通过不抹掉此前问题时段""新构建不覆盖旧证据"天然成立。
- 测试脚本版本在任务创建时固化，脚本更新只影响之后创建的任务。
- 自动规则只产出 suspected；复核员确认后才能出具告知材料。
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from ledger import Ledger, hash_event
from spec_rules import (
    DEFAULT_SPEC_VERSION,
    RULES_ENGINE_VERSION,
    SPECS,
    TRACKS,
    close_path_assessment,
    evaluate_session,
    finding_key,
)

# 责任主体类型
PARTY_OPERATOR = "app_operator"   # 应用运营者
PARTY_ADVERTISER = "advertiser"   # 广告主
PARTY_SDK = "sdk_provider"        # 嵌入 SDK 提供方
PARTY_TYPES = (PARTY_OPERATOR, PARTY_ADVERTISER, PARTY_SDK)

EVENT_TYPES = ("ad_shown", "close_entry", "sensor", "jump", "network", "close_attempt")

# 复核决定
DECISION_CONFIRM = "confirm"
DECISION_DISMISS = "dismiss"

# 复测结论
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_ts(value):
    """解析 ISO8601 时间戳为 UTC 感知时间，供排序与期限计算。"""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LabError(Exception):
    """业务错误，status 供 HTTP 层映射。"""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _finding_id(key):
    return "f_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class Lab:
    def __init__(self, ledger_path=None, now=None):
        self.ledger = Ledger(ledger_path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.parties = {}
        self.apps = {}
        self.builds = {}
        self.devices = {}
        self.tasks = {}
        self.sessions = {}
        self.events = {}
        self._event_hashes = {}
        self.decisions = {}
        self.notifications = {}
        self.retests = []
        for entry in self.ledger.entries:
            self._apply(entry["type"], entry["data"])

    # ---------- 账本重放 ----------

    def _apply(self, entry_type, data):
        handler = getattr(self, "_apply_" + entry_type, None)
        if handler is None:
            raise LabError(500, "ledger_corrupt", f"未知账本条目类型 {entry_type}")
        handler(data)

    def _record(self, entry_type, data):
        entry = self.ledger.append(entry_type, data)
        self._apply(entry_type, data)
        return entry

    def _apply_party_registered(self, d):
        self.parties[d["party_id"]] = dict(d)

    def _apply_app_registered(self, d):
        self.apps[d["app_id"]] = dict(d)

    def _apply_build_registered(self, d):
        self.builds[d["build_id"]] = dict(d)

    def _apply_device_registered(self, d):
        self.devices[d["device_id"]] = dict(d)

    def _apply_task_created(self, d):
        self.tasks[d["task_id"]] = dict(d)

    def _apply_session_started(self, d):
        self.sessions[d["session_id"]] = dict(d)

    def _apply_event_ingested(self, d):
        self.events[d["event_id"]] = dict(d)
        self._event_hashes[d["event_hash"]] = d["event_id"]

    def _apply_review_decision(self, d):
        self.decisions[d["decision_id"]] = dict(d)

    def _apply_notification_issued(self, d):
        self.notifications[d["notification_id"]] = dict(d)

    def _apply_retest_recorded(self, d):
        self.retests.append(dict(d))

    # ---------- 注册 ----------

    def register_party(self, party_type, name, party_id=None):
        if party_type not in PARTY_TYPES:
            raise LabError(400, "bad_party_type", f"责任主体类型须为 {PARTY_TYPES}")
        party_id = party_id or _new_id("party")
        data = {"party_id": party_id, "type": party_type, "name": name}
        self._record("party_registered", data)
        return dict(data)

    def register_app(self, name, operator_party_id, app_id=None):
        if operator_party_id not in self.parties:
            raise LabError(404, "party_not_found", f"责任主体不存在：{operator_party_id}")
        app_id = app_id or _new_id("app")
        data = {"app_id": app_id, "name": name, "operator_party_id": operator_party_id}
        self._record("app_registered", data)
        return dict(data)

    def register_build(self, app_id, version, submitted_at, sdk_map=None, build_id=None):
        """登记应用构建。sdk_map：{sdk_key: {"party_id": …}}，登记后不可变。"""
        if app_id not in self.apps:
            raise LabError(404, "app_not_found", f"应用不存在：{app_id}")
        sdk_map = sdk_map or {}
        for key, info in sdk_map.items():
            party_id = info.get("party_id")
            if party_id not in self.parties:
                raise LabError(404, "party_not_found", f"SDK 责任主体不存在：{party_id}")
            if self.parties[party_id]["type"] != PARTY_SDK:
                raise LabError(400, "not_sdk_party", f"{party_id} 不是 SDK 提供方")
        build_id = build_id or _new_id("build")
        data = {"build_id": build_id, "app_id": app_id, "version": version,
                "submitted_at": iso(parse_ts(submitted_at)),
                "sdk_map": {k: {"party_id": v["party_id"]} for k, v in sdk_map.items()}}
        self._record("build_registered", data)
        return dict(data)

    def register_device(self, model, os_version, device_id=None):
        device_id = device_id or _new_id("device")
        data = {"device_id": device_id, "model": model, "os_version": os_version}
        self._record("device_registered", data)
        return dict(data)

    # ---------- 任务与采集 ----------

    def create_task(self, app_id, build_id, device_id, script_version,
                    accessibility_mode="off", spec_version=None, retest_of=None):
        if build_id not in self.builds:
            raise LabError(404, "build_not_found", f"构建不存在：{build_id}")
        if device_id not in self.devices:
            raise LabError(404, "device_not_found", f"设备不存在：{device_id}")
        build = self.builds[build_id]
        if build["app_id"] != app_id:
            raise LabError(400, "build_app_mismatch", "构建不属于该应用")
        spec_version = spec_version or DEFAULT_SPEC_VERSION
        if spec_version not in SPECS:
            raise LabError(400, "unknown_spec", f"未知规范版本：{spec_version}")
        if retest_of is not None and retest_of not in self.tasks:
            raise LabError(404, "task_not_found", f"复测基准任务不存在：{retest_of}")
        task_id = _new_id("task")
        data = {"task_id": task_id, "app_id": app_id, "build_id": build_id,
                "device_id": device_id, "script_version": script_version,
                "accessibility_mode": accessibility_mode,
                "spec_version": spec_version, "retest_of": retest_of,
                "created_at": iso(self._now())}
        self._record("task_created", data)
        return dict(data)

    def start_session(self, task_id, track, started_at):
        if task_id not in self.tasks:
            raise LabError(404, "task_not_found", f"任务不存在：{task_id}")
        if track not in TRACKS:
            raise LabError(400, "bad_track", f"操作轨迹须为 {TRACKS}")
        session_id = _new_id("sess")
        data = {"session_id": session_id, "task_id": task_id, "track": track,
                "started_at": iso(parse_ts(started_at))}
        self._record("session_started", data)
        return dict(data)

    def ingest_events(self, session_id, events):
        """批量入库。迟到、乱序、重传均接受；按 event_id+payload 哈希去重。"""
        if session_id not in self.sessions:
            raise LabError(404, "session_not_found", f"会话不存在：{session_id}")
        accepted, duplicates = [], []
        for raw in events:
            event_id = raw.get("event_id")
            payload = raw.get("payload")
            if not event_id or not isinstance(payload, dict):
                raise LabError(400, "bad_event", "事件须包含 event_id 与 payload 对象")
            if payload.get("type") not in EVENT_TYPES:
                raise LabError(400, "bad_event_type",
                               f"事件类型须为 {EVENT_TYPES}")
            if "ts" not in payload:
                raise LabError(400, "bad_event", "事件缺少 ts 时间戳")
            digest = hash_event({"event_id": event_id, "payload": payload})
            if digest in self._event_hashes:
                duplicates.append(event_id)
                continue
            if event_id in self.events:
                raise LabError(409, "event_conflict",
                               f"event_id 相同但内容不同：{event_id}")
            data = {"event_id": event_id, "session_id": session_id,
                    "type": payload.get("type"), "ts": iso(parse_ts(payload["ts"])),
                    "payload": payload, "event_hash": digest}
            self._record("event_ingested", data)
            accepted.append(event_id)
        return {"accepted": accepted, "duplicates": duplicates}

    # ---------- 规则判定（读模型） ----------

    def _session_events(self, session_id):
        events = [e for e in self.events.values() if e["session_id"] == session_id]
        events.sort(key=lambda e: (e["ts"], e["event_id"]))
        return events

    def _resolve_party(self, build, sdk_key):
        if sdk_key is None:
            return None
        info = build["sdk_map"].get(sdk_key)
        return info["party_id"] if info else None

    def _enrich(self, event, app, build):
        payload = dict(event["payload"])
        payload["event_id"] = event["event_id"]
        payload["ts"] = event["ts"]
        payload["_operator_party_id"] = app["operator_party_id"]
        sdk_key = payload.get("sdk_key")
        payload["_sdk_party_id"] = self._resolve_party(build, sdk_key)
        return payload

    def evaluate_task(self, task_id):
        """对任务的全部轨迹执行规则，返回涉嫌问题列表（纯函数，不落库）。"""
        if task_id not in self.tasks:
            raise LabError(404, "task_not_found", f"任务不存在：{task_id}")
        task = self.tasks[task_id]
        build = self.builds[task["build_id"]]
        app = self.apps[task["app_id"]]
        spec = SPECS[task["spec_version"]]
        matches = []
        for session in self.sessions.values():
            if session["task_id"] != task_id:
                continue
            events = [self._enrich(e, app, build)
                      for e in self._session_events(session["session_id"])]
            matches.extend(evaluate_session(
                spec, task["app_id"], task["build_id"], task["device_id"],
                session["track"], session["session_id"], events))
        return matches

    def _merge_matches(self, matches):
        """同一 finding key 的多次命中合并为一条，问题时段累计、证据并集。"""
        merged = {}
        for m in matches:
            key = finding_key(m)
            fid = _finding_id(key)
            if fid not in merged:
                merged[fid] = {
                    "finding_id": fid,
                    "issue_code": m["issue_code"],
                    "clause": m["clause"],
                    "app_id": m["app_id"],
                    "build_id": m["build_id"],
                    "device_id": m["device_id"],
                    "track": m["track"],
                    "responsible": m["responsible"],
                    "windows": [],
                    "event_ids": [],
                    "sessions": [],
                    "details": [],
                }
            f = merged[fid]
            f["windows"].append(m["window"])
            for eid in m["event_ids"]:
                if eid not in f["event_ids"]:
                    f["event_ids"].append(eid)
            if m["session_id"] not in f["sessions"]:
                f["sessions"].append(m["session_id"])
            f["details"].append(m["detail"])
        for f in merged.values():
            f["windows"].sort(key=lambda w: w["start"])
            f["event_ids"].sort(key=lambda eid: (self.events[eid]["ts"], eid))
        return merged

    def _decision_for(self, finding_id):
        decided = [d for d in self.decisions.values()
                   if d["finding_id"] == finding_id]
        decided.sort(key=lambda d: d["decided_at"])
        return decided[-1] if decided else None

    def _evidence_snapshot(self, finding):
        events = [self.events[eid] for eid in finding["event_ids"]]
        events.sort(key=lambda e: (e["ts"], e["event_id"]))
        return [{"event_id": e["event_id"], "type": e["type"],
                 "ts": e["ts"], "event_hash": e["event_hash"]} for e in events]

    def _snapshot_hash(self, snapshot):
        return hashlib.sha256(
            "|".join(s["event_hash"] for s in snapshot).encode("utf-8")
        ).hexdigest()

    def _finding_status(self, finding):
        decision = self._decision_for(finding["finding_id"])
        if decision is None:
            return "suspected", None
        if decision["decision"] == DECISION_DISMISS:
            return "dismissed", decision
        current_hash = self._snapshot_hash(self._evidence_snapshot(finding))
        if current_hash != decision["evidence_hash"]:
            # 已确认问题出现新的问题时段，需复核员再次确认
            return "confirmed_pending_review", decision
        return "confirmed", decision

    def all_findings(self):
        """全量涉嫌问题读模型：跨任务重放规则并合并，附复核状态。"""
        matches = []
        for task_id in self.tasks:
            matches.extend(self.evaluate_task(task_id))
        merged = self._merge_matches(matches)
        result = []
        for f in merged.values():
            status, decision = self._finding_status(f)
            f = dict(f)
            f["status"] = status
            f["spec_version"] = self.tasks[self.sessions[f["sessions"][0]]
                                           ["task_id"]]["spec_version"]
            f["rules_version"] = RULES_ENGINE_VERSION
            if decision:
                f["last_decision"] = decision
            result.append(f)
        result.sort(key=lambda f: (f["windows"][0]["start"], f["finding_id"]))
        return result

    def get_finding(self, finding_id):
        for f in self.all_findings():
            if f["finding_id"] == finding_id:
                return f
        raise LabError(404, "finding_not_found", f"问题记录不存在：{finding_id}")

    # ---------- 复核与告知 ----------

    def record_decision(self, finding_id, decision, reviewer, rationale=""):
        if decision not in (DECISION_CONFIRM, DECISION_DISMISS):
            raise LabError(400, "bad_decision", "复核决定须为 confirm 或 dismiss")
        finding = self.get_finding(finding_id)
        snapshot = self._evidence_snapshot(finding)
        data = {"decision_id": _new_id("dec"), "finding_id": finding_id,
                "decision": decision, "reviewer": reviewer,
                "rationale": rationale,
                "decided_at": iso(self._now()),
                "evidence_hash": self._snapshot_hash(snapshot),
                "evidence_snapshot": snapshot,
                "spec_version": finding["spec_version"],
                "rules_version": RULES_ENGINE_VERSION}
        self._record("review_decision", data)
        return dict(data)

    def issue_notification(self, finding_id, issuer):
        """出具告知材料。仅对已确认且证据未变化的问题可用。"""
        finding = self.get_finding(finding_id)
        status, decision = self._finding_status(finding)
        if status != "confirmed":
            raise LabError(409, "not_confirmed",
                           f"仅已确认的问题可出具告知材料，当前状态 {status}")
        for n in self.notifications.values():
            if n["finding_id"] == finding_id:
                return dict(n)  # 幂等：同一问题只出具一次
        spec = SPECS[finding["spec_version"]]
        decided_at = parse_ts(decision["decided_at"])
        deadline = decided_at + timedelta(days=spec.rectification_days)
        clause = spec.clauses[finding["issue_code"]]
        data = {
            "notification_id": _new_id("ntf"),
            "finding_id": finding_id,
            "issuer": issuer,
            "issued_at": iso(self._now()),
            "issue_code": finding["issue_code"],
            "clause": clause,
            "spec_version": finding["spec_version"],
            "rules_version": RULES_ENGINE_VERSION,
            "evidence_hash": decision["evidence_hash"],
            "responsible": finding["responsible"],
            "windows": finding["windows"],
            "rectification_deadline": iso(deadline),
            "rectification_days": spec.rectification_days,
        }
        self._record("notification_issued", data)
        return dict(data)

    # ---------- 复测与回潮 ----------

    def record_retest(self, task_id, verdict, reviewer):
        if verdict not in (VERDICT_PASS, VERDICT_FAIL):
            raise LabError(400, "bad_verdict", "复测结论须为 pass 或 fail")
        if task_id not in self.tasks:
            raise LabError(404, "task_not_found", f"任务不存在：{task_id}")
        task = self.tasks[task_id]
        if task["retest_of"] is None:
            raise LabError(400, "not_retest_task", "该任务不是复测任务")
        matches = self.evaluate_task(task_id)
        computed = VERDICT_FAIL if matches else VERDICT_PASS
        if computed != verdict:
            raise LabError(
                409, "verdict_mismatch",
                f"复测结论与证据不符：任务仍存在 {len(matches)} 项涉嫌问题" if matches
                else "复测结论与证据不符：任务未检出涉嫌问题，不能记为 fail")
        data = {"retest_id": _new_id("retest"), "task_id": task_id,
                "app_id": task["app_id"], "build_id": task["build_id"],
                "verdict": verdict, "reviewer": reviewer,
                "recorded_at": iso(self._now())}
        self._record("retest_recorded", data)
        return dict(data)

    def issue_thread(self, app_id, issue_code, party_id):
        """跨构建的问题主线：问题时段、复测点与回潮次数。

        复测通过只追加通过点，不抹除此前问题时段；通过之后再次出现
        问题时段即记为一次回潮。
        """
        if app_id not in self.apps:
            raise LabError(404, "app_not_found", f"应用不存在：{app_id}")
        windows = []
        for f in self.all_findings():
            if f["app_id"] != app_id or f["issue_code"] != issue_code:
                continue
            if f["responsible"][0]["party_id"] != party_id:
                continue
            if f["status"] == "dismissed":
                continue
            windows.extend(f["windows"])
        windows.sort(key=lambda w: w["start"])
        retests = sorted(
            (r for r in self.retests
             if r["app_id"] == app_id and r["verdict"] == VERDICT_PASS),
            key=lambda r: r["recorded_at"])
        passes = [{"at": r["recorded_at"], "build_id": r["build_id"],
                   "task_id": r["task_id"]} for r in retests]
        # 时间线归并：复测通过进入"洁净"状态，此后再出现问题时段记一次回潮；
        # 问题持续存在不重复计数，从未出过问题时的首次命中也不算回潮。
        points = [(w["start"], "violation") for w in windows]
        points += [(p["at"], "pass") for p in passes]
        points.sort()
        recurrences = 0
        clean = False
        seen_violation = False
        for _ts, kind in points:
            if kind == "pass":
                clean = True
            else:
                if clean and seen_violation:
                    recurrences += 1
                clean = False
                seen_violation = True
        return {"app_id": app_id, "issue_code": issue_code, "party_id": party_id,
                "windows": windows, "passes": passes,
                "recurrence_count": recurrences}

    # ---------- 承办视图 ----------

    def party_overview(self, party_id):
        """按责任主体汇总：在办问题、整改期限、是否逾期、回潮记录。"""
        if party_id not in self.parties:
            raise LabError(404, "party_not_found", f"责任主体不存在：{party_id}")
        now = self._now()
        items = []
        for f in self.all_findings():
            roles = [r for r in f["responsible"] if r["party_id"] == party_id]
            if not roles or f["status"] == "dismissed":
                continue
            notice = next((n for n in self.notifications.values()
                           if n["finding_id"] == f["finding_id"]), None)
            item = {"finding_id": f["finding_id"], "issue_code": f["issue_code"],
                    "app_id": f["app_id"], "build_id": f["build_id"],
                    "track": f["track"], "status": f["status"],
                    "role": roles[0]["role"],
                    "spec_version": f["spec_version"],
                    "windows": f["windows"]}
            if notice:
                deadline = parse_ts(notice["rectification_deadline"])
                item["rectification_deadline"] = notice["rectification_deadline"]
                item["overdue"] = now > deadline and f["status"] in (
                    "confirmed", "confirmed_pending_review")
            items.append(item)
        threads = []
        seen = set()
        for item in items:
            key = (item["app_id"], item["issue_code"])
            if key in seen:
                continue
            seen.add(key)
            threads.append(self.issue_thread(item["app_id"], item["issue_code"], party_id))
        return {"party": dict(self.parties[party_id]), "items": items,
                "threads": threads, "generated_at": iso(now)}

    def session_jump_report(self, session_id):
        """单次轨迹的跳转台账：谁触发、当时是否存在可操作关闭路径、版本依据。"""
        if session_id not in self.sessions:
            raise LabError(404, "session_not_found", f"会话不存在：{session_id}")
        session = self.sessions[session_id]
        task = self.tasks[session["task_id"]]
        build = self.builds[task["build_id"]]
        app = self.apps[task["app_id"]]
        spec = SPECS[task["spec_version"]]
        events = [self._enrich(e, app, build)
                  for e in self._session_events(session_id)]
        by_id = {e["event_id"]: e for e in events}
        session_start = parse_ts(session["started_at"])
        jumps = []
        for e in events:
            if e.get("type") != "jump":
                continue
            ad = by_id.get(e.get("ad_event_id")) if e.get("ad_event_id") else None
            trigger = e.get("trigger")
            if trigger == "user_tap":
                attribution = "user"
            elif trigger == "shake":
                attribution = "sdk" if (ad and ad.get("_sdk_party_id")) else "app"
            else:
                attribution = "advertiser" if e.get("stage") == "landing" else (
                    "sdk" if (ad and ad.get("_sdk_party_id")) else "app")
            close_info = None
            if ad:
                at_ms = int((parse_ts(e["ts"]) - parse_ts(ad["ts"])).total_seconds() * 1000)
                operable, reason = close_path_assessment(ad, at_ms, session["track"], spec)
                close_info = {"operable": operable, "reason": reason}
            jumps.append({
                "event_id": e["event_id"], "ts": e["ts"], "trigger": trigger,
                "attributed_to": attribution,
                "target": e.get("target"),
                "close_path_at_jump": close_info,
            })
        return {"session_id": session_id, "track": session["track"],
                "task_id": session["task_id"],
                "spec_version": task["spec_version"],
                "script_version": task["script_version"],
                "rules_version": RULES_ENGINE_VERSION,
                "session_started_at": session["started_at"],
                "jumps": jumps}

    def verify_ledger(self):
        ok, bad = self.ledger.verify()
        return {"ok": ok, "corrupt_at": bad, "entries": len(self.ledger.entries)}
