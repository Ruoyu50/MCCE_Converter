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

### snap_02_noop_5min
- 时间: 2026-05-19 HH:MM
- iPad 动作: 进入 snap_01 世界,不移动、不开背包、不开聊天框,等待约 5 分钟,
  从游戏内退出按钮退出世界
- iPad 内消耗时间: ~5 分钟
- 导出后目录大小: 132 KB
- 导出后 db/ 内容:
  CURRENT             16 bytes  sha256:b3524365a633
    → MANIFEST-000009
  MANIFEST-000009    257 bytes  sha256:0dedf1dacea1
  000011.log      100037 bytes  sha256:c93b14c8f8d8
  000012.ldb        2704 bytes  sha256:2930fc65064d
  TOTAL           103014 bytes  (4 files)
- 与 snap_01 对比:
    - CURRENT: MANIFEST-000006 → MANIFEST-000009 (+3)
    - MANIFEST 大小: 104 → 257 (+153)
    - log: 000008.log (20069) → 000011.log (100037),编号 +3,大小 ×5
    - ldb: 000007.ldb (2716) → 000012.ldb (2704),**旧 ldb 消失,新 ldb 出现**
    - 文件总数: 4 → 4 (不变)
- 解读:
    - **进出世界 = 一次完整的 LSM 重组**:旧 log flush + 旧 ldb 被合并 + 新 log + 新 ldb
    - ldb 数量始终保持 1 个,文件名滚动变化。和标准 LSM "积累 L0 到阈值才 compaction" 不同
    - 5 分钟 idle 期间 log 增长 ~80 KB,折算速率 ~270 bytes/秒,与 snap_01 的 ~300 bytes/秒 接近
    - NetEase 客户端在世界打开期间有稳定的后台写入,与玩家操作无关
- 异常或意外: 无

### snap_03_one_block
- 时间: 2026-05-19 HH:MM
- iPad 动作: 进入 snap_02 世界,在出生点附近放 1 个圆石方块,立即退出
- iPad 内消耗时间: <30 秒
- 导出后目录大小: 48 KB
- 导出后 db/ 内容:
  CURRENT             16 bytes  sha256:e6325e36f681
      → MANIFEST-000013
  MANIFEST-000013    281 bytes  sha256:0fabdad57a29
  000015.log       15122 bytes  sha256:acb9359760d8
  000016.ldb        2703 bytes  sha256:1f18e9382b18
  TOTAL            18122 bytes  (4 files)
- 与 snap_02 对比:
    - CURRENT: MANIFEST-000009 → MANIFEST-000013 (+4)
    - MANIFEST 大小: 257 → 281 (+24)
    - log: 000011.log (100037) → 000015.log (15122),编号 +4,
      大小骤降(idle 时间短)
    - ldb: 000012.ldb (2704) → 000016.ldb (2703),旧消失新出现,大小几乎相同,
      sha256 完全不同
    - 文件总数: 4 → 4
- 解读:
    - **第三个 snapshot 锁定模式**:每次进出世界后,LevelDB 总是回到
      "1 个 log + 1 个 ldb + 1 个 MANIFEST + 1 个 CURRENT" 的形态
    - 单方块改动在 ldb 字节数上不可见(2716→2704→2703,几乎不变),
      但 sha256 每次都不同,内容确实变了
    - 模式锁定:每次进出 = 强制 compaction,L0 永远只有 1 个 ldb,
      标准 LSM 的 kL0_CompactionTrigger 行为在 NetEase iPad 上不存在
- 异常或意外: 无

### snap_04_big_chunk
- 时间: 2026-05-19 HH:MM
- iPad 动作: 进入 snap_03 世界,切创造飞行,沿 +X 方向飞 200 格,
  放 16×16×16 圆石堆,立即退出
- iPad 内消耗时间: ~3 分钟
- 导出后目录大小: 244 KB
- 导出后 db/ 内容:
  CURRENT             16 bytes  sha256:7d3d7c3b6e16
      → MANIFEST-000017
  MANIFEST-000017   1063 bytes  sha256:f53195207c9f
  000036.log      209349 bytes  sha256:cca9face2d47
  000038.ldb        4212 bytes  sha256:5dcc3f045669
  TOTAL           214640 bytes  (4 files)
- 与 snap_03 对比:
    - CURRENT: MANIFEST-000013 → MANIFEST-000017 (+4)
    - MANIFEST 大小: 281 → 1063 (+782, 3.7×),记录了多次 VersionEdit
    - log: 000015 → 000036,**编号跳 +21**(之前都是 +3 或 +4)
    - log 大小: 15122 → 209349,反映退出时新 log 接收的少量写入
    - ldb: 000016 (2703) → 000038 (4212),旧消失新出现,大小 +1509
    - 文件总数: 4 → 4(仍然单 ldb!)
