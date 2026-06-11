# cutroom — 项目约束与长期记忆

## 项目定位
先"看完素材再下剪刀"的长视频剪辑师 Agent：本地优先、GPU-free、每一刀都附证据
（transcript 片段 + agent 真正看过的帧）。CLI：`log` / `highlights` / `ask` / `chapters` / `cut`。

## 硬约束（用户 2026-06-11 明确给定，不可违反）
- **GPU-free**：与模型训练完全无关，纯 Agent 工程。本地推理只允许 CPU/Metal
  （faster-whisper 等），不依赖 NVIDIA/CUDA。
- **不绑定用户的研究背景**：本项目独立站得住，不为任何论文服务。
- 评判标准：真实工程量、技术 solid、能吸引注意（GitHub stars / demo 传播力）。
- Mac（Apple Silicon）本机可开发可运行，同时保持 Linux 兼容。

## 技术基线
- Python 3.12 + uv；Claude Agent SDK 0.2.x（复用 Claude Code 认证，无需单独 API key）。
- ffmpeg、yt-dlp、faster-whisper、SQLite + FTS5。
  ⚠️ brew 的 ffmpeg 是瘦身构建（无 libass/subtitles/drawtext）——渲染层通过
  `render.ffmpeg.resolve_ffmpeg()` 解析：$CUTROOM_FFMPEG → 系统 ffmpeg（有字幕能力时）→
  static-ffmpeg 兜底（pyproject 依赖，自带 libass）。
- 内层 agent 看帧的兜底方案：`view_frames` 落盘 JPEG + 授予内置 `Read` 工具（原生读图）。

## 核心架构原则
1. **Agent 永远不读完整转写** —— 只能拿到分层视频地图（chapters ← scenes ← shots ← words）
   + 预算化的分页检索。这是本项目的立身之本，直接针对 AgenticVBench 量化的头号失败模式
   （Repurpose 失败的 83% 是长上下文信息丢失）。
2. **每一刀有证据（receipts）** —— EDL 中每个 cut 必须携带 transcript 证据段 + 已查看帧的
   时间戳，渲染时生成 receipts 报告。
3. **预算账本** —— 工具结果计费，agent 可见剩余预算；工具返回值必须紧凑、可分页。

## 证据基础（选型依据，调研于 2026-06-11，详见 DESIGN.md）
- AgenticVBench (arXiv 2605.27705)：最佳 stack 31% vs 专家 88.5%（差距 43-65pp）。
- VideoOdyssey (arXiv 2605.22907)：模型连续推理跨度 >3min 即显著退化 → 工程层（索引/记忆/预算）有明确杠杆。
- 竞品定位：HKUDS/VideoAgent（748★，agentic 但要 8GB GPU + 4 家 LLM provider）；
  SamurAIGPT/AI-Youtube-Shorts-Generator（3.8k★，固定流水线非 agentic，无视觉验证）；
  商业产品（Opus Clip 等）闭源 SaaS。"本地优先 + GPU-free + 真 agentic + 索引优先 + 证据可溯"无人占位。

## 工程纪律
- 与用户全局 CLAUDE.md 一致：验证后才说完成；最小变更；不加废话注释。
- 测试不依赖网络/API：合成视频 fixture（ffmpeg testsrc + macOS `say`）；
  需要 Claude 的 e2e 用 `requires_claude` 标记。
- 提交信息英文，代码注释英文；面向用户的 README 英文（开源传播）。
