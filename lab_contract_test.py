"""领域契约测试：采集去重、规则判定、责任归属、复核门控、
证据不可变、脚本版本钉住、复测与回潮、账本完整性。"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from lab_service import Lab, LabError
from spec_rules import RULES_ENGINE_VERSION

T0 = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)


def ts(seconds):
    return (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def ad_shown(event_id, placement="splash", sdk_key="adsdk", close=None,
             observed_for_ms=6000, at=1.0, creative="cr-1"):
    return {"event_id": event_id, "payload": {
        "type": "ad_shown", "ts": ts(at), "placement": placement,
        "sdk_key": sdk_key, "creative_id": creative,
        "close": close if close is not None else {
            "present": True, "appears_after_ms": 500, "enabled": True,
            "rect_dp": {"w": 48, "h": 48},
            "accessibility_focusable": True, "label": "关闭广告"},
        "observed_for_ms": observed_for_ms}}


def sensor(event_id, magnitude, duration_ms, rotation_deg=40.0, at=2.0):
    return {"event_id": event_id, "payload": {
        "type": "sensor", "ts": ts(at), "magnitude": magnitude,
        "duration_ms": duration_ms, "rotation_deg": rotation_deg}}


def jump(event_id, trigger, ad_event_id=None, stage="ad_unit",
         target=None, at=3.0):
    return {"event_id": event_id, "payload": {
        "type": "jump", "ts": ts(at), "trigger": trigger,
        "ad_event_id": ad_event_id, "stage": stage,
        "target": target or {"url": "https://shop.example/x", "owner_id": None}}}


class LabFixture(unittest.TestCase):
    """搭好责任主体、应用、两个构建与设备，并提供一个可推进的时钟。"""

    def setUp(self):
        self.clock = [T0]
        self.lab = Lab(now=lambda: self.clock[0])
        self.operator = self.lab.register_party("app_operator", "某应用运营公司")
        self.advertiser = self.lab.register_party("advertiser", "某广告主")
        self.sdk = self.lab.register_party("sdk_provider", "某广告SDK")
        self.app = self.lab.register_app("某资讯App", self.operator["party_id"])
        self.build_v1 = self.lab.register_build(
            self.app["app_id"], "3.2.0", ts(0),
            sdk_map={"adsdk": {"party_id": self.sdk["party_id"]}})
        self.device = self.lab.register_device("Pixel 7", "Android 14")

    def advance(self, days=0, hours=0):
        self.clock[0] += timedelta(days=days, hours=hours)

    def make_task(self, build=None, script="script-2026.1", track_spec="2025.1",
                  retest_of=None, device=None):
        return self.lab.create_task(
            self.app["app_id"], (build or self.build_v1)["build_id"],
            (device or self.device)["device_id"], script,
            spec_version=track_spec, retest_of=retest_of)

    def run_track(self, task, track, events, started=0.0):
        session = self.lab.start_session(task["task_id"], track, ts(started))
        result = self.lab.ingest_events(session["session_id"], events)
        return session, result


class IngestTest(LabFixture):
    def test_retransmission_is_deduplicated(self):
        task = self.make_task()
        session = self.lab.start_session(task["task_id"], "normal", ts(0))
        batch = [ad_shown("e1")]
        first = self.lab.ingest_events(session["session_id"], batch)
        self.assertEqual(first["accepted"], ["e1"])
        # 迟到重传：同一事件再次上报不产生第二条证据
        second = self.lab.ingest_events(session["session_id"], batch)
        self.assertEqual(second["accepted"], [])
        self.assertEqual(second["duplicates"], ["e1"])
        self.assertEqual(len(self.lab.events), 1)

    def test_out_of_order_events_are_sorted_by_timestamp(self):
        task = self.make_task()
        session = self.lab.start_session(task["task_id"], "normal", ts(0))
        self.lab.ingest_events(session["session_id"], [
            jump("e2", "user_tap", at=5.0), ad_shown("e1", at=1.0)])
        ordered = [e["event_id"] for e in self.lab._session_events(session["session_id"])]
        self.assertEqual(ordered, ["e1", "e2"])

    def test_same_event_id_with_different_payload_conflicts(self):
        task = self.make_task()
        session = self.lab.start_session(task["task_id"], "normal", ts(0))
        self.lab.ingest_events(session["session_id"], [ad_shown("e1")])
        with self.assertRaises(LabError) as ctx:
            self.lab.ingest_events(session["session_id"], [
                ad_shown("e1", placement="interstitial")])
        self.assertEqual(ctx.exception.status, 409)


class RuleTest(LabFixture):
    def test_lockscreen_without_close_entry_is_flagged(self):
        task = self.make_task()
        close = {"present": False}
        self.run_track(task, "elderly", [
            ad_shown("e1", placement="lockscreen_wallpaper", close=close,
                     observed_for_ms=6000)])
        matches = self.lab.evaluate_task(task["task_id"])
        self.assertEqual([m["issue_code"] for m in matches], ["LOCKSCREEN_NO_CLOSE"])
        # 锁屏画报由应用自身承载，应用运营者单独担责
        self.assertEqual(matches[0]["responsible"][0]["party_id"],
                         self.operator["party_id"])
        self.assertEqual(matches[0]["clause"], "4.2")

    def test_splash_close_appearing_late_is_flagged_with_sdk_shared(self):
        task = self.make_task()
        close = {"present": True, "appears_after_ms": 4500, "enabled": True,
                 "rect_dp": {"w": 48, "h": 48},
                 "accessibility_focusable": True, "label": "关闭"}
        self.run_track(task, "normal", [ad_shown("e1", close=close)])
        matches = self.lab.evaluate_task(task["task_id"])
        self.assertEqual([m["issue_code"] for m in matches],
                         ["SPLASH_CLOSE_DEFICIENT"])
        roles = {r["party_id"]: r["role"] for r in matches[0]["responsible"]}
        self.assertEqual(roles[self.operator["party_id"]], "primary")
        self.assertEqual(roles[self.sdk["party_id"]], "secondary")

    def test_elderly_track_uses_larger_touch_target(self):
        task = self.make_task()
        close = {"present": True, "appears_after_ms": 500, "enabled": True,
                 "rect_dp": {"w": 50, "h": 50},
                 "accessibility_focusable": True, "label": "关闭"}
        # 50dp 在正常轨迹达标（≥48），老人轨迹不达标（≥56）
        self.run_track(task, "normal", [ad_shown("e1", close=close)])
        self.run_track(task, "elderly", [ad_shown("e2", close=close)])
        matches = self.lab.evaluate_task(task["task_id"])
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["track"], "elderly")
        self.assertEqual(matches[0]["detail"]["reason"], "touch_target_too_small")

    def test_screen_reader_track_flags_unfocusable_close(self):
        task = self.make_task()
        close = {"present": True, "appears_after_ms": 500, "enabled": True,
                 "rect_dp": {"w": 48, "h": 48},
                 "accessibility_focusable": False, "label": None}
        self.run_track(task, "screen_reader", [ad_shown("e1", close=close)])
        matches = self.lab.evaluate_task(task["task_id"])
        self.assertEqual([m["issue_code"] for m in matches],
                         ["CLOSE_NOT_ACCESSIBLE"])

    def test_shake_below_threshold_is_flagged_and_attributed_to_sdk(self):
        task = self.make_task()
        self.run_track(task, "normal", [
            ad_shown("e1", placement="shake_banner"),
            sensor("s1", magnitude=9.0, duration_ms=300),
            jump("j1", "shake", ad_event_id="e1")])
        matches = self.lab.evaluate_task(task["task_id"])
        self.assertEqual([m["issue_code"] for m in matches], ["SHAKE_MISTRIGGER"])
        roles = {r["party_id"]: r["role"] for r in matches[0]["responsible"]}
        self.assertEqual(roles[self.sdk["party_id"]], "primary")
        self.assertEqual(roles[self.operator["party_id"]], "secondary")
        self.assertIn("magnitude", matches[0]["detail"]["below_thresholds"])
        self.assertIn("duration", matches[0]["detail"]["below_thresholds"])

    def test_genuine_shake_is_not_flagged(self):
        task = self.make_task()
        self.run_track(task, "normal", [
            ad_shown("e1", placement="shake_banner"),
            sensor("s1", magnitude=25.0, duration_ms=3500, rotation_deg=60.0),
            jump("j1", "shake", ad_event_id="e1")])
        self.assertEqual(self.lab.evaluate_task(task["task_id"]), [])

    def test_landing_auto_redirect_is_attributed_to_advertiser(self):
        task = self.make_task()
        self.run_track(task, "normal", [
            ad_shown("e1"),
            jump("j1", "auto", ad_event_id="e1", stage="landing",
                 target={"url": "https://shop.example/x",
                         "owner_id": self.advertiser["party_id"]})])
        matches = self.lab.evaluate_task(task["task_id"])
        self.assertEqual([m["issue_code"] for m in matches], ["AUTO_REDIRECT"])
        self.assertEqual(matches[0]["responsible"][0]["party_id"],
                         self.advertiser["party_id"])

    def test_evaluation_is_pure_and_idempotent(self):
        task = self.make_task()
        self.run_track(task, "normal", [
            ad_shown("e1", placement="lockscreen_wallpaper",
                     close={"present": False})])
        first = self.lab.evaluate_task(task["task_id"])
        second = self.lab.evaluate_task(task["task_id"])
        self.assertEqual(first, second)
        self.assertEqual(len(self.lab.ledger.entries),
                         9)  # 主体3+应用+构建+设备+任务+会话+事件，判定不落库


class ReviewGateTest(LabFixture):
    def _flagged_finding(self):
        task = self.make_task()
        self.run_track(task, "normal", [
            ad_shown("e1", placement="lockscreen_wallpaper",
                     close={"present": False})])
        return self.lab.all_findings()[0]

    def test_suspected_finding_cannot_generate_notification(self):
        finding = self._flagged_finding()
        self.assertEqual(finding["status"], "suspected")
        with self.assertRaises(LabError) as ctx:
            self.lab.issue_notification(finding["finding_id"], "承办人甲")
        self.assertEqual(ctx.exception.status, 409)

    def test_confirmed_finding_yields_notification_with_deadline(self):
        finding = self._flagged_finding()
        self.lab.record_decision(finding["finding_id"], "confirm", "复核员乙")
        notice = self.lab.issue_notification(finding["finding_id"], "承办人甲")
        # 2025.1 版规范整改期 10 天，自复核确认之日起算
        self.assertEqual(notice["rectification_days"], 10)
        deadline = datetime.fromisoformat(
            notice["rectification_deadline"].replace("Z", "+00:00"))
        self.assertEqual(deadline, T0 + timedelta(days=10))
        self.assertEqual(notice["spec_version"], "2025.1")
        self.assertEqual(notice["rules_version"], RULES_ENGINE_VERSION)
        self.assertEqual(notice["clause"]["number"], "4.2")
        # 幂等：重复出具返回同一份材料
        again = self.lab.issue_notification(finding["finding_id"], "承办人甲")
        self.assertEqual(again["notification_id"], notice["notification_id"])

    def test_dismissed_finding_disappears_from_thread(self):
        finding = self._flagged_finding()
        self.lab.record_decision(finding["finding_id"], "dismiss", "复核员乙",
                                 rationale="证据为系统相册弹窗，非广告")
        thread = self.lab.issue_thread(
            self.app["app_id"], "LOCKSCREEN_NO_CLOSE", self.operator["party_id"])
        self.assertEqual(thread["windows"], [])

    def test_new_evidence_after_confirmation_requires_rereview(self):
        finding = self._flagged_finding()
        self.lab.record_decision(finding["finding_id"], "confirm", "复核员乙")
        # 同一构建另一台设备上又采到同类问题：并存成新时段，需再次复核
        device2 = self.lab.register_device("Redmi Note", "Android 13")
        task2 = self.make_task(device=device2)
        self.run_track(task2, "normal", [
            ad_shown("e9", placement="lockscreen_wallpaper",
                     close={"present": False})])
        findings = {f["finding_id"]: f for f in self.lab.all_findings()}
        self.assertEqual(findings[finding["finding_id"]]["status"], "confirmed")
        other = [f for f in findings.values()
                 if f["device_id"] == device2["device_id"]]
        self.assertEqual(len(other), 1)  # 不同设备各自成案、并存
        # 同一台设备再来一条新证据，则原案进入待再复核
        task3 = self.make_task()
        self.run_track(task3, "normal", [
            ad_shown("e10", placement="lockscreen_wallpaper",
                     close={"present": False}, at=20.0)])
        current = self.lab.get_finding(finding["finding_id"])
        self.assertEqual(current["status"], "confirmed_pending_review")
        with self.assertRaises(LabError):
            self.lab.issue_notification(finding["finding_id"], "承办人甲")


class ImmutabilityTest(LabFixture):
    def test_new_build_does_not_overwrite_old_evidence(self):
        task1 = self.make_task()
        self.run_track(task1, "normal", [
            ad_shown("e1", placement="lockscreen_wallpaper",
                     close={"present": False})])
        before = self.lab.get_finding(
            self.lab.all_findings()[0]["finding_id"])
        # 开发者提交新构建
        build_v2 = self.lab.register_build(self.app["app_id"], "3.3.0", ts(100))
        task2 = self.make_task(build=build_v2)
        self.run_track(task2, "normal", [ad_shown("e2")])  # 新构建表现正常
        after = self.lab.get_finding(before["finding_id"])
        self.assertEqual(before["windows"], after["windows"])
        self.assertEqual(before["event_ids"], after["event_ids"])

    def test_retest_pass_does_not_erase_problem_windows(self):
        task1 = self.make_task()
        self.run_track(task1, "normal", [
            ad_shown("e1", placement="lockscreen_wallpaper",
                     close={"present": False})])
        finding = self.lab.all_findings()[0]
        self.lab.record_decision(finding["finding_id"], "confirm", "复核员乙")
        # 新构建复测通过
        build_v2 = self.lab.register_build(self.app["app_id"], "3.3.0", ts(100))
        task2 = self.make_task(build=build_v2, retest_of=task1["task_id"])
        self.run_track(task2, "normal", [ad_shown("e2")])
        self.lab.record_retest(task2["task_id"], "pass", "复核员乙")
        kept = self.lab.get_finding(finding["finding_id"])
        self.assertEqual(kept["status"], "confirmed")
        self.assertEqual(len(kept["windows"]), 1)  # 问题时段仍在
        thread = self.lab.issue_thread(
            self.app["app_id"], "LOCKSCREEN_NO_CLOSE", self.operator["party_id"])
        self.assertEqual(len(thread["windows"]), 1)
        self.assertEqual(len(thread["passes"]), 1)
        self.assertEqual(thread["recurrence_count"], 0)

    def test_ledger_replay_restores_state_and_verifies(self):
        task = self.make_task()
        self.run_track(task, "normal", [
            ad_shown("e1", placement="lockscreen_wallpaper",
                     close={"present": False})])
        finding = self.lab.all_findings()[0]
        self.lab.record_decision(finding["finding_id"], "confirm", "复核员乙")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.jsonl")
            persisted = Lab(path, now=lambda: self.clock[0])
            persisted.parties = self.lab.parties  # 仅构造空壳，改用重放验证
            # 直接把现有账本写入文件再重放
            with open(path, "w", encoding="utf-8") as fh:
                for entry in self.lab.ledger.entries:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            replayed = Lab(path, now=lambda: self.clock[0])
            self.assertTrue(replayed.verify_ledger()["ok"])
            restored = replayed.get_finding(finding["finding_id"])
            self.assertEqual(restored["status"], "confirmed")
            self.assertEqual(restored["windows"], finding["windows"])

    def test_ledger_detects_tampering(self):
        task = self.make_task()
        self.run_track(task, "normal", [ad_shown("e1")])
        self.lab.ledger.entries[-1]["data"]["payload"]["placement"] = "banner"
        result = self.lab.verify_ledger()
        self.assertFalse(result["ok"])
        self.assertEqual(result["corrupt_at"], len(self.lab.ledger.entries) - 1)


class VersionPinningTest(LabFixture):
    def test_script_version_is_fixed_per_task(self):
        task1 = self.make_task(script="script-2026.1")
        # 脚本升级到 2026.2 后，新任务用新版本，旧任务保持原版本
        task2 = self.make_task(script="script-2026.2")
        self.assertEqual(self.lab.tasks[task1["task_id"]]["script_version"],
                         "script-2026.1")
        self.assertEqual(self.lab.tasks[task2["task_id"]]["script_version"],
                         "script-2026.2")

    def test_spec_version_changes_thresholds_and_deadline(self):
        close = {"present": True, "appears_after_ms": 3500, "enabled": True,
                 "rect_dp": {"w": 48, "h": 48},
                 "accessibility_focusable": True, "label": "关闭"}
        # 3500ms：2024.1 版（上限 5000ms）下合规，2025.1 版（上限 3000ms）下涉嫌
        task_old = self.make_task(track_spec="2024.1")
        self.run_track(task_old, "normal", [ad_shown("e1", close=close)])
        self.assertEqual(self.lab.evaluate_task(task_old["task_id"]), [])
        task_new = self.make_task(track_spec="2025.1")
        self.run_track(task_new, "normal", [ad_shown("e2", close=close)])
        matches = self.lab.evaluate_task(task_new["task_id"])
        self.assertEqual([m["issue_code"] for m in matches],
                         ["SPLASH_CLOSE_DEFICIENT"])
        finding = [f for f in self.lab.all_findings()
                   if f["issue_code"] == "SPLASH_CLOSE_DEFICIENT"][0]
        self.lab.record_decision(finding["finding_id"], "confirm", "复核员乙")
        notice = self.lab.issue_notification(finding["finding_id"], "承办人甲")
        self.assertEqual(notice["rectification_days"], 10)  # 2025.1 版期限


class RecurrenceTest(LabFixture):
    def test_relapse_after_pass_is_counted(self):
        # 第一次：v1 检出锁屏画报问题
        task1 = self.make_task()
        self.run_track(task1, "normal", [
            ad_shown("e1", placement="lockscreen_wallpaper",
                     close={"present": False}, at=1.0)])
        finding = self.lab.all_findings()[0]
        self.lab.record_decision(finding["finding_id"], "confirm", "复核员乙")
        # 第二次：v2 复测通过
        build_v2 = self.lab.register_build(self.app["app_id"], "3.3.0", ts(100))
        task2 = self.make_task(build=build_v2, retest_of=task1["task_id"])
        self.run_track(task2, "normal", [ad_shown("e2")])
        self.advance(days=5)
        self.lab.record_retest(task2["task_id"], "pass", "复核员乙")
        # 第三次：v3 又出现同样问题 → 回潮一次
        build_v3 = self.lab.register_build(self.app["app_id"], "3.4.0", ts(200))
        task3 = self.make_task(build=build_v3)
        self.run_track(task3, "normal", [
            ad_shown("e3", placement="lockscreen_wallpaper",
                     close={"present": False}, at=6 * 24 * 3600)])
        thread = self.lab.issue_thread(
            self.app["app_id"], "LOCKSCREEN_NO_CLOSE", self.operator["party_id"])
        self.assertEqual(thread["recurrence_count"], 1)
        self.assertEqual(len(thread["windows"]), 2)  # 两段问题时段都保留
        # 承办视图能看到该主体的回潮记录
        overview = self.lab.party_overview(self.operator["party_id"])
        self.assertEqual(overview["threads"][0]["recurrence_count"], 1)

    def test_retest_verdict_must_match_evidence(self):
        task1 = self.make_task()
        self.run_track(task1, "normal", [
            ad_shown("e1", placement="lockscreen_wallpaper",
                     close={"present": False})])
        task2 = self.make_task(retest_of=task1["task_id"])
        self.run_track(task2, "normal", [
            ad_shown("e2", placement="lockscreen_wallpaper",
                     close={"present": False})])
        # 证据仍有问题，不能记 pass
        with self.assertRaises(LabError) as ctx:
            self.lab.record_retest(task2["task_id"], "pass", "复核员乙")
        self.assertEqual(ctx.exception.code, "verdict_mismatch")


class JumpReportTest(LabFixture):
    def test_jump_report_answers_trigger_close_path_and_versions(self):
        task = self.make_task(script="script-2026.1")
        session = self.lab.start_session(task["task_id"], "screen_reader", ts(0))
        close = {"present": True, "appears_after_ms": 500, "enabled": True,
                 "rect_dp": {"w": 48, "h": 48},
                 "accessibility_focusable": False, "label": "关闭广告"}
        self.lab.ingest_events(session["session_id"], [
            ad_shown("e1", close=close),
            sensor("s1", magnitude=8.0, duration_ms=200),
            jump("j1", "shake", ad_event_id="e1"),
            jump("j2", "auto", ad_event_id="e1", stage="landing",
                 target={"url": "https://shop.example/y",
                         "owner_id": self.advertiser["party_id"]}, at=4.0)])
        report = self.lab.session_jump_report(session["session_id"])
        self.assertEqual(report["spec_version"], "2025.1")
        self.assertEqual(report["script_version"], "script-2026.1")
        self.assertEqual(report["rules_version"], RULES_ENGINE_VERSION)
        shake, auto = report["jumps"]
        # 跳转由谁触发
        self.assertEqual(shake["attributed_to"], "sdk")
        self.assertEqual(auto["attributed_to"], "advertiser")
        # 当时是否存在可操作的关闭路径（读屏轨迹下不可聚焦 → 不可操作）
        self.assertFalse(shake["close_path_at_jump"]["operable"])
        self.assertEqual(shake["close_path_at_jump"]["reason"], "not_focusable")


if __name__ == "__main__":
    unittest.main()
