# cutroom — 迭代 backlog

> 工作协议（2026-06-11 用户指示改为全自动连续模式）：按优先级连续执行，不人为间隔，
> 直到 Claude 判断项目达到"好项目"标准：全部动词真实验证 / 测试全绿 / README 带真实
> 证据 / 发布就绪 / 索引优先架构有量化消融证据。每项完成移入「已完成」附一行结果。
> 命中用量限制时安排自动唤醒续跑。不 commit、不 push（仍需用户明确授权）。

## 下一步（按优先级）

1. **M2 评测故事（剩余部分）**：AgenticVBench Repurpose 子集跑分脚本接入 CI。

## 已完成

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
