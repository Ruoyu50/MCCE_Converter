# 实验 02:NetEase iPad 游戏模式字段的读写机制

## 1. 背景

`java-to-netease` 的玩家状态迁移(player_translate.py)在 Phase 1b–3c 已经把
位置 / 血量 / 经验 / 饥饿 / 维度 / 选中格 / 背包 / 装备 / 附魔 / 自定义名 /
ID 差异 / 末影箱全部恢复并经 iPad 实测。剩下一个字段:**玩家的当前游戏模式**。

需求很直接:Java 端是创造模式的存档,导入 iPad 后希望加载时直接显示创造,
而不是让用户在 iPad 上手动切。本实验研究"工具能否从导出侧(存档数据)控制
iPad 加载时显示的游戏模式"。

结论先行:**不能**。iPad 加载时的模式由"在 iPad 上新建占位世界时 UI 选的
模式"决定,存在存档数据之外、工具写不到的地方。Phase 3d / 3e 的代码已回滚
(commit `75f7465`)。

## 2. 要回答的具体问题

1. Bedrock 的游戏模式存在哪个字段?Java 的存在哪个字段?Chunker 翻译什么?
2. iPad 加载世界时,玩家的当前模式是从哪个字段读的?
3. 工具能不能通过覆盖这些字段,控制 iPad 加载时的模式?

## 3. 字段层调研

### 3.1 字段定义

| 边 | 字段 | 类型 | 语义 |
|----|------|------|------|
| Java | `level.dat` → `Data.GameType` | int | **世界默认**游戏模式(新玩家加入时的模式) |
| Java | player NBT → `playerGameType` | int | **该玩家当前**游戏模式 |
| Bedrock | `level.dat` → `GameType` | int | 世界默认游戏模式 |
| Bedrock | `~local_player` → `PlayerGameMode` | int | 本地玩家当前游戏模式 |

模式编码两版一致:0=生存 / 1=创造 / 2=冒险 / 3=旁观。

### 3.2 Chunker 翻译什么

Chunker 把 Java `level.dat.Data.GameType`(世界默认)→ Bedrock
`level.dat.GameType`。它**不**把 Java player 的 `playerGameType`(玩家当前)
翻译到 Bedrock `~local_player.PlayerGameMode`(Chunker 写的 LocalPlayer 是个
9 字段 stub,见实验外的 player_translate 调研)。

所以默认产物里:`level.dat.GameType` = Java 世界默认值,`PlayerGameMode` =
模板存档的值。两者都不等于 Java 玩家的当前模式。

### 3.3 各模板 / 存档的实际值

- 测试用 Java 存档 `JNPhase3b`:`playerGameType = 1`(创造),
  `Data.GameType = 0`(世界默认生存)。注意这俩本来就不一样 —— 玩家切到了
  创造,但世界默认还是生存。
- 模板 `B`(`bnqoY7hdBAA=`):`~local_player.PlayerGameMode = 0`(生存)。
- iPad 新建、从未进入的空白世界:`~local_player.PlayerGameMode = 5`
  —— **无效哨兵值**,超出 0..3 范围。带这个值的 player 无法实例化,玩家掉到
  (0, -2, 0)(这正是早期用空白世界做模板时踩的坑,后来改用有游玩历史的 B)。

## 4. 三次受控 iPad 实验

变量:`level.dat.GameType`、`~local_player.PlayerGameMode`、iPad 新建占位
世界时 UI 选的模式。观测:iPad 加载后玩家实际显示的模式。

| 实验 | level.dat.GameType | PlayerGameMode | iPad UI 选择 | iPad 加载结果 |
|------|--------------------|----------------|--------------|---------------|
| Phase 3d test 1 | 0(Chunker) | 1(Phase 3d 写) | 生存 | **生存** |
| Phase 3d test 2 | 0(Chunker) | 1(Phase 3d 写) | 创造 | **创造** |
| Phase 3e        | 1(Phase 3e 写) | 1(Phase 3d 写) | 生存 | **生存** |

读法:

- test 1 vs test 2:存档数据完全相同(GameType=0, PlayerGameMode=1),只改
  iPad UI 选择,加载结果跟着 UI 走 → **UI 选择是 source of truth**。
- Phase 3e:把 `level.dat.GameType` 也改成 1(创造),UI 仍选生存,结果仍是
  生存 → `level.dat.GameType` 不被加载逻辑采用。

## 5. 已证伪假设

- **假设 A:`~local_player.PlayerGameMode` 是加载时的 source of truth。**
  证伪 —— test 1 里 PlayerGameMode=1(创造)但 UI 选生存,结果生存。
- **假设 B:`level.dat.GameType` 是加载时的 source of truth。**
  证伪 —— Phase 3e 把它设成 1(创造),UI 选生存,结果仍生存。
- **假设 C:模板 `~local_player` 末尾的 trailer(`00 00 00` vs NetEase 的
  `67 c0 00`)是 iPad 是否接受 player 的关键。**
  证伪 —— `Netease_Template`(一个字段齐全但 trailer 为 `00 00 00` 的模板)
  仍然失败,根因是它的 `PlayerGameMode=5`(无效哨兵),不是 trailer。

## 6. 当前最佳理解(v1, 2026-05-20)

iPad 加载世界时的玩家游戏模式,**由"在 iPad 上新建占位世界时 UI 选的模式"
决定**,该选择存在存档数据(`db/` + `level.dat`)之外 —— 大概率在应用沙箱内
其它文件或设备级状态,工具从导出侧写不到。

存档里的两个模式字段:

- `level.dat.GameType`:加载时不被玩家模式逻辑采用(至少在我们测的路径里)。
- `~local_player.PlayerGameMode`:加载时不被读取来决定模式;但 iPad 在玩家
  **游戏内切换模式**时会主动写它。还额外承担"实例化校验"职责 —— 值为 5
  (无效)会导致 player 整个加载失败。

实际含义:`java-to-netease` 无法控制 iPad 加载时的模式。文档化为
out-of-scope,workaround 是"在 iPad 新建占位世界时手动选对应模式"。

## 7. 未尝试的可能路径

留给后续研究(都超出当前工具的导出侧范围):

- 通过 iPad 的局域网共享 / 云存档链路,观察 NetEase 在哪同步游戏模式状态。
- 反编译 NetEase iPad 客户端,定位读取游戏模式的代码路径。
- 监控 iPad 文件系统访问(需越狱或开发者级权限),看加载世界时除了 `db/`
  还读了哪些文件 —— 那里可能就是模式状态的真正落点。
- 找到一个可靠的 iPad 内操作流程,把空白世界的 `PlayerGameMode=5` 转成有效
  值,从而让"全新空白世界"也能当干净模板用(目前必须用有游玩历史的存档)。

## 8. 相关 commit

- `8b40e87` — Phase 3d:写 `~local_player.PlayerGameMode`(后证伪、已回滚)
- (Phase 3e:写 `level.dat.GameType`,未单独成 commit,改动在工作区中证伪)
- `75f7465` — revert:移除 Phase 3d + 3e 的游戏模式覆盖代码,改为文档化
