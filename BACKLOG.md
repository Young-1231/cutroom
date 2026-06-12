# cutroom — 迭代 backlog

> 工作协议（2026-06-11 用户指示改为全自动连续模式）：按优先级连续执行，不人为间隔，
> 直到 Claude 判断项目达到"好项目"标准：全部动词真实验证 / 测试全绿 / README 带真实
> 证据 / 发布就绪 / 索引优先架构有量化消融证据。每项完成移入「已完成」附一行结果。
> 命中用量限制时安排自动唤醒续跑。不 commit、不 push（仍需用户明确授权）。

## 下一步（按优先级）

经 2026-06 完整 deep research（见 docs/agent-paradigms.md）排出的范式吸收优先级：

1. **PyPI 发包（唯一剩余，等用户侧动作）**：在 pypi.org 给 repo 配 trusted publishing
   （publisher: GitHub / Young-1231/cutroom / release.yml / environment: pypi），或把
   PYPI_API_TOKEN 加进 repo secrets；然后手动触发 `release` workflow 即发布。
   发包成功后：README Install 节加 `uv tool install cutroom` 一行。
2. 传播：拿 demo GIF 发 HN (Show HN) / r/LocalLLaMA / X（用户自行决定时机与文案）。

（2026-06 范式吸收 backlog 已全部清空；2026-06-12 已上线：仓库 public、v0.1.0 tag +
GitHub Release（带 wheel/sdist）、release.yml 一键发包 workflow、README badges。）

## 已完成

- 发布产品化（2026-06-12）：README 大改版（hero demo GIF + 8 特性段 + FAQ + 跑分表 +
  状态更新）；docs/demo.gif —— asciinema 录**真实运行**（list → cut --plan 进度行全程
  → checkpoints，agent 自己 load_recipe(teaser) 出镜）3.5× 加速 19s/438KB，
  scripts/record_demo.sh 可复录；PyPI 就绪（classifiers/keywords/urls，uv build +
  twine check 双过，wheel 含内置 recipes）；GitHub topics ×10。

- M2 Repurpose 跑分 + CI（2026-06-12）：184 离线测试（bench +8）+ 真实跑分。cutroom.bench
  只测机械可证伪项（EDL 产出 / 时长±tol / 刀数 / receipts 全覆盖 / 剪点距自然边界
  ≤0.5s），明确不做 LLM 评审（docstring 写明边界）；bench/repurpose.json 3 任务规格；
  scripts/bench_repurpose.py 出 markdown 表 + docs/bench-*.json + GITHUB_STEP_SUMMARY；
  bench.yml workflow_dispatch（下载公有领域影片 → log → 跑分 → artifact，需
  ANTHROPIC_API_KEY secret）；`cutroom list --ids` 供脚本取 id。真实跑分：demo 影片
  3/3 全过（12-21k chars / 16-22 轮），表格进 README。

- Checkpoint 三粒度 restore（2026-06-12，范式吸收 #2 收尾）：184 离线测试（+4）+ 真实
  验证。`cutroom restore --scope edl|session|both`：EDL 粒度即原行为（pre-restore 快照
  保证可撤销）；session 粒度经 checkpoint 记录的 session id（resolve 验证存在）给出
  resume/fork 句柄——SDK 会话 append-only，"恢复会话"=从会话末尾续/分叉（agent
  checkpoint 的会话末尾≈EDL 接受时刻，docstring 诚实记录该近似）；both 二者兼做。
  非 agent 来源（plan/render/pre-restore）的 checkpoint 申请 session 粒度 → 友好报错。
  真实验证：--scope both 恢复 cp_0013 并给出当时 steering 会话句柄；plan checkpoint 走
  session 粒度正确拒绝。

- Observability：`cutroom trail` CLI（2026-06-12，自研机制 #3，"无标准机制"三连收官）：
  173 离线测试（+7）+ 真实数据验证。trail.py 解析聚合（损坏行跳过不致命；fan-out 多
  会话交错按 session 分组），CLI 三视图：默认每会话汇总表（calls/spent/denied/errors/
  moments/edl）、--session 逐调用时间线（计费/余额、deny 黄色、EDL accepted → checkpoint
  id、stop 含 breakdown）、--denials 跨会话门拒绝审计。真实验证：对真实 trail 钻取出
  此前对抗测试的 sandbox deny 与逐调用账目。

