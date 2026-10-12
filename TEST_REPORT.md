# 稳定性与完整度测试报告

测试日期：2026-10-12。环境：本机 Windows、FFmpeg、Python、NVIDIA CUDA 隔离环境。

## 结论

当前 MVP 的音频处理、文件交付和故障保护通过本次验收：38/38 项单元与集成测试、12/12 个验收场景通过，Ruff 和 Skill 元数据检查通过。此结论不是“所有播客制作能力 100% 完成”，也不等于真实节目已经达到可发布听感。

## 稳定性

已测试：并发输出保护、进程崩溃后锁释放、依赖环境并发安装保护、失败覆盖重跑、损坏文件、已有用户文件保护、Windows 临时文件占用重试、中文及特殊字符路径、凭证脱敏、连续三次重复渲染、GPU 失败转 CPU（故障注入）、音乐服务未配置降级、极短/静音音频 QA 拒绝、错误时间范围及配置。

本次发现并修复：

- 覆盖重跑失败后残留旧成功清单：现在开始运行即更新状态和运行编号，失败清单不再列出旧成品；旧状态清单保存到 `run_history/`。这不是对所有旧音频的备份。
- 同一输出目录并发写入：新增内核文件锁；另一个运行会明确拒绝，不改写持有者的状态。共享 AI 安装环境也使用独立锁。
- Windows 一次原子替换“拒绝访问”：首次矩阵有 1 项失败，已保留日志。状态写入增加最多 7 次尝试、总退避约 1.575 秒，永久权限错误仍失败，不修改权限。
- 诊断信息可能包含 Jamendo ID：错误报告增加脱敏；异常语音文本的字段类型也更严格。
- 验收器在 Python 优化模式下可能跳过断言：现在拒绝 `-O/PYTHONOPTIMIZE` 模式，不能以关闭检查获得“通过”。

## 完整度

独立验收器检查 33 个必需文件及 11 类一致性条件，包括 JSON 可解析且无 NaN/Infinity、各 manifest 的运行编号、成品 SHA256 与大小、母版 48kHz/24-bit/双声道、CSV/JSON 时间轴、实际片段及母版时长、AU 相对媒体引用、复听页音频链接、字幕范围、原始副本和完整解码。

| 能力 | 本次结论 |
|---|---|
| 读取 WAV/MP3/FLAC/M4A/OGG | 通过；覆盖 8kHz 至 96kHz、单声道/双声道/6 声道输入 |
| 基础清理、格式统一、时间坐标保留 | 技术链路通过；未做真实复杂噪声的主观音质评估 |
| 多麦同步混音 | 三条各 300 秒合成音轨通过，偏移及独立增益有效 |
| faster-whisper 转录 | 实际 CUDA 推理通过；测试为 17.66 秒英文合成语音，不代表中文长访谈准确率 |
| pyannote 多人分离 | 依赖导入通过，无 token 时正确降级；真实模型推理未验收 |
| 内容价值评分、主题分析 | 只有启发式实现，不能证明自动编辑理解或原意保留 |
| 剪辑决策、裁剪、重排、合成 | 通过；覆盖删除区间、手动重排、30 段编排及复听数量上限 |
| 结构稿、show notes、开场结尾 | 文件完整；开场结尾只是待录制文案，主题结构需人工确认 |
| 后期与 MP3/WAV 导出 | 技术 QA 通过；CUDA 流程测试成品 -16.53 LUFS、-2.25 dBTP，完整解码通过 |
| 音乐 | 自备测试素材合成、许可规则和故障降级通过；联网下载成功率、授权真实性及风格适配不在本次通过范围 |
| AU 操作包与 SESX | XML、时间轴、源会话重建和媒体引用通过；AU GUI 导入、自动降噪/混音/导出未验收 |
| 字幕、复听包、发布状态 | 结构与时间范围通过；没有完成浏览器视觉验收或整期人工听审 |

## 本机原始证据

- 最终矩阵：`.test-artifacts/acceptance/20261012T130751Z/acceptance_report.md`
- 机器结果：`.test-artifacts/acceptance/20261012T130751Z/acceptance_report.json`
- 最新独立完整度复核：`.test-artifacts/acceptance/20261012T130751Z/final_completeness_audit.json`
- 首轮失败记录：`.test-artifacts/acceptance/20261012T125948Z/very_short_audio.log`

上述路径仅在测试机器上存在，不是 GitHub 仓库中的下载链接。证据目录保留了生成素材、各次输出、逐场景日志和依赖验证报告；`.test-artifacts/` 不纳入 Git。所有压力测试音频是测试素材，不应作为正式节目发布。

## 复现

在项目根目录运行：

```powershell
python -m unittest discover -s tests -v
python tests/acceptance_runner.py --with-ai --stress-seconds 300
python .codex/skills/multi-speaker-podcast/scripts/validate_output.py outputs/episode01 --report episode01_audit.json
```

验收矩阵默认生成新的证据目录，不覆盖历史测试结果。`--with-ai` 需要已存在的 `.test-artifacts/ai_smoke.wav` 英文合成语音样例；没有时会明确报告未通过，不伪造转录。状态文件保留 `running/failed/completed/technical_qa_failed` 区别；硬终止后可在确认无其他运行时用 `--overwrite` 重跑，但没有断点续跑。
