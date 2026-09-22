"""规范版本与规则引擎。

规则只负责"标出涉嫌问题"，不做违规定性；每条命中都记录：
触发证据、规范条款、责任主体及归因依据。规则引擎自身带版本号，
与测试脚本版本、规范版本共同构成一次判定的版本依据。
"""

from dataclasses import dataclass

# 三条操作轨迹
TRACK_NORMAL = "normal"            # 正常用户
TRACK_SCREEN_READER = "screen_reader"  # 读屏用户
TRACK_ELDERLY = "elderly"          # 老人模式
TRACKS = (TRACK_NORMAL, TRACK_SCREEN_READER, TRACK_ELDERLY)

# 广告位
PLACEMENT_LOCKSCREEN = "lockscreen_wallpaper"  # 锁屏画报
PLACEMENT_SPLASH = "splash"                    # 开屏弹窗
PLACEMENT_INTERSTITIAL = "interstitial"        # 插屏
PLACEMENT_SHAKE = "shake_banner"               # 摇一摇广告位

# 问题代码
CODE_LOCKSCREEN_NO_CLOSE = "LOCKSCREEN_NO_CLOSE"        # 锁屏画报无有效关闭路径
CODE_SPLASH_CLOSE_DEFICIENT = "SPLASH_CLOSE_DEFICIENT"  # 开屏/插屏关闭入口缺失或不合规
CODE_CLOSE_NOT_ACCESSIBLE = "CLOSE_NOT_ACCESSIBLE"      # 关闭入口读屏不可达
CODE_SHAKE_MISTRIGGER = "SHAKE_MISTRIGGER"              # 摇一摇低阈值误触跳转
CODE_AUTO_REDIRECT = "AUTO_REDIRECT"                    # 无用户意图自动跳转

RULES_ENGINE_VERSION = "rules-2026.1.0"
DEFAULT_SPEC_VERSION = "2025.1"


@dataclass(frozen=True)
class Spec:
    version: str
    effective_date: str
    rectification_days: int
    params: dict
    clauses: dict


