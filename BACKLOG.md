# cutroom — 迭代 backlog

> 工作协议（2026-06-11 用户指示改为全自动连续模式）：按优先级连续执行，不人为间隔，
> 直到 Claude 判断项目达到"好项目"标准：全部动词真实验证 / 测试全绿 / README 带真实
> 证据 / 发布就绪 / 索引优先架构有量化消融证据。每项完成移入「已完成」附一行结果。
> 命中用量限制时安排自动唤醒续跑。不 commit、不 push（仍需用户明确授权）。

## 下一步（按优先级）

1. **发布准备**（需用户确认后才执行 push）：LICENSE 文件、GitHub repo 名核查、CI
   （GitHub Actions：ruff + 离线测试）、首个 git commit。
2. **M2 评测故事（剩余部分）**：AgenticVBench Repurpose 子集跑分脚本接入 CI。

## 已完成

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
