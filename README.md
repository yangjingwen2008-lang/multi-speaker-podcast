# 多人访谈播客 Skill V0.1

当前发布版本为 `0.1.0`。这是可运行的本地制作 MVP，不是无人审核的自动发布系统。版本能力与限制见 [CHANGELOG.md](CHANGELOG.md)，测试范围见 [TEST_REPORT.md](TEST_REPORT.md)。仓库不包含原始访谈音频、模型、大型 AI 依赖、测试生成素材或凭证。

Skill 已放在本项目的 `.codex/skills/multi-speaker-podcast/`。在本项目的 Codex 对话中，使用 `$multi-speaker-podcast`，附上音频路径、主题与剪辑要求即可调用。

例如：

> 使用 $multi-speaker-podcast 处理 C:\录音\访谈.wav。本期主题是职业转型，输出到本项目 outputs\episode01。自动安装 AI 依赖，使用克制现代的片头片尾音乐，生成 AU 操作包。保留原意和自然互动。

## 命令行运行

需要 Python 3.10+、ffmpeg、ffprobe；自动安装 AI 环境还需要 uv。安装器创建 Python 3.11 隔离环境，并限制 PyTorch/TorchCodec/Matplotlib 的兼容版本。大型依赖安装在系统用户目录，模型在模型库缓存目录，均不放进仓库。

```powershell
python .codex/skills/multi-speaker-podcast/scripts/podcast_pipeline.py --input "C:\录音\访谈.wav" --output-dir outputs/episode01 --install-ai-deps --music-mode auto --enable-audition
```

仅验证音频处理，不下载模型：

```powershell
.\run_podcast.ps1 -Audio "C:\录音\访谈.wav" -Output outputs/audio_only -SkipAI
```

复用节目设置：编辑 `examples/podcast_config.example.json` 中的文件路径和节目资料，然后运行：

```powershell
.\run_podcast.ps1 -Config .\examples\podcast_config.example.json
```

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

## 同步多麦录音

使用 `--input-mode multitrack`，或编辑 `examples/multitrack_config.example.json` 后运行：

```powershell
.\run_podcast.ps1 -Config .\examples\multitrack_config.example.json
```

`track_offsets`、`track_gains`、`track_names` 分别设置时间偏移（秒）、独立增益（dB）和音轨名称。推荐用输入顺序的字符串索引 `"1"`、`"2"` 作为键，避免同名文件歧义。偏移为正表示该音轨延后开始；示例中的 0.25 秒仅是演示，必须按实际录音修改。未设置时全部从零开始，不会推测声学同步。

音轨先独立清理、调平，再按共同时间轴进行浮点混音，最后统一响度。转录和剪辑基于 `multitrack_mix.wav` 的共同秒数；音轨名称不等于经过识别的说话人身份。当前不自动消除串音，也不分离同时讲话的人声。`multitrack_report.json` 保存同步和增益设置，AU 源会话保留各支麦克风独立音轨。

导出的转录和 EDL 包含源音频指纹。复用时若录音内容、输入顺序或同步/增益设置改变，会拒绝执行，避免套用错误时间轴。没有指纹的外部手写 JSON 仍可使用，但需自行确认对应素材。

## 音乐与 AU

`--music-mode auto` 联网搜索带明确许可证的音乐；默认仅 CC0/Public Domain 自动加入，CC BY 候选可在 `--allow-cc-attribution-music` 下加入并生成署名。音乐未找到或网络失败时继续导出无配乐成品；素材是否真正适合节目仍需要试听。

自备音乐：

```powershell
python .codex/skills/multi-speaker-podcast/scripts/podcast_pipeline.py --input interview.wav --output-dir outputs/provided --music-mode provided --music-file music.wav --music-license "本人原创并授权本节目使用" --music-creator "作者姓名" --skip-ai
```

默认片头最多 12 秒、片尾最多 20 秒，音乐做淡入淡出并以较低响度处理，不生成持续背景音乐。

`--enable-audition` 检测 AU；`--audition-path` 可指定安装路径；`--open-audition` 尝试打开带 cue markers 的 WAV 母版；`--attempt-au-import` 尝试打开 `podcast_edit.sesx` 会话。PowerShell 启动器也可添加 `-OpenAudition` 打开会话。

