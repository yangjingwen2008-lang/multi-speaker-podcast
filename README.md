# 多人访谈播客制作 Skill

多人访谈播客制作 Skill：从原始录音到音频成品、转录稿、剪辑决策和 Adobe Audition 工程包。

访谈录完以后，制作工作往往才刚开始：不同麦克风的音量要调平，长停顿需要整理，转录稿要对照录音，剪辑后的衔接还得逐段复听。本项目把这些重复工作集中到一条本地流水线中，并保留可检查、可修改的中间结果。

输入一段混合访谈录音、几段连续录音，或主持人和嘉宾各自的麦克风音轨，可以获得 MP3/WAV、带时间戳的转录与字幕、剪辑清单、发布文案和 AU 操作包。内容取舍仍由编辑确认，自动处理默认保留谈话，只压缩测得的长停顿。

**当前版本：V0.1 / `0.1.0`，可运行的 MVP。** 以 Windows 本机测试为依据；其他系统尚未完成同等验收。它不自动发布节目，也不将“文件导出成功”视为“内容已适合发布”。

[版本发布](https://github.com/yangjingwen2008-lang/multi-speaker-podcast/releases/tag/v0.1.0) · [版本记录](CHANGELOG.md) · [测试报告](TEST_REPORT.md) · [配置说明](.codex/skills/multi-speaker-podcast/references/configuration.md)

## 核心能力

| 环节 | V0.1 实现 |
|---|---|
| 音频整理 | 格式与时长检查、48kHz 双声道工作文件、保守降噪、基础 EQ 与动态处理 |
| 多麦制作 | 手动设置时间偏移、独立增益与音轨名称，按共同时间轴混音 |
| 本地 AI | 隔离安装 faster-whisper、pyannote.audio、torch、torchaudio；转录与说话人标签支持失败降级 |
| 内容与剪辑 | 启发式评分、源时间轴 EDL、片段裁剪与重排、短淡入淡出；复杂内容编排需审核 |
| 音乐 | 检索带许可信息的片头片尾素材，或处理自备授权音乐；记录来源与署名 |
| 音频交付 | MP3/WAV 成品、48kHz/24-bit WAV 母版、两遍响度归一化与编码后检查 |
| AU 协作 | 原始副本、清理素材、片段、时间轴、marker 参考与可编辑 SESX 工程 |
| 验收与复听 | 成品字幕、剪点试听页、SHA256 清单、运行状态和独立完整度检查 |

适合主持人、播客编辑和录音后期整理多人访谈、人物对谈、圆桌及远程多麦录音。严重串音、多人重叠讲话和复杂噪声仍需要针对素材人工处理。

## 目录

- [制作流程](#制作流程)
- [快速开始](#快速开始)
- [在 Codex 中调用](#在-codex-中调用)
- [AI 环境与配置规则](#ai-环境与配置规则)
- [实际剪辑](#实际剪辑)
- [同步多麦录音](#同步多麦录音)
- [片头片尾音乐](#片头片尾音乐)
- [Adobe Audition 协作](#adobe-audition-协作)
- [输出文件](#输出文件)
- [验证与已知限制](#验证与已知限制)
- [项目结构](#项目结构)

## 制作流程

```text
原始录音 -> 基础检查与清理 -> 转录 / 可选说话人识别
         -> 内容评分与剪辑方案 -> 片段裁剪、编排与音乐
         -> 母版与成品导出 -> 技术 QA / 完整度检查
         -> 人工复听与内容审核 -> 可选 AU 精修 -> 手动发布
```

| 输入场景 | 使用方式 |
|---|---|
| 单个混合访谈文件 | 提供一个 `--input` |
| 同一期的连续录音片段 | 提供多个 `--input`，默认按输入顺序串接 |
| 同时录制的独立麦克风 | 使用 `--input-mode multitrack`，显式提供偏移与增益 |

支持 MP3、WAV、M4A、FLAC、OGG、AAC 等 FFmpeg 可读取的音频。格式支持受本机 FFmpeg 构建影响；本次验收覆盖范围见测试报告。

## 快速开始

### 1. 获取项目并检查环境

```powershell
git clone https://github.com/yangjingwen2008-lang/multi-speaker-podcast.git
cd multi-speaker-podcast
python --version
ffmpeg -version
ffprobe -version
uv --version
```

需要 Python 3.10+、`ffmpeg` 和 `ffprobe`；自动安装 AI 依赖还需要 `uv`，以上工具应在 `PATH` 中。Git 仅用于克隆，也可从发布页下载源码。首次安装大型依赖、下载模型需要网络、足够磁盘空间和等待时间。

### 2. 先跑通基础音频处理

替换为实际录音路径，在项目根目录运行：

```powershell
.\run_podcast.ps1 -Audio "C:\录音\访谈.wav" -Output outputs/audio_only -SkipAI
```

此模式不下载模型，不自动转录或识别说话人，仍会生成音频、报告和 AU 包；缺失文字不会被编造。它适合先检查 FFmpeg 和素材是否正常。

### 3. 启用 AI 与可选配乐

```powershell
python .codex/skills/multi-speaker-podcast/scripts/podcast_pipeline.py `
  --input "C:\录音\访谈.wav" `
  --output-dir outputs/episode01 `
  --podcast-name "我的播客" `
  --episode-topic "职业转型" `
  --host "主持人" `
  --guest "嘉宾" `
  --install-ai-deps `
  --compute auto `
  --whisper-model small `
  --target-lufs -16 `
  --music-mode auto `
  --music-style "calm modern instrumental interview" `
  --enable-audition `
  --require-technical-qa
```

默认输出 MP3；需要 WAV 时加 `--target-format wav`。配乐是可选项，不需要联网检索时使用 `--music-mode off`。模型、音乐或 AU 不可用时会记录降级原因；最终应检查报告，不能仅凭退出成功判断所有能力均已执行。

### 4. 复用节目设置

复制示例到项目根目录，再修改录音路径和节目资料：

```powershell
Copy-Item examples/podcast_config.example.json podcast_config.json
.\run_podcast.ps1 -Config .\podcast_config.json
```

复制后配置的相对路径基准会改变，应将示例中的 `../raw_audio/`、`../outputs/` 改为 `raw_audio/`、`outputs/`，或填写绝对路径。`podcast_config.json` 已被 Git 忽略，凭证仍应放在环境变量中。

## 在 Codex 中调用

Skill 位于本项目的 `.codex/skills/multi-speaker-podcast/`。在项目目录中开启 Codex 对话，使用 `$multi-speaker-podcast` 并附上音频路径、主题与剪辑要求。

例如：

> 使用 $multi-speaker-podcast 处理 C:\录音\访谈.wav。本期主题是职业转型，输出到本项目 outputs\episode01。自动安装 AI 依赖，使用克制现代的片头片尾音乐，生成 AU 操作包。保留原意和自然互动。

## AI 环境与配置规则

安装器创建 Python 3.11 隔离环境，并限制 PyTorch/TorchCodec/Matplotlib 的兼容版本。Windows 默认位置是 `%LOCALAPPDATA%\CodexPodcastSkill\venvs\podcast-ai`；模型使用模型库缓存目录，均不放进仓库。

配置文件内的相对路径以配置文件所在目录为基准；命令行参数覆盖配置。输出目录必须为空，重跑时可指定新目录，或明确添加 `--overwrite`。该参数覆盖本次使用的文件，不删除目录中的其他文件。

多人说话人识别需要在 Hugging Face 接受 community-1 模型条款，再通过当前进程的 `HUGGINGFACE_TOKEN` 环境变量提供 token。不要把 token 写进配置文件。没有 token 时继续转录，说话人标为 `Unknown`。

## 实际剪辑

默认保留全部谈话，仅压缩超过 2.5 秒且低于 -50 dB 的停顿，剪点两侧各保留 0.5 秒。预处理不改变时间坐标。短句、笑声、附和和口癖不会仅因内容评分而被自动删掉。

生成的 `edit_decision_list.json` 可作为下一次运行的 `--edit-plan`：删除 clips 中不需要的条目，调整 start/end，重排 clips 即可控制成品。时间单位为原始文件秒数。也可导入自备转录稿 `--transcript-json`，格式为：

```json
{"segments": [{"source_file": "interview.wav", "start": 0, "end": 12.5, "speaker": "Speaker 1", "text": "实际录音转录内容"}]}
```

姓名映射用配置中的 `speaker_map`，例如 `{"interview.wav:Speaker 1": "主持人"}`。不同文件的 Speaker 1 不自动视为同一个人。

多个输入默认按提供顺序串接，适用于分段录音。内容评分是启发式复听提示，复杂主题重排由 Codex 根据真实转录稿生成剪辑方案，再用 `--edit-plan` 渲染。`--generate-intro-outro` 生成待录制文案，不合成人声。

第一轮处理后，复制 `edit_decision_list.json` 为自己的剪辑方案，审核保留范围和顺序，再输出到新目录：

```powershell
python .codex/skills/multi-speaker-podcast/scripts/podcast_pipeline.py `
  --input "C:\录音\访谈.wav" `
  --transcript-json outputs/episode01/transcript.json `
  --edit-plan reviewed_edit_plan.json `
  --output-dir outputs/episode01_reviewed `
  --skip-ai `
  --require-technical-qa
```

这一步复用转录，不重复运行 AI。手动 EDL 优先于默认静音压缩；没有保留片段或超出素材范围会拒绝执行。首次使用的多麦设置也必须在重渲染时保持一致。

## 同步多麦录音

使用 `--input-mode multitrack`，或编辑 `examples/multitrack_config.example.json` 后运行：

```powershell
.\run_podcast.ps1 -Config .\examples\multitrack_config.example.json
```

`track_offsets`、`track_gains`、`track_names` 分别设置时间偏移（秒）、独立增益（dB）和音轨名称。推荐用输入顺序的字符串索引 `"1"`、`"2"` 作为键，避免同名文件歧义。偏移为正表示该音轨延后开始；示例中的 0.25 秒仅是演示，必须按实际录音修改。未设置时全部从零开始，不会推测声学同步。

音轨先独立清理、调平，再按共同时间轴进行浮点混音，最后统一响度。转录和剪辑基于 `multitrack_mix.wav` 的共同秒数；音轨名称不等于经过识别的说话人身份。当前不自动消除串音，也不分离同时讲话的人声。`multitrack_report.json` 保存同步和增益设置，AU 源会话保留各支麦克风独立音轨。

导出的转录和 EDL 包含源音频指纹。复用时若录音内容、输入顺序或同步/增益设置改变，会拒绝执行，避免套用错误时间轴。没有指纹的外部手写 JSON 仍可使用，但需自行确认对应素材。

## 片头片尾音乐

`--music-mode auto` 联网搜索带明确许可证的音乐；默认仅 CC0/Public Domain 自动加入，CC BY 候选可在 `--allow-cc-attribution-music` 下加入并生成署名。音乐未找到或网络失败时继续导出无配乐成品；素材是否真正适合节目仍需要试听。

自备音乐：

```powershell
python .codex/skills/multi-speaker-podcast/scripts/podcast_pipeline.py --input interview.wav --output-dir outputs/provided --music-mode provided --music-file music.wav --music-license "本人原创并授权本节目使用" --music-creator "作者姓名" --skip-ai
```

默认片头最多 12 秒、片尾最多 20 秒，音乐做淡入淡出并以较低响度处理，不生成持续背景音乐。

默认提供商是 Internet Archive；选择 Jamendo 需要设置 `JAMENDO_CLIENT_ID`。不抓取商业音乐平台音频，未知许可及带 NC/ND 限制的候选不会自动混入。检索会参考节目名称、主题和 `music_style`，但风格匹配仍需要试听。

许可报告保存来源、作者、下载链接与许可信息；来源元数据并不证明版权归属真实。发布前应核对授权与所需署名。规则见 [音乐许可说明](.codex/skills/multi-speaker-podcast/references/music_licensing.md)。

## Adobe Audition 协作

`--enable-audition` 检测 AU；`--audition-path` 可指定安装路径；`--open-audition` 尝试打开带 cue markers 的 WAV 母版；`--attempt-au-import` 尝试打开 `podcast_edit.sesx` 会话。PowerShell 启动器也可添加 `-OpenAudition` 打开会话。

AU 包包含原始副本、清理素材、片段、时间轴、marker 参考 CSV、启动脚本和可编辑工程。`podcast_edit.sesx` 已按成品顺序放置片段和音乐；多麦模式额外生成 `source_multitrack.sesx`，保留独立音轨、偏移及增益。整个 `audition_package/` 一起移动可保持相对媒体引用。工程采用 AU 23 实际会话结构，已测试 XML、素材引用和音轨重建，但尚未确认各 AU 版本的 GUI 加载结果。它是未做最终母带归一化的编排工程，AU 重新导出后必须复测响度和峰值。

时间轴及 marker CSV 是剪辑参考，不保证可由 AU 任意版本直接导入；WAV 母版另有内嵌 cue markers。启动命令只表示已发出打开请求，不证明 AU 已完成导入。更多细节见 [AU 工作流](.codex/skills/multi-speaker-podcast/references/audition_workflow.md)。

## 输出文件

```text
outputs/episode01/
  final_podcast.mp3          MP3 成品；选择 WAV 时为 final_podcast.wav
  master_podcast.wav         48kHz / 24-bit / 双声道母版
  transcript_raw.md          原始转录、时间戳与可用说话人标签
  transcript_cleaned.md      清理后的文字稿
  transcript.json           可复用转录
  content_scores.json       启发式内容评分
  edit_decision_list.md      剪辑理由
  edit_decision_list.json    可复用 EDL 与成品时间位置
  podcast_structure.md      结构草稿
  show_notes.md             发布文案与音乐署名
  edit_report.md            处理与降级记录
  audio_info_report.md      音频基础信息
  audio_qa_report.md        实测响度、真峰值与解码结果
  music_manifest.json       音乐选择与来源
  music_license_report.md   音乐许可记录
  final_subtitles.srt       成品时间轴字幕；另有 VTT
  production_status.json    技术检查与人工审核状态
  release_manifest.json     成品文件哈希与大小
  run_state.json            本次运行编号及状态
  review_package/           剪点试听页和参考音频
  audition_package/         原始副本、清理音频、片段、音乐与 SESX
```

清单为主要输出，不是全部文件。音乐素材、额外多麦会话及开场结尾文案按运行配置和实际成功状态生成；没有转录能力时相关报告会明确说明缺失。

此外，`run_config.json` 保存不含凭证的本次参数，`release_manifest.json` 保存成品 SHA256。

另有 `final_subtitles.srt` / `.vtt`（成品时间轴字幕）与 `subtitle_report.json`。跨越剪点的转录整句不会自动写入字幕，需根据报告复听补齐。打开 `review_package/index.html`，可比较语音剪点两侧参考音频和成品衔接；默认最多 20 个剪点，可用 `--review-limit 0..100` 调整。多麦参考音频来自共同混音，不是未经处理的单麦原音。复听包不覆盖整集，也不自动确认听感。

最终导出使用两遍响度归一化，并对编码成品检查完整解码、LUFS 和真峰值。`audio_qa_report.md` 中列出实测值；静音或极短素材不一定能达到目标。技术检查不能替代整集人工听审，也不执行平台发布。

## 验证与已知限制

`production_status.json` 区分技术检查与人工审核状态。添加 `--require-technical-qa`（或启动器 `-RequireTechnicalQA`），技术 QA 未通过时返回退出码 2，仍保留成品及诊断报告。音乐筛选依据许可证和风格元数据，不能保证曲目适配或权利声明真实，发布前仍需试听和核对。

`source_audio_info.json` 保存所有原始输入的格式与时长信息，多麦模式不会只留下混音信息。原始副本保存在 AU 包中；麦克风录音不会上传到转录服务。`--overwrite` 不清理上次运行的多余素材，避免删除手动编辑文件；应以本次 manifest/session_report 为准，正式制作优先使用新输出目录。

```powershell
python -m unittest discover -s tests -v
python .codex/skills/multi-speaker-podcast/scripts/setup_ai_env.py --compute auto --dry-run
```

### V0.1 验收（2026-10-12）

- 38 项单元/集成测试、12 个稳定性与完整度验收场景通过，Ruff 检查与 Skill 元数据验证通过。详见 `TEST_REPORT.md`，其中区分已验收能力与未完成验证的功能。
- 使用本机 CUDA 跑通 17.66 秒英文合成语音的转录、剪辑、导出、字幕和复听包；最终验收矩阵中的测试 MP3 实测 -16.53 LUFS / -2.25 dBTP，不代表中文长访谈准确率。
- 多麦会话按 XML 导出的偏移/增益经 FFmpeg 重建后，与浮点混音文件一致；不代表 AU GUI 已加载确认。
- 联网候选筛选已执行，本次测试未找到符合条件的音乐；没有为凑配乐放宽许可条件。
- AU GUI 加载、完整人工听审和带模型授权的真实多人 diarization 尚未完成验收。本地 HTML 在当前浏览器工具中被协议策略阻止预览，未作浏览器视觉验收，也未绕过限制。

独立检查某次输出：

```powershell
python .codex/skills/multi-speaker-podcast/scripts/validate_output.py outputs/episode01 --report episode01_audit.json
```

完整度校验通过不代表转录准确或听感成熟。不要使用 Python `-O` 优化模式执行验收器。`run_state.json` 记录本次运行状态及编号；输出目录使用内核锁，失败重跑会将成功清单失效，历史状态清单在 `run_history/`，不会备份所有旧音频文件。

### 当前边界

| 能力 | 需要注意 |
|---|---|
| 自动内容分析 | 启发式评分用于提示复听，不能证明理解观点或保证保留原意 |
| 多人识别 | 需要模型授权；不做重叠人声分离，不自动确认真实姓名 |
| 多麦同步 | 需要显式偏移；不做声学自动对齐或串音消除 |
| 片头片尾 | 文案供后续录制，没有 TTS；配乐仅为短片头片尾 |
| AU 自动化 | 生成工程并尝试打开；GUI 导入和自动降噪、混音、导出未验收 |
| 发布准备 | 需人工听审、内容审核和授权核对；不自动上传发布平台 |

### 遇到问题

- 找不到 `ffmpeg` / `ffprobe`：确认工具已安装并进入当前终端的 `PATH`。
- 没有 GPU 或 GPU 推理失败：可指定 `--compute cpu`；CPU 运行时间取决于模型和音频长度。
- 没有 Hugging Face token：转录仍可继续，说话人标为 `Unknown`；不要把凭证写入配置或提交仓库。
- 没有匹配音乐或音乐下载失败：阅读许可报告，改用 `off` 或自备音乐，不要放宽许可条件凑配乐。
- 输出目录非空：推荐新建输出目录；只有确认要覆盖时才使用 `--overwrite`。
- 退出码为 `2`：检查参数错误信息或 `audio_qa_report.md`；严格 QA 失败也会返回 2 并保留诊断。

## 项目结构

```text
.codex/skills/multi-speaker-podcast/
  SKILL.md                       Skill 入口与制作约束
  agents/openai.yaml             Codex 展示元数据
  scripts/
    podcast_pipeline.py          制作流水线 CLI
    setup_ai_env.py              隔离 AI 环境安装与验证
    music_fetcher.py             配乐检索、处理与许可记录
    audio_editing.py             剪辑、静音区间与音频 QA
    multitrack.py                多麦同步混音
    audition_bridge.py           AU 探测、打开请求与操作包
    audition_session.py          SESX 工程生成
    review_package.py            字幕与剪点复听
    run_state.py                 内核锁与运行状态
    validate_output.py           独立完整度验收
  references/                    配置、AU 与音乐许可说明
examples/                        单文件与多麦配置示例
tests/                           单元测试与验收矩阵
run_podcast.ps1                  Windows 启动入口
TEST_REPORT.md                   测试范围、结果与复现条件
CHANGELOG.md                     版本记录
```

仓库不包含原始访谈、成品音频、模型、大型依赖、凭证或测试生成素材。本地转录不向在线转录服务上传录音；依赖/模型下载和 `music-mode auto` 会联网，音乐检索会向提供商发送节目名称、主题或风格关键词。含敏感信息的节目可关闭自动音乐并提前准备本地依赖与模型。

反馈问题请使用 [GitHub Issues](https://github.com/yangjingwen2008-lang/multi-speaker-podcast/issues)，附上系统、命令、依赖版本和脱敏后的报错。涉及私人访谈时，请使用合成音频或获授权的最小样例，不要公开上传录音与凭证。