SPECS = {
    "2024.1": Spec(
        version="2024.1",
        effective_date="2024-03-01",
        rectification_days=15,
        params={
            "close_appear_max_ms": 5000,      # 关闭入口最迟出现时间
            "absence_observation_ms": 5000,  # 判定"始终不出现"所需观测时长
            "target_min_dp": 44,
            "elderly_target_min_dp": 44,
            "shake_min_magnitude": 15.0,     # m/s^2
            "shake_min_duration_ms": 2000,
            "shake_min_rotation_deg": 20.0,
            "shake_correlation_ms": 1500,    # 传感器采样与跳转的最大关联间隔
        },
        clauses={
            CODE_LOCKSCREEN_NO_CLOSE: {
                "number": "4.2",
                "title": "锁屏画报应提供可操作的关闭入口",
                "text": "应用以锁屏画报形式展示广告的，应当在展示期间提供持续可操作、"
                        "可被无障碍服务定位的关闭入口。",
            },
            CODE_SPLASH_CLOSE_DEFICIENT: {
                "number": "5.1",
                "title": "开屏与插屏广告关闭入口要求",
                "text": "开屏、插屏广告应在规定时限内出现清晰可辨的关闭入口，"
                        "触控热区不得小于规定尺寸，不得使用虚假关闭按钮。",
            },
            CODE_CLOSE_NOT_ACCESSIBLE: {
                "number": "5.3",
                "title": "关闭入口的无障碍可达性",
                "text": "关闭入口应可被读屏服务聚焦并朗读其用途，不得以纯图形方式"
                        "提供且无内容描述。",
            },
            CODE_SHAKE_MISTRIGGER: {
                "number": "6.2",
                "title": "摇一摇交互触发阈值",
                "text": "不得以摇动作为广告跳转的唯一或低门槛触发方式；触发时的加速度、"
                        "持续时间与转动角度应同时达到规定下限。",
            },
            CODE_AUTO_REDIRECT: {
                "number": "6.4",
                "title": "禁止无用户意图自动跳转",
                "text": "未经用户主动操作，不得从广告位或广告落地页自动跳转至第三方页面。",
            },
        },
    ),
    "2025.1": Spec(
        version="2025.1",
        effective_date="2025-06-01",
        rectification_days=10,
        params={
            "close_appear_max_ms": 3000,
            "absence_observation_ms": 4000,
            "target_min_dp": 48,
            "elderly_target_min_dp": 56,
            "shake_min_magnitude": 18.0,
            "shake_min_duration_ms": 3000,
            "shake_min_rotation_deg": 35.0,
            "shake_correlation_ms": 1500,
        },
        clauses={
            CODE_LOCKSCREEN_NO_CLOSE: {
                "number": "4.2",
                "title": "锁屏画报应提供可操作的关闭入口",
                "text": "应用以锁屏画报形式展示广告的，应当在展示期间提供持续可操作、"
                        "可被无障碍服务定位的关闭入口；老人模式下热区应同步放大。",
            },
            CODE_SPLASH_CLOSE_DEFICIENT: {
                "number": "5.1",
                "title": "开屏与插屏广告关闭入口要求",
                "text": "开屏、插屏广告应在规定时限内出现清晰可辨的关闭入口，"
                        "触控热区不得小于规定尺寸，老人模式下应使用更大热区。",
            },
            CODE_CLOSE_NOT_ACCESSIBLE: {
                "number": "5.3",
                "title": "关闭入口的无障碍可达性",
                "text": "关闭入口应可被读屏服务聚焦并朗读其用途，焦点顺序应先于广告主内容。",
            },
            CODE_SHAKE_MISTRIGGER: {
                "number": "6.2",
                "title": "摇一摇交互触发阈值",
                "text": "不得以摇动作为广告跳转的唯一或低门槛触发方式；触发时的加速度、"
                        "持续时间与转动角度应同时达到规定下限。",
            },
            CODE_AUTO_REDIRECT: {
                "number": "6.4",
                "title": "禁止无用户意图自动跳转",
                "text": "未经用户主动操作，不得从广告位或广告落地页自动跳转至第三方页面；"
                        "落地页内的二次自动跳转由广告主承担责任。",
            },
        },
    ),
}


def _min_target_dp(spec, track):
    if track == TRACK_ELDERLY:
        return spec.params["elderly_target_min_dp"]
    return spec.params["target_min_dp"]


def close_path_assessment(ad_event, at_ms, track, spec):
    """评估广告在展示后 at_ms 时刻是否存在可操作的关闭路径。

    返回 (是否可操作, 原因)。屏幕朗读轨迹下额外要求可聚焦、有朗读文案。
    """
    close = ad_event.get("close") or {}
    if not close.get("present"):
        return False, "no_close_entry"
    appears = close.get("appears_after_ms")
    if appears is not None and appears > at_ms:
        return False, "not_yet_appeared"
    if close.get("enabled") is False:
        return False, "disabled"
    rect = close.get("rect_dp") or {}
    if rect:
        shortest = min(rect.get("w", 0), rect.get("h", 0))
        if shortest < _min_target_dp(spec, track):
            return False, "touch_target_too_small"
    if track == TRACK_SCREEN_READER:
        if not close.get("label"):
            return False, "missing_screen_reader_label"
        if close.get("accessibility_focusable") is False:
            return False, "not_focusable"
    return True, "operable"


def _parties_for_ad(code, ad_event, operator_party, sdk_party):
    """关闭入口类问题的责任链：应用运营者承担集成责任，SDK 承担次要责任。"""
    parties = [{"party_id": operator_party, "role": "primary",
                "basis": "关闭入口由应用集成并对最终展示负责"}]
    if sdk_party:
        parties.append({"party_id": sdk_party, "role": "secondary",
                        "basis": "广告位由该 SDK 渲染，关闭控件随 SDK 下发"})
    return parties