AU 包包含原始副本、清理素材、片段、时间轴、marker 参考 CSV、启动脚本和可编辑工程。`podcast_edit.sesx` 已按成品顺序放置片段和音乐；多麦模式额外生成 `source_multitrack.sesx`，保留独立音轨、偏移及增益。整个 `audition_package/` 一起移动可保持相对媒体引用。工程采用 AU 23 实际会话结构，已测试 XML、素材引用和音轨重建，但尚未确认各 AU 版本的 GUI 加载结果。它是未做最终母带归一化的编排工程，AU 重新导出后必须复测响度和峰值。

## 输出与验证

主要成品：`final_podcast.mp3`（或 wav）、`master_podcast.wav`（48kHz/24-bit）、原始/清理转录稿、剪辑 JSON/Markdown、内容评分、结构稿、show notes、音乐许可记录和 `audition_package/`。`run_config.json` 保存不含凭证的本次参数，`release_manifest.json` 保存成品 SHA256。

另有 `final_subtitles.srt` / `.vtt`（成品时间轴字幕）与 `subtitle_report.json`。跨越剪点的转录整句不会自动写入字幕，需根据报告复听补齐。打开 `review_package/index.html`，可比较语音剪点两侧参考音频和成品衔接；默认最多 20 个剪点，可用 `--review-limit 0..100` 调整。多麦参考音频来自共同混音，不是未经处理的单麦原音。复听包不覆盖整集，也不自动确认听感。

最终导出使用两遍响度归一化，并对编码成品检查完整解码、LUFS 和真峰值。`audio_qa_report.md` 中列出实测值；静音或极短素材不一定能达到目标。技术检查不能替代整集人工听审，也不执行平台发布。

`production_status.json` 区分技术检查与人工审核状态。添加 `--require-technical-qa`（或启动器 `-RequireTechnicalQA`），技术 QA 未通过时返回退出码 2，仍保留成品及诊断报告。音乐筛选依据许可证和风格元数据，不能保证曲目适配或权利声明真实，发布前仍需试听和核对。

`source_audio_info.json` 保存所有原始输入的格式与时长信息，多麦模式不会只留下混音信息。原始副本保存在 AU 包中；麦克风录音不会上传到转录服务。`--overwrite` 不清理上次运行的多余素材，避免删除手动编辑文件；应以本次 manifest/session_report 为准，正式制作优先使用新输出目录。

```powershell
python -m unittest discover -s tests -v
python .codex/skills/multi-speaker-podcast/scripts/setup_ai_env.py --compute auto --dry-run
```

### 本次验收

- 38 项单元/集成测试、12 个稳定性与完整度验收场景通过，Ruff 检查与 Skill 元数据验证通过。详见 `TEST_REPORT.md`，其中区分已验收能力与未完成验证的功能。
- 使用本机 CUDA 跑通 17.66 秒合成语音的转录、剪辑、导出、字幕和复听包，测试 MP3 实测 -16.60 LUFS / -2.26 dBTP。样例在 `.test-artifacts/deep_smoke_output/`，不是正式节目素材。
- 多麦会话按 XML 导出的偏移/增益经 FFmpeg 重建后，与浮点混音文件一致；不代表 AU GUI 已加载确认。
- 联网候选筛选已执行，本次测试未找到符合条件的音乐；没有为凑配乐放宽许可条件。
- AU GUI 加载、完整人工听审和带模型授权的真实多人 diarization 尚未完成验收。本地 HTML 在当前浏览器工具中被协议策略阻止预览，未作浏览器视觉验收，也未绕过限制。

独立检查某次输出：

```powershell
python .codex/skills/multi-speaker-podcast/scripts/validate_output.py outputs/episode01 --report episode01_audit.json
```

完整度校验通过不代表转录准确或听感成熟。不要使用 Python `-O` 优化模式执行验收器。`run_state.json` 记录本次运行状态及编号；输出目录使用内核锁，失败重跑会将成功清单失效，历史状态清单在 `run_history/`，不会备份所有旧音频文件。
