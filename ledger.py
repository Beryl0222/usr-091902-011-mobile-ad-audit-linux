"""追加式证据账本。

所有会改变系统认知的事实（原始事件、复核决定、复测结论、告知材料）
都以条目形式追加，条目之间以哈希链相连，可整体重放校验。
账本只增不改：新构建、复测通过、脚本更新都不会触碰既有条目。
"""

import hashlib
import json
import os

GENESIS_HASH = "0" * 64


def canonical_json(obj):
    """生成键序稳定、无空白的 JSON，用于哈希与存储。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_event(event):
    """事件内容哈希：客户端事件标识 + 载荷，用于重传/迟到去重。"""
    return hashlib.sha256(canonical_json({
        "event_id": event["event_id"],
        "payload": event["payload"],
    }).encode("utf-8")).hexdigest()


def hash_entry(entry_type, data, prev_hash):
    body = canonical_json({"type": entry_type, "data": data, "prev": prev_hash})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Ledger:
    """内存账本，可选镜像到 JSONL 文件。"""

    def __init__(self, path=None):
        self.path = path
        self.entries = []
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self.entries.append(json.loads(line))

    def append(self, entry_type, data):
        entry = {
            "seq": len(self.entries),
            "type": entry_type,
            "data": data,
            "prev_hash": self.entries[-1]["hash"] if self.entries else GENESIS_HASH,
        }
        entry["hash"] = hash_entry(entry_type, data, entry["prev_hash"])
        self.entries.append(entry)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(canonical_json(entry) + "\n")
        return entry

    def verify(self):
        """校验哈希链完整性，返回 (是否完整, 首个损坏位置或 None)。"""
        prev = GENESIS_HASH
        for index, entry in enumerate(self.entries):
            if entry["seq"] != index or entry["prev_hash"] != prev:
                return False, index
            if hash_entry(entry["type"], entry["data"], prev) != entry["hash"]:
                return False, index
            prev = entry["hash"]
        return True, None