- Verification/self-critique 回合（2026-06-12，自研机制 #2）：166 离线测试（+10）+ 真实
  e2e。`--verify`：EDL 落地后开**全新上下文** critic 会话（fresh eyes 而非窗口内自评，
  role="critic" 工具面没有 propose_edl/mark_moment/load_recipe，只能查证据），逐刀读
  边界转写 + 看刀内帧，经新工具 submit_review 提交结构化裁决（free、可在预算耗尽后
  finalize）；有 flag → 恰一轮修订，resume 回编辑器会话（receipts 延续），修订失败保
  原 EDL。真实验证：critic 13→4 轮查证后 "verify ✓ all 1 cuts confirmed"，理由具体到
  段边界与帧时间戳。flagged→修订→保底三路径离线覆盖。

- 中途 steering + 工具调用进度行（2026-06-12，自研机制 #1）：156 离线测试（+6）+ 真实
  e2e。自研设计（调研确认无工业标准可抄）：`--steer` 下 stdin 每行 → client.interrupt()
  → 驱动循环把该行包成 [USER STEERING] 重新 query 注入同一会话；prompt 字符串是唯一
  通道，receipts 状态原样延续；只有最后一轮决定 ok/error（被打断≠失败）。runner 重构出
  可测的 _drive_session（注入 client，FakeClient 离线全覆盖）。配套：每次工具调用打一行
  紧凑进度（→ view_frames 42s,46.5s），有得看才有得 steer。真实验证：任务要 3 刀 30s
  teaser，管道注入"只要 1 刀 Bert 歌 ≤15s"→ 最终 EDL 恰 1 刀 14.4s Bert 歌。

- 文件 allowlist sandbox（2026-06-12，范式吸收 #5b，Bundle 收官）：150 离线测试（+6）
  + 对抗性真实 e2e。结论：SDK 的 OS 级 sandbox 只罩 Bash（cutroom 已三层拒绝 Bash），
  对本工具面无增益且 Linux 依赖 bubblewrap 有破坏风险 → 正确落点是 hooks 层：
  PreToolUse 给内置 Read（编辑器唯一碰文件系统的工具）加路径白名单，只许读本视频
  media 目录；symlink resolve 后判定，相对路径直接拒（进程间 cwd 不一致）。
  真实验证：诱导 agent 读 /tmp 探针文件 → 真实链路 deny + trail 记录 + 会话优雅继续，
  秘密未泄露。这补上了间接注入经 Read 外读任意主机文件的最后一条路。
2026-06-12 范式吸收 Bundle（#5）至此全部落地：AGENTS.md / recipes→Skills / 文件沙箱。