def _parties_for_ad_unit(ad_event, operator_party, sdk_party, basis):
    if sdk_party:
        return [
            {"party_id": sdk_party, "role": "primary", "basis": basis},
            {"party_id": operator_party, "role": "secondary",
             "basis": "应用运营者对嵌入 SDK 的行为负有审核与管理责任"},
        ]
    return [{"party_id": operator_party, "role": "primary", "basis": basis}]


def _evidence_around(ids, events_by_id):
    """补充与广告相关的网络往来、关闭尝试作为佐证，保持按时间排序去重。"""
    result = list(ids)
    for eid in list(ids):
        ad = events_by_id.get(eid)
        if not ad or ad.get("type") != "ad_shown":
            continue
        for ev in events_by_id.values():
            if ev.get("correlates_event_id") == eid and ev["event_id"] not in result:
                result.append(ev["event_id"])
            if ev.get("type") == "close_attempt" and ev.get("target_event_id") == eid \
                    and ev["event_id"] not in result:
                result.append(ev["event_id"])
    ordered = sorted(result, key=lambda x: events_by_id[x]["ts"])
    return ordered


def evaluate_session(spec, app_id, build_id, device_id, track, session_id, events):
    """对一条操作轨迹执行规则，返回命中列表（仅"涉嫌"，不做违规定性）。"""
    by_id = {e["event_id"]: e for e in events}
    matches = []
    params = spec.params

    def window_for(evidence_ids):
        ts_list = [by_id[i]["ts"] for i in evidence_ids if i in by_id]
        return {"start": min(ts_list), "end": max(ts_list)}

    def add(code, evidence, detail, parties):
        evidence = _evidence_around(evidence, by_id)
        matches.append({
            "issue_code": code,
            "clause": spec.clauses[code]["number"],
            "app_id": app_id,
            "build_id": build_id,
            "device_id": device_id,
            "track": track,
            "session_id": session_id,
            "event_ids": evidence,
            "window": window_for(evidence),
            "detail": detail,
            "responsible": parties,
        })

    # 一、广告展示与关闭入口
    for ad in events:
        if ad.get("type") != "ad_shown":
            continue
        close = ad.get("close") or {}
        placement = ad.get("placement")
        observed = ad.get("observed_for_ms", 0)
        operator_party = ad["_operator_party_id"]
        sdk_party = ad.get("_sdk_party_id")
        evidence = [ad["event_id"]]

        if placement == PLACEMENT_LOCKSCREEN:
            operable, reason = close_path_assessment(
                ad, observed, track, spec)
            if not operable:
                add(CODE_LOCKSCREEN_NO_CLOSE, evidence,
                    {"reason": reason, "observed_for_ms": observed,
                     "close": close},
                    [{"party_id": operator_party, "role": "primary",
                      "basis": "锁屏画报由应用申请设备能力承载，属于应用自身行为"}])
            continue

        if placement not in (PLACEMENT_SPLASH, PLACEMENT_INTERSTITIAL, PLACEMENT_SHAKE):
            continue

        # 读屏轨迹单独成案：控件视觉上合规但读屏不可达
        if track == TRACK_SCREEN_READER and close.get("present"):
            appears = close.get("appears_after_ms") or 0
            if appears <= params["close_appear_max_ms"] and close.get("enabled") is not False:
                operable, reason = close_path_assessment(ad, max(appears, observed), track, spec)
                if not operable and reason in ("missing_screen_reader_label", "not_focusable"):
                    add(CODE_CLOSE_NOT_ACCESSIBLE, evidence,
                        {"reason": reason, "close": close},
                        _parties_for_ad(CODE_CLOSE_NOT_ACCESSIBLE, ad, operator_party, sdk_party))

        operable, reason = close_path_assessment(ad, observed, TRACK_NORMAL, spec)
        # 老人轨迹下按大热区重新判定尺寸
        if track == TRACK_ELDERLY and reason == "operable":
            operable, reason = close_path_assessment(ad, observed, TRACK_ELDERLY, spec)
        absent_conclusive = (not close.get("present")
                             and observed >= params["absence_observation_ms"])
        late = close.get("present") and (close.get("appears_after_ms") or 0) > params["close_appear_max_ms"]
        if (not operable and reason in ("no_close_entry", "disabled", "touch_target_too_small")
                and (absent_conclusive or reason != "no_close_entry")) or late:
            final_reason = "appeared_late" if late else reason
            add(CODE_SPLASH_CLOSE_DEFICIENT, evidence,
                {"reason": final_reason, "observed_for_ms": observed,
                 "close_appear_max_ms": params["close_appear_max_ms"],
                 "required_target_dp": _min_target_dp(spec, track),
                 "close": close},
                _parties_for_ad(CODE_SPLASH_CLOSE_DEFICIENT, ad, operator_party, sdk_party))

    # 二、跳转：摇一摇误触 / 无意图自动跳转
    sensors = sorted((e for e in events if e.get("type") == "sensor"),
                     key=lambda e: e["ts"])
    for jump in events:
        if jump.get("type") != "jump":
            continue
        ad = by_id.get(jump.get("ad_event_id")) if jump.get("ad_event_id") else None
        operator_party = jump["_operator_party_id"]
        sdk_party = ad.get("_sdk_party_id") if ad else None

        if jump.get("trigger") == "shake":
            latest = None
            for s in sensors:
                if s["ts"] <= jump["ts"]:
                    latest = s
            if latest:
                below = []
                if latest.get("magnitude", 0) < params["shake_min_magnitude"]:
                    below.append("magnitude")
                if latest.get("duration_ms", 0) < params["shake_min_duration_ms"]:
                    below.append("duration")
                rotation = latest.get("rotation_deg")
                if rotation is not None and rotation < params["shake_min_rotation_deg"]:
                    below.append("rotation")
                if below:
                    evidence = [latest["event_id"], jump["event_id"]]
                    if ad:
                        evidence.insert(0, ad["event_id"])
                    add(CODE_SHAKE_MISTRIGGER, evidence,
                        {"below_thresholds": below,
                         "sensor": {k: latest.get(k) for k in
                                    ("magnitude", "duration_ms", "rotation_deg")},
                         "thresholds": {
                             "magnitude": params["shake_min_magnitude"],
                             "duration_ms": params["shake_min_duration_ms"],
                             "rotation_deg": params["shake_min_rotation_deg"]},
                         "target": jump.get("target")},
                        _parties_for_ad_unit(
                            ad, operator_party, sdk_party,
                            "摇一摇广告位的传感器阈值与跳转逻辑由该 SDK 实现"))

        elif jump.get("trigger") == "auto":
            stage = jump.get("stage", "ad_unit")
            evidence = [jump["event_id"]] + ([ad["event_id"]] if ad else [])
            target = jump.get("target") or {}
            if stage == "landing":
                owner = target.get("owner_id")
                parties = [{"party_id": owner, "role": "primary",
                            "basis": "跳转发生在广告主落地页内，落地页行为由广告主负责"}]
                code_detail = "landing_auto_redirect"
            else:
                parties = _parties_for_ad_unit(
                    ad, operator_party, sdk_party,
                    "广告位在无用户操作情况下发起跳转，跳转逻辑由广告 SDK 实现")
                code_detail = "ad_unit_auto_redirect"
            add(CODE_AUTO_REDIRECT, evidence,
                {"reason": code_detail, "stage": stage, "target": target},
                parties)

    return matches


def finding_key(match):
    """同一问题在不同构建、设备、轨迹、责任主体下各自成案、并存不覆盖。"""
    primary = match["responsible"][0]["party_id"]
    return "|".join([match["issue_code"], match["build_id"],
                     match["device_id"], match["track"], primary])