- 解读:
    - **形态不变**:大改动 + 200 格飞行后仍然回到"1 log + 1 ldb"标准形态
    - **编号跳 +21 与 MANIFEST 大小翻倍是独立验证**:期间发生了 ~10 次
      flush + ~10 次 compaction(每次写一条 VersionEdit,消耗若干编号)
    - **推断 memtable 阈值远小于 LevelDB 默认 4 MB**:
      总写入量估计几百 KB ~ 1-2 MB,但触发了 ~10 次 flush,
      推断 NetEase 把 write_buffer_size 调到 ~64-128 KB(为 iPad 节省内存)
    - **退出时强制完整 compaction**:无论中间 L0 累积到多少,
      退出时都合并成单 ldb。这才是 NetEase 客户端真正非标准的行为
- 异常或意外: 无

## 8. 当前结论(随实验推进重写,标注版本)

## 8. 当前结论(随实验推进重写,标注版本)

### v1 (2026-05-19)

#### 核心发现

NetEase iPad 客户端的 LevelDB 写入行为大体遵循标准 LSM,但有一个关键
非标准行为:**每次退出世界时强制完整 compaction**,所有 L0 ldb 被合并成
单个 ldb 落盘。

具体形态:每次"进入 + 退出"循环后,db/ 目录稳定回到
"1 个 .log + 1 个 .ldb + 1 个 MANIFEST + 1 个 CURRENT"的 4 文件形态,
无论世界打开期间发生过多少次 flush 或 compaction。

#### 支撑证据(4 个独立 snapshot)

| snap | iPad 动作 | log+ldb 形态 | 文件编号跳跃 |
|------|-----------|-------------|------------|
| 00 | 新建立即退出 | 1 log,0 ldb | -          |
| 01 | 进入 + 30s + 退出 | 1 log + 1 ldb | +3 |
| 02 | 进入 + 5min + 退出 | 1 log + 1 ldb(替换) | +3 |
| 03 | 进入 + 放 1 方块 + 退出 | 1 log + 1 ldb(替换) | +4 |
| 04 | 进入 + 飞 200 格 + 大方块堆 + 退出 | 1 log + 1 ldb(替换) | **+21** |

snap_04 的编号跳 +21 与 MANIFEST 大小从 281 字节增至 1063 字节同时出现,
独立验证了"期间发生了多次 flush + compaction,但退出时全部合并成单文件"。

#### 关于 NetEase 的 LevelDB 参数推断

snap_04 显示在估计几百 KB 到 1-2 MB 写入量内触发了约 10 次 flush,推断
NetEase 把 `write_buffer_size` 调到约 64-128 KB(标准 LevelDB 默认 4 MB),
推测原因是 iPad 内存预算受限。

#### 对"减法回溯"可行性的判断

**不可行**。

每次进出世界时,退出过程中的强制 compaction 会把所有 L0 ldb 合并成单个
ldb。**合并过程中,被新值覆盖的 key 的旧 value 字节级物理消失**——
不在新 ldb 里,不在 log 里,不在 MANIFEST 里,不在磁盘任何位置。

这意味着:
- 从 snap_N 的磁盘文件中,**无法**通过任何算法重构 snap_(N-1) 的状态
- snap_N 的 ldb 与 snap_(N-1) 的 ldb 是字节级完全不同的两个文件
- LevelDB 标准的"L0 累积到阈值才 compaction"窗口在 NetEase iPad 上不存在
  (因为退出时强制 compaction,L0 永远归零)

#### 对存档版本管理的实际含义

NetEase iPad 上的存档版本管理,**只能**通过外部完整目录快照实现
(等价于 git add . 全量提交,无法做增量)。任何"事后从单一存档推回历史"
的尝试在物理上不可能。

具体到 2022 年的老存档:在没有当年快照的前提下,无法恢复到任何历史状态。

#### 实验未能覆盖的边界

1. **iPad 导出操作本身是否独立于退出触发额外 compaction**:无法验证。
   所有 snapshot 都是"退出 + 导出"后抓的,无法区分两者各自的贡献
2. **NetEase 客户端的具体 LevelDB 参数**:推断但未直读源码或反编译验证
3. **崩溃/强制结束应用是否绕过退出时 compaction**:未测试。如果绕过,
   理论上能保留多 ldb 状态,但实践上不可靠且会造成 .log 数据丢失
4. **大型真实存档(GB 级)是否仍维持单 ldb 形态**:未测试。可能存在
   L1+ 层级的多文件状态,但 snap_04 这个量级看不出来

## 9. 待解决的不确定性

