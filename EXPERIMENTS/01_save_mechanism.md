# 实验 01:NetEase iPad 存档的保存机制与减法回溯可行性

## 1. 背景

(简述:已 ship 的 MCCE Converter 揭示了 NetEase 在 LevelDB 之上的加密封装,
但未触及"NetEase 客户端在何种节奏、何种粒度上向 LevelDB 写入"的问题。
本实验意在通过受控的 snapshot 序列,理解写入模式,
进而判断"对已保存存档做减法以回到历史状态"在工程上是否可行。)

## 2. 实验要回答的具体问题

1. iPad NetEase 一次"保存"实际写入的字节量级是多少?
2. 多少次保存会触发一次 memtable flush(L0 新增一个 SSTable)?
3. 网易是否改动了 LevelDB 默认参数?(间接观察)
4. compaction 发生后,L0 旧 SSTable 是否在磁盘上完全消失?
5. 在 flush 触发之前的窗口内,把 db/ 整体替换回旧 snapshot 能否回到旧状态?

## 3. 实验前提与边界

- 测试存档必须是全新建的、从未使用过的
- 每次保存之间的改动要最小化且可重复
- 每次导出后完整拷贝整个目录到 Mac,文件名带序号
- iPad 客户端"保存"的触发时机未知,Phase 0 顺带探测
- 不可消除的不确定性:iPad 在导出时是否做额外操作(强制 flush / 整理)
  在无 jailbreak 条件下无法验证

## 4. snapshot 存放约定

- 仓库外路径:`~/Projects/MCCE_Experiments/time_travel/`
- 命名:`snap_NN_<short_description>/`(原始)
        `snap_NN_<short_description>_decrypted/`(mcce decrypt 输出)
- NN 从 00 开始,单调递增,不复用

## 5. 实验设计

### Phase 0:基线与保存触发机制

| snap | 动作 |
|------|------|
| 00 | 新建世界(超平坦/和平/关天气与随机刻),进入立刻退出 |
| 01 | 进入,不动 30 秒,退出 |
| 02 | 进入,不动 5 分钟,退出 |

观测:
- 00 vs 01:纯进出是否产生写入
- 01 vs 02:是否有时间触发的自动保存

### Phase 1:单次最小改动

| snap | 动作 |
|------|------|
| 03 | 在 02 基础上,放 1 个石头方块,退出 |
| 04 | 再放 1 个,退出 |
| 05-10 | 各放 1 个,退出(共 8 次单方块保存) |

观测:
- `.log` 是否单调增长
- `.log` 文件名是否变化(变化 = 发生过 flush)
- 是否有新 `.ldb` 出现
- 旧 `.ldb` 是否消失

### Phase 2:推动 flush 与 compaction

若 Phase 1 未观察到 flush,加大改动:

| snap | 动作 |
|------|------|
| 11 | 飞到 200 格外,放 16×16×16 方块堆,退出 |
| 12-15 | 各去新位置放大方块堆(强制新 chunk),退出 |

观测:第一次出现新 `.ldb` 的 snap;第一次出现 `.ldb` 减少(compaction)的 snap。

### Phase 3:减法可行性验证

假设 snap_07 和 snap_08 之间 `.ldb` 未变化、只有 `.log` 增长。
测试:
1. 拷贝 snap_08 整体为 snap_08_TEST
2. 用 snap_07/db/ 完整替换 snap_08_TEST/db/
3. 用 mcce encrypt 重新加密(保持账号 keystream 一致)
4. 推回 iPad,打开世界,验证状态是否回到 snap_07
5. 若不一致,记录差异(level.dat?玩家位置?背包?)

## 6. 每个 snapshot 要记录的字段
snap_NN_<desc>

时间: YYYY-MM-DD HH:MM
iPad 动作: <精确动作描述>
iPad 内消耗时间: <估计>
导出后目录大小: <KB>
导出后 db/ 内容(由 inspect_db.py 生成):
<文件名>  <字节数>  sha256:<前12位>
...
CURRENT 指向: <内容>
异常或意外: <无 / 描述>


## 7. 执行日志(按时间追加,只追加不修改)

### snap_00_initial
- 时间: 2026-05-19 HH:MM  (填你实际导出的时间)
- iPad 动作: 新建世界(设置见下),进入后立即退出
- iPad 内消耗时间: ~30 秒
- 世界设置:
    - 模式: 创造 / 和平 / 平坦
    - 作弊: 开
    - 模拟距离: 4
    - 昼夜更替/生物生成/生物破坏/实体掉落战利品/生物战利品/方块掉落/
      火焰蔓延/TNT 爆炸/重生方块爆炸/自然生命恢复/奖励箱/初始地图/命令方块:
      全部关闭
    - randomTickSpeed: 进入后命令设为 0
- 导出后目录大小: 40K
- 导出后 db/ 内容:
  CURRENT             16 bytes  sha256:0861415cada6
      → MANIFEST-000004
  MANIFEST-000004     50 bytes  sha256:b51bc53f3c43
  000005.log       11243 bytes  sha256:70c5f5aa3373
  TOTAL            11309 bytes  (3 files)

### snap_01_noop_30s
- 时间: 2026-05-19 HH:MM  (填实际时间)
- iPad 动作: 进入 snap_00 世界,不移动、不开背包、不开聊天框,等待约 30 秒,从游戏内退出按钮退出世界
- iPad 内消耗时间: ~30 秒
- 导出后目录大小: 52 KB
- 导出后 db/ 内容:
  CURRENT             16 bytes  sha256:4166bf17b2da
      → MANIFEST-000006
  MANIFEST-000006    104 bytes  sha256:da5f20c4ab17
  000008.log       20069 bytes  sha256:8ef1d9939f37
  000007.ldb        2716 bytes  sha256:c9013fbe356b
  TOTAL            22905 bytes  (4 files)
- 与 snap_00 对比:
    - CURRENT: MANIFEST-000004 → MANIFEST-000006 (+2)
    - MANIFEST 大小: 50 → 104 (+54)
    - log: 000005.log (11243) → 000008.log (20069),编号跳 3,大小 +8826
    - **L0 出现第一个 SSTable**: 000007.ldb (2716 bytes)
    - 文件总数: 3 → 4
- 解读:
    - 一次"进入 + 30 秒静止 + 退出"消耗了 3 个文件编号(6/7/8),
      推测启动序列为:新 MANIFEST-000006 → 把旧 log 000005 flush 成
      000007.ldb → 开新 log 000008 接收后续写入
    - 关键发现:**NetEase 客户端退出世界时强制 flush**(或客户端启动时
      把上一次的 .log 直接 flush 成 .ldb)。每次进出 = L0 多一个文件
    - 30 秒"什么都不做"期间 log 仍增长 ~9 KB,说明 NetEase 有后台写入
      (玩家位置时间戳 / tick 计数 / 客户端状态等)
- 异常或意外: 无


## 8. 当前结论(随实验推进重写,标注版本)

(实验开始后在此填写)

## 9. 待解决的不确定性

- iPad 在导出时是否强制 flush?(无 jailbreak 无法验证)
- 网易是否改动了 LevelDB 默认参数?(可间接推断,无法直读)
- (随实验追加)