- Recipes → Skills progressive disclosure（2026-06-12，范式吸收 #5a）：144 离线测试（+15）
  + 真实 e2e。recipe 变成 SKILL.md 式文件（frontmatter: summary/vertical/reel/budget/n，
  正文=专家指导）：内置 5 个迁到 recipes/builtin/*.md，用户把 .md 丢进
  $CUTROOM_HOME/recipes/ 即生效（按文件名覆盖内置）。双模式调用：CLI 显式 +
  模型自调用——系统提示只进 name:summary 行，新 load_recipe 工具按需取正文（计费、
  预算耗尽被 hooks 拒）；scout 不带配方层。真实验证：teaser 任务首轮即
  load_recipe(298 chars)，按配方出 3 刀 hook→build→cliffhanger 结构 plan。
  注意到内层 CLI 有 ToolSearch 轮次开销（18 次/23 轮），待查能否禁用。

- 强化 fan-out scout 隔离（2026-06-12，范式吸收 #4）：117 离线测试（+2）+ 真实 fanout
  e2e。scout 以 role="scout" 运行，make_toolkit `exclude` 把 propose_edl 从 MCP server
  整体剥离（不进上下文，从 prompt 自律升级为代码强制）；只有编排器能组 EDL。session
  索引带 role，`cutroom sessions` 标记 scout 行。真实验证：2 窗并发（26,308 chars/24 轮
  汇总），trail 显示 scout 工具集无 propose_edl，4 候选合并 top-2 落 EDL，receipts 契约保持。

- JSONL 会话持久化 + resume/fork（2026-06-12，范式吸收 #3）：115 离线测试（+7）+ 真实
  e2e 三连验证。fork 杀手锏的量化证明：父会话调查 12,489 chars/13 轮 → `--fork` 重剪
  ~10s teaser 只花 **1,500 chars/4 轮**（恰一次 view_frames），新 EDL 引用 [42.0, 46.0]
  其中 42.0 直接复用父会话凭证（fork 中未重看，证据门凭重建状态放行）。
  - **sessions.py**：sessions/index.jsonl（每 run 一条：task/spend/turns/lineage，同 id
    resume 原位更新）+ sessions/<id>.json 证据状态（viewed_frames 跨 resume/fork 重建，
    证据门继续认账，不强迫重看）。
  - **runner**：`resume`/`fork` 参数 → SDK options.resume/fork_session；cwd 钉到
    workspace home（会话 JSONL 按 cwd 派生目录存储，钉死后任意调用位置都能 resume）；
    EditorResult 带 session_id。
  - **CLI**：`cutroom sessions <video>`（lineage 列：resumed / fork of）；ask/cut 加
    `--resume/--fork`（id 前缀解析、互斥校验）；每次运行尾行打印 session 句柄。
  - 真实验证：resume 同 id 续会话且逐字记得首问（0 chars/1 轮）；fork 出新 id 带完整
    父上下文；state 文件正确累积（父 6 帧 → fork 7 帧）。

- Shadow-VCS checkpoint over EDL（2026-06-12，范式吸收 #2）：108 离线测试（+10）+ 真实
  全闭环 e2e 验证（plan → 手改 → diff → restore → undo → render）。
  - **checkpoints.py**：不依赖 git 的内容去重快照（HEAD 语义）+ cut 级语义 diff
    （`~ cut 0 [68.46-87.82] -> [68.46-81.82]`，比行 diff 更贴 EDL）；restore 先把当前态
    存为 pre-restore checkpoint（restore 本身可撤销），损坏的 edl.json 移到 .corrupt 不覆盖。
  - **四个落点**：agent propose_edl 被接受（经 hooks `on_edl_accepted` 挂载点，checkpoint id
    写回 trail）、plan 保存、render 前（人工编辑由此进历史）、restore 前。
  - **CLI**：`cutroom checkpoints <video>`（列表 + `--diff <cp>` 对比当前）、
    `cutroom restore <video> <cp>`。
  - 真实验证：demo 影片 plan 模式 12 轮落 EDL → cp_0001(agent)+cp_0002(plan，吸附差异被
    正确识别为新状态) → 手剪 3s 后 diff 输出精确两行 → restore 自动存 cp_0003(pre-restore)
    → 从恢复态真实渲出 bert_01.mp4。Cline 式 task 粒度 restore 等 resume/fork 落地后升级。

- Hooks/生命周期门（2026-06-12，范式吸收 #1）：98 离线测试（+11）+ 真实双 e2e 全绿。
  - **agent/hooks.py**：`make_lifecycle_hooks(ledger, registry, trail_path)` 挂
    PreToolUse/PostToolUse/PostToolUseFailure/Stop 四个 SDK hook 进 ClaudeAgentOptions。
  - **PreToolUse 硬门**（只 deny 或沉默，allow 仍归 can_use_tool，参数报错仍归 handler）：
    ① 副作用内置工具机械 deny（第三层）；② 预算耗尽时 deny 调查类工具、finalize 仍放行；
    ③ 证据门——propose_edl/mark_moment 引用未真正 view 过的帧直接 deny。证据门控从
    handler 自律升级为生命周期层结构性强制。
  - **trail.jsonl 审计轨迹**（renders/ 下逐 JSON 行）：每次工具调用的逐调用计费/余额、
    deny 事件、错误、Stop 会话摘要（breakdown）；`edl_accepted` 记录是 shadow-VCS
    checkpoint 的挂载点。真实 ask 验证逐调用计费与 ledger 总额完全对账（4,123 chars）。
  - **对抗性 e2e**：诱导 agent 不看帧直接 propose_edl → 真实 CLI 链路里 PreToolUse 门
    deny，拒绝理由逐字回到模型，EDL 未注册，trail 记录 deny 事件。
  - 顺手修：disallowed_tools 里的 MultiEdit 死规则（新 CLI 已并入 Edit，每次会话都警告）。

- harness 范式三连（2026-06-11，对标 Claude Code/OpenClaw 热点）：99 离线测试 + 真实
  e2e 全绿，demo 影片三链路 live 验证通过。
  - **Plan mode（人在环）**：highlights/cut/recipe 加 `--plan` → 出可读剪辑方案
    （时间码+理由+引用）+ 存 edl.json + 停，用户编辑后 `cutroom render` 落地。
  - **Recipes（可复用专家配方）**：`cutroom recipes` 列表 + `cutroom recipe <name> <video>`；
    内置 podcast-shorts/talk-highlights/teaser/quotes/tighten；对标 Claude Code skills。
  - **Subagent fan-out**：`highlights --fanout` 按场景分窗 → 每窗一个 scout agent 并发
    （CapacityLimiter 限流）→ 标记带分数的 moment → 全局去重排序 → 合并成 EDL；
    moment 已过 view-frame 校验，receipts 契约保持。real demo: 2 窗并发 → 合并 2 刀。

- 多代理 code review 修复（2026-06-11）：修掉 15 条审查发现的高优问题，85 离线测试
  （+18 回归）+ 真实 e2e 全绿，真实影片 ask/highlights 端到端复验通过。要点：
  - **安全**：内层 agent 从 bypassPermissions 改为 default + 白名单 can_use_tool 门
    + disallowed_tools，封堵恶意转写经间接注入触达 Bash/Write/WebFetch 的 RCE 链。
  - **契约**：propose_edl 现强制 frame_ts 必须真的 view 过 + 非空 evidence（_basic_validate
    同步），"每一刀有证据"不再可绕过。
  - **健壮性**：ingest 幂等（replace_shots/segments/audio_events）；ffmpeg 统一走
    cutroom.ffmpeg_util（返回码检查 + latin-1 解码 + 单一 resolve_ffmpeg）；shots 单切点
    不再丢弃；read_span 续读点修正（不跳过半展示段）+ tiny-cap 不再死循环；snap_edl 收
    真实 duration、防反转、丢退化 cut，CLI 侧重新校验；FTS 补 UPDATE 触发器；ASS 转义反斜杠/CR。
  - **CLI**：友好错误边界（@friendly），agent 输出 markup=False（保留 [seg]/[mm:ss] 引用），
    runner 暴露 ok/error（max_turns/API 错误不再伪装成正常结果），render 解析坏 json 不再 traceback。
  - **清理**：删 3 个重复 mm:ss / 5 处 ffmpeg 封装合一 / receipts 复用已抽帧。

- 发布（2026-06-11，用户授权）：private 仓库 https://github.com/Young-1231/cutroom，
  2 commits（M0 + CI 修复），CI ubuntu+macos 双绿。CI 修复：HF 对共享 runner 匿名
  下载限流 429 → whisper_tiny fixture 优雅 skip + actions/cache 缓存模型 + fail-fast 关闭。

- 预算消融实验（2026-06-11，全自动模式收官）：His Girl Friday（92min，转写 133k
  chars）3 问对照——cutroom 15.8-28k chars vs baseline 133.6k chars（4.8-8.5×），
  双方全对、cutroom 附帧验证；结果进 README + docs/ablation-*.json；
  scripts/ablation.py 可复现。另修复：长片场景碎片 bug（强边界最小间距）、
  场景数随片长缩放（92min → 41 scenes / 4.6KB 地图）、ASR 进度回调。

- `cutroom cut` 真实验证 + README 真实 demo（2026-06-11，全自动模式）：30s teaser
  指令 → 2 刀 EDL 共 30.5s（13.4k chars / 19 轮），出点吸附静音；CLI cut 现产出
  拼接 reel；README 加真实运行段落 + 字幕帧图 + receipts 摘录 + static-ffmpeg 说明；
  LICENSE(MIT) + GitHub Actions CI（ubuntu+macos，无语音环境自动降级）就绪。

- vertical 真实验证 + 场景切分改进（2026-06-11，loop 迭代 3）：vert 1080x1920 构图
  与竖版字幕样式肉眼验证通过；build_scenes 增加自适应弱停顿增补（目标 ~90s/scene，
  按停顿强度选边界），demo 影片 4 个等分 → 7 个语义场景；67 测试绿。

- `cutroom render` 动词（2026-06-11，loop 迭代 2）：从 renders/edl.json 重渲染，
  支持 --target / --captions / --basename 覆写；66 测试绿；真实验证 2 条秒级重渲染。

- 内层 agent 隔离（2026-06-11，loop 迭代 1）：runner 加 `setting_sources=[]`
  （SDK isolation mode）+ `output_language` 参数；65 测试绿；真实验证内层 agent
  不再继承宿主 CLAUDE.md（英文任务答英文，1.7k chars / 5 轮）。
- M0 MVP（2026-06-11）：log/list/map/ask/highlights/chapters/cut 全部实现；
  65 测试全绿 + ruff 干净；真实影片（Duck and Cover, 9:15）验证：
  ask 带帧证据引用回答（9.7k chars 预算）、highlights 出 2 条带词级烧录字幕成片
  + receipts.md + edl.json（14.1k chars 预算、15 轮）。
- ffmpeg 字幕能力解析（2026-06-11）：brew 瘦身构建无 libass → `resolve_ffmpeg()`
  三级解析 + static-ffmpeg 兜底；EDL 持久化到 renders/edl.json。

## 已知约束（来自 CLAUDE.md，不可违反）

GPU-free / 不绑定用户研究背景 / Mac 本机可跑 / 不 commit 不 push 除非用户明说。