- iPad 在导出时是否强制 flush?(无 jailbreak 无法验证)
- 网易是否改动了 LevelDB 默认参数?(可间接推断,无法直读)
- (随实验追加)

## 10. 未探的边界与可证伪假设

v1 结论("减法回溯在 db/ 观察窗口内不可行")是在 4 个 snapshot 的
基础上锁定的,锁定范围限于 db/ 目录内的 LevelDB 文件。db/ 之外的
潜在信息源,以及 db/ 内未细查的副作用,都不在 v1 结论的覆盖范围内。

下面列出已识别但未验证的边界,每条以"可证伪假设 + 测试方法 + 可能结果"
形式给出。这些都是后续实验的候选,不是必做项。

### 假设 A:iPad NetEase 客户端在 LevelDB 之外保留存档数据副本

来源:NetEase 客户端可能为了同步 / 统计 / 崩溃恢复 / 成就上传等目的,
在应用沙箱内保留 LevelDB 之外的额外文件,或在 iCloud 备份链路中
留下完整或部分存档副本。

测试方法:
- 在 Mac 上做 iPad 的 iTunes/Finder 完整备份
- 在备份目录中 grep `level.dat` 字节签名,或 chunk 数据典型模式
- 检查 NetEase 应用沙箱(`AppDomain-com.netease.mc`)的所有文件,
  排除 db/ 后看剩余部分

可能结果:
- 找到副本 → "回溯不可能"在此层面被推翻,需要重新评估
- 没找到 → 这条边界被压实,v1 结论扩展到 iPad 文件系统层

### 假设 B:LevelDB 中存在摘要式 key,包含跨时间的累积信息

来源:Minecraft Bedrock 在 LevelDB 里可能写入了某些"统计型" key
(例如玩家累积时间、chunk 修改计数、最近访问时间戳序列),
这些 key 的值虽然被覆盖,但内部数据本身是历史信息的摘要。

测试方法:
- 用 amulet-leveldb 打开 snap_04 的 db/
- 枚举所有 key,按命名空间分组
- 重点检查名称包含 stat / history / log / tick / time / counter 的 key
- 读其 value,看 NBT 结构里有没有跨时间累积的字段

可能结果:
- 找到摘要 key 且能解读 → 部分历史以"摘要"形式可读(不是 bit-level 回溯,
  但能告诉你"这块 chunk 被修改了 N 次")
- 没找到或字段不含历史 → 这条边界被压实

### 假设 C:旧 MANIFEST 在磁盘上残留

来源:LevelDB 启动时新建 MANIFEST,旧 MANIFEST 在 CURRENT 切换后
理论上应被删除,但实际删除时机可能滞后,或在异常退出时未清理。
如果旧 MANIFEST 残留,且其中引用的 ldb 文件也未被删除,理论上
可以挂载到那个 MANIFEST 进入旧版本视图。

测试方法:
- 检查每个 snapshot 的 db/ 目录,看是否存在 MANIFEST 编号比 CURRENT
  指向的更小的 MANIFEST 文件
- 若有,读其内容(MANIFEST 是 LevelDB VersionEdit 的序列化,
  amulet-leveldb 或自写 parser 可读)
- 看其引用的 ldb 文件是否仍在磁盘上

可能结果:
- 残留 + 引用的 ldb 也存在 → 至少一步回溯在文件级可能
- 残留但 ldb 不在 → 知道历史 metadata,拿不到数据
- 没有残留 → 这条边界被压实

### 假设 D:iPad 系统级快照(APFS snapshot / Time Machine 链路)
包含历史存档

来源:APFS 文件系统支持 snapshot,iOS 在系统更新等场景会创建 snapshot。
如果存档数据被某个 APFS snapshot 捕获,理论上可以挂载该 snapshot
读出当时的数据。

测试方法:
- 该测试需要 jailbreak 或开发者级 iPad 访问权限,普通用户不可执行
- 暂列为"知道存在但不可测"

可能结果:不适用(测试条件不具备)

### 关于这些假设的态度

这些假设按"可执行度 × 期望回报"排序:

| 假设 | 可执行度 | 期望回报(救回溯的可能性) |
|------|---------|-------------------------|
| A    | 中(需 iPad 备份) | 低-中 |
| B    | 高(已有工具) | 低 |
| C    | 高(已有数据) | 极低(snap_00-04 看起来都只有 1 个 MANIFEST) |
| D    | 极低 | 未知 |

假设 C 可以**立即在现有 snapshot 上验证**——只需重新看一遍 inspect_db.py
的输出,确认每个 snapshot 的 db/ 里 MANIFEST 文件数 = 1。已知答案是
"= 1",所以 C 实际上已经被现有数据压实,可以在下次更新中合并进 v1 结论。

假设 A 和 B 是真正未探的边界。