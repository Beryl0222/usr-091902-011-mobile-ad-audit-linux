# 移动广告合规实验室

归集多设备广告行为、无障碍操作证据、规则判定与整改复测的后端服务。
针对锁屏画报、开屏弹窗、摇一摇广告"关不掉、误触跳转、整治后回潮"的专项治理场景。

## 核心原则

1. **证据只增不改**。原始事件、复核决定、复测结论、告知材料全部以哈希链追加进账本（`ledger.py`，JSONL 镜像，可整体重放与篡改校验）。规则判定是从账本重放的**纯函数读模型**，重跑不产生新证据；开发者提交新构建不触碰旧构建证据；复测通过不抹除此前问题时段。
2. **责任三分**：应用运营者 / 广告主 / 嵌入 SDK 提供方。归因来自证据本身——锁屏画报归应用自身；广告位关闭控件缺陷由运营者主责、SDK 次责；摇一摇与广告位自动跳转由 SDK 主责、运营者次责；落地页内自动跳转归广告主。
3. **版本三钉**：`spec_version`（规范版本，决定传感器阈值、关闭时限、热区尺寸与整改期限，如 2024.1 / 2025.1）、`script_version`（测试脚本版本，**任务创建时固化**，脚本更新只影响新任务）、`rules_version`（规则引擎版本）。每条问题与每份告知材料都带这三个版本，可回答"依据哪版规范"。
4. **自动规则只标记**：命中结果状态为 `suspected`；复核员 `confirm` 后才能出具告知材料，`dismiss` 即排除。确认后若同一案件出现新的问题时段，状态回到 `confirmed_pending_review`，须再次确认。
5. **同一构建跨设备并存**：问题主键 = 问题代码 + 构建 + 设备 + 轨迹 + 首要责任主体，不同设备/轨迹各自成案。
6. **采集幂等**：事件以 `event_id + 载荷哈希` 去重，迟到、乱序、重传均安全；相同 `event_id` 不同载荷按冲突拒绝。

## 三条操作轨迹

| 轨迹 | 说明 | 特殊判定 |
|---|---|---|
| `normal` | 正常用户 | 关闭入口出现时限、热区 ≥48dp（2025.1） |
| `screen_reader` | 读屏用户 | 关闭入口须可聚焦、有朗读文案，否则 `CLOSE_NOT_ACCESSIBLE` |
| `elderly` | 老人模式 | 热区 ≥56dp，锁屏画报全程须可关闭 |

## 问题主线与回潮

`GET /parties/{id}/overview` 按责任主体给出在办问题、整改期限（告知后 10 天，2025.1）、是否逾期。
问题主线（`issue_thread`）在时间线上归并：问题时段 → 复测通过点 → 再次出现的问题时段记一次**回潮**；多次复测通过可累计多次回潮，历史问题时段始终保留。

## API 速览

```
POST /parties                     登记责任主体（type: app_operator|advertiser|sdk_provider）
POST /apps                        登记应用（绑定运营主体）
POST /builds                      登记应用构建（sdk_map 把广告位 SDK 映射到责任主体，不可变）
POST /devices                     登记设备（型号 + 系统版本）
POST /tasks                       创建测试任务（钉住 script_version / spec_version，retest_of 表示复测任务）
POST /sessions                    开始一条轨迹（track: normal|screen_reader|elderly）
POST /sessions/{id}/events        批量上报事件（ad_shown/sensor/jump/network/close_attempt…），重传自动去重
POST /tasks/{id}/evaluate         执行规则，返回涉嫌问题（不落库）
GET  /findings                    全部问题及状态（suspected/confirmed/.../dismissed）
POST /findings/{id}/decisions     复核员确认/排除（快照当时全部证据哈希）
POST /findings/{id}/notifications 出具告知材料（仅 confirmed；含条款、整改期限、责任主体；幂等）
POST /tasks/{id}/retests          记录复测结论（必须与证据一致，否则拒绝）
GET  /sessions/{id}/jumps         单次轨迹跳转台账：谁触发、当时关闭路径是否可操作、版本依据
GET  /parties/{id}/overview       按责任主体汇总整改期限与回潮记录
GET  /ledger/verify               哈希链完整性校验
```

## 运行与测试

```bash
python3 service.py --check          # 基础配置检查
python3 service.py --port 8000      # 内存模式
python3 service.py --ledger data/ledger.jsonl   # 持久化模式，重启自动重放
npm test                            # 27 项契约测试（含原健康检查契约）
```

`fixtures/domain.json` 保存领域名词与状态称谓，供接口联调对齐语义。
