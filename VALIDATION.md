# 验证记录

## 2026-10-12 离线翻译浮窗与稿件选项

- 完整回归：`python -m unittest discover -s tests -q`，86 项测试通过；`git diff --check` 通过。
- OPUS-MT 英中模型已安装并以 CTranslate2 CPU INT8 实测。真实 Qt 后台进程首次两句翻译约 0.86 秒；第二次测试段落约 0.06 秒，进程 PID 相同，确认复用模型。仅为本机短样本耗时，不是不同设备或长稿的性能保证。
- 非模态浮窗真实生成中文结果，并检查中文字体、换行和复制按钮布局。单元测试覆盖选区原文不变、无选区时禁用、长文本拆分不丢 token、输出未结束时拒绝成功、段落保留、浮窗不阻挡主窗口以及忽略旧请求结果。没有做真实会议译文质量评测。
- 查看选项统一为“会后稿（说话者标注）”、“实时草稿”和“会后摘要”；默认选中标注稿，未生成标注时保留已有会后稿访问。旧版本及原声文件未删除。

## 2026-10-12 仅开场无声提醒

82 项测试通过。静音提醒改为每场录音的开场检查：受监测音轨一直无声且有效录音时间达到 30 秒时最多弹出一次；收到声音后，本场后续安静不再触发，更换设备也不重新开启检查。测试覆盖开场收到声音后长时间静音、提醒后恢复声音再静音、暂停排除和仅麦克风模式。旧弹窗没有持久化事件日志，无法据此追溯用户刚才看到的每次弹窗内容。

## 2026-10-10 录音中更换设备

81 项单元测试通过。新增模拟设备切换验证：已有 WAV 内容保留、44.1kHz 新设备转换到原音轨 48kHz、旧回调不再写入、新设备打开或启动失败时保留旧设备、设备索引变化拒绝误接、暂停不自动恢复、麦克风与会议声音独立切换、连续切换释放旧引擎、后台任务期间控件可用、程序刷新不触发切换、收起的下拉框忽略滚轮。会议状态记录设备切换时间及前后设备。未执行真实硬件热切换测试，切换期间可能出现短暂空白。

## 2026-10-10 录音无声提醒

73 项单元测试通过。新增验证 30 秒边界、任一路声音重置计时、暂停时间排除、仅麦克风模式、非阻塞弹窗、同一静音周期不重复弹出及恢复声音后的再次提醒。测试使用模拟设备与时钟，未启动真实录音；录音仍继续保存。

后续按用户要求改为：双路模式只监测会议声音音轨，麦克风有声不再重置静音计时；仅麦克风模式保持检查麦克风。提示列出被监测的设备。更新后的 81 项测试通过，包括麦克风有声但扬声器静音仍弹窗的回归验证。

## 2026-09-28 长会议摘要输出上限修正

54 项测试通过。分段提取不再使用完整纪要模板，输出长度触顶时以更充裕 token 预算精简重试一次；截断输出仍不作为完整摘要保存。完整分段采用内容和模型关联的本地缓存，验证最终生成失败后重试不重复处理已完成分段；缓存文件名为 Windows 原子写入临时后缀留出空间，缓存写入失败不阻断生成。

用户报告的约 25 分钟录音对应转写稿，在本机 Qwen3.5:4B 上按 8 段重新生成并保存摘要；最终引用检查仍有未匹配项，结果明确标为“引用待核对”。这是实际长稿运行完成的验证，不代表摘要事实及归属已经人工审核；未上传用户内容。

## 2026-09-28 转写与摘要一体化

48 项单元测试通过，覆盖三阶段串联、失败不继续、摘要标题提取与长度限制、会议级摘要索引及原文保留。`scripts/verify_summary_pipeline.py` 用 18 秒合成录音，通过真实 Qt QProcess、Turbo GPU、Nemotron 分离和本机 Ollama Qwen3.5:4B 完成全链路；确认默认自动执行、右侧摘要展示、列表短总结、摘要复制，以及切回正文后的播放高亮恢复。截图 `artifacts/summary_pipeline_ui.png` 已检查。未使用真实会议验证摘要事实准确率。

## 2026-09-28 仅麦克风模式

44 项测试通过。模拟采集设备验证仅打开一个麦克风输入、暂停续录、保存单路清单和模式、不生成 system.wav / mixed.wav；兼容旧双路录音。`scripts/verify_microphone_only.py` 使用 62 秒合成音频，通过真实 Qt / GPU 验证单路会后转写与分离、直接回听 microphone.wav、无输出设备仍可开始录音，以及 Turbo / Nemotron 两种实时引擎的单路输入、30 秒暂停边界和完整导出。Nemotron 和会后分离不把整路麦克风认作本机本人。没有采集用户声音，尚未验证实际房间外放的拾音质量。

## 2026-09-28 自动转写与说话者标注

42 项单元测试通过，包含转写成功后接续指定新稿、取消/失败/空稿不启动下一阶段。`scripts/verify_automatic_speakers.py` 使用本地 62 秒合成录音，通过真实 Qt QProcess 完成 Turbo GPU 转写、自动 Nemotron 标注、切换标注稿；会议列表可用高度为 154 px，截图 `artifacts/automatic_speakers_ui.png` 已检查。受限环境首次验证在分离阶段超时；清理该测试进程后，在桌面程序相同权限下复测通过。未新增采集真实录音。

## 2026-09-28 换人边界修正

40 项单元测试通过，新增词级拆分、短应答、英文子词完整性、重叠与未知标签、上下文复核保持原文及重复词顺序测试。最新用户授权样本（110.403 秒）重新生成词时间戳并复用已保存的 Nemotron 发言区间：`H I and J`、`Thomas … item K` 归参会者 2，`yeah apologies` 归参会者 3，与用户指定归属一致。会后复核只校准可匹配原词的时间，不替换原转写文字；这是局部回归证据，不是整场 WER/DER 评估。

同一原音轨重跑实时引擎，`Thomas got item K` 为参会者 2、`yeah apologies …` 为参会者 3。结果明确标为回放重建，保留原版本；并非新一场实时会议的现场验证。原始词时间仍可能偏移，未匹配词保留原时间，自动标签仍需核对。

## Nemotron 3 Diarization 独立试验

Windows GPU/CPU 离线推理均跑通，资源占用较低，但合成样本上仍有说话者合并和分块标签变化；尚未接入正式 UI。安装版本、资源数据和限制见 [NEMOTRON_TRIAL.md](NEMOTRON_TRIAL.md)。

## 摘要引用与弹窗修复

- 修复时间戳格式不一致导致整份摘要被丢弃：兼容短格式及全角括号/冒号，重试仍不匹配则保存明确标注的待核对初稿；无依据时间戳替换为待核对标记。
- 仅连接失败或响应超时提示检查 Ollama，内容错误不再附带误导性启动提示。
- 对话框明确使用浅色背景和深色文字，模拟深色系统配色渲染验证。
- 全部 25 项测试通过，包括格式转换、待核对摘要保存、无效引用移除和错误分类。

## 0.3：本地会后摘要

- 已安装并验证 Ollama Qwen3.5-4B / 9B（Q4_K_M）；4B 默认，9B 可选。9B 实测部分使用系统内存，在 8192 上下文下报告约 5.7GB GPU 分配，并非完全驻留显存。
- 两个模型均用合成会议稿完成真实推理，检查提议与决定区分、被否决预算、未明确日期及时间戳。未使用真实会议内容测试，结果不能代表所有会议的摘要准确率。
- 共 24 项单元测试通过，覆盖分段保留、取消、截断拒绝、无效时间戳拒绝、历史版本与源稿保留。
- 实际 Qt QProcess 集成验证通过：启动摘要时禁用录音和模型切换，完成后保存结果并恢复控件，摘要路径可读取。已渲染检查左侧摘要模型选择及按钮。
- 4B 实际完成三段合成输入的逐段提取和最终合并；尚未用数小时真实会议验证多轮压缩质量。
- 时间戳检查仅验证出现且属于输入材料；不能据此证明每条结论的语义正确性。

## 2026-09-28：紧凑布局与保存目录

- 移除顶部横幅，左右区域按 1:2 分配；会议记录改为紧凑单行并显示常驻滚动条，草稿区使用右侧主要空间。检查了最小窗口与大窗口布局。
- 左侧可选择录音与转写保存文件夹；设置持久化，旧会议保留原位且仍在列表中，模型目录不变。录音或转写期间禁止切换。
- 全部 19 项单元测试通过，包括目录切换、历史保留、导出位置和无效目录回退；用合成数据渲染并检查界面。此次未重复运行声卡/GPU集成测试。
- 本文引用的 `artifacts/` 验证产物仅保存在开发机器，不随源码发布。

## 0.2.1：转写阅读区

- 改为正文优先的段落布局：同音源连续句子合并，段首显示小号灰色起始时间和音源；导出及复制的数据保持逐段原格式。
- 右侧滚动条常驻；仅在底部自动跟随。向上阅读时，以可见段落标识及像素偏移恢复位置，支持新句子追加和较早时间的另一音源结果插入。
- 添加“回到最新”；保留完整实时会话供上翻，不再移除超过 300 段的早期内容。
- 4 项界面回归测试通过：上翻定位在连续更新和前方插入后保持不变、底部跟随/离底停跟随、合并同音源但不跨音源、正文 HTML 字符按原文显示。全部 17 项测试通过。
- 使用合成测试转写稿渲染并检查新版布局；此次是显示层调整，未重复运行声卡/GPU测试。

## 0.2：Turbo 实时转写

日期：2026-09-27，同一 RTX 4060 Ti 8GB 主机。

- Turbo 模型下载到本机，仓库 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`，revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`。
- **实时回放测试通过**：双路中英文合成音频按实际时间速度写入，包含暂停/恢复，两遍共 28.54 秒有效音频；阻断 socket 网络连接后完成 Turbo CUDA 推理。第一次临时文字在启动后 **4.39 秒**出现（包含约 3.58 秒模型加载），单轮识别约 **0.3–0.8 秒**。全流程约 33.53 秒，13 个已确认段落，无积压跳过。
- **真实录音＋界面集成通过**：实际 WASAPI 回环与实体麦克风采集、实时临时文字/确认段落、暂停/继续、结束时收尾、TXT/JSON 导出。
- **GPU 模型交接通过**：录音结束后，自动会后重转等待 Turbo 进程退出，再加载 large-v3；实时与会后版本均存在且能在界面切换。
- **故障隔离通过**：主动结束实时识别子进程后，麦克风录制帧数仍增长，结束保存按钮仍可使用。用户停止实时预览后也继续录音，最终 session 状态为 saved。
- **13 项回归测试通过**：新增临时文字替换、不重复提交、暂停上下文隔离、短尾句冲刷、跨更新周期的最大窗口推进、积压跳过的显式标注、草稿与会后版本分离、Windows 并发读写状态文件替换重试。
- 已修复测试发现的两个问题：窗口上限不足下次更新步长时的停滞；Windows 短暂文件读取锁导致原子替换失败。后续重复实时回放和声卡端到端测试通过。

证据：`artifacts/live_stream_test.json`、`artifacts/live_gui_test.json`、`artifacts/live_preview.png`。实际录音测试文件保存在本机 `MeetingScribeData/verification/`。

以上延迟是**合成测试条件下的实测**，不是所有真实会议的保证。尚未验证讯飞蓝牙耳机或连续数小时开会。临时文字会修订，确认草稿也仍可能识别错，重要内容建议会后重转核对。

## 0.1：录音与会后转写

日期：2026-09-27。主机：Windows，NVIDIA RTX 4060 Ti 8188 MiB，驱动 596.49。

## 已完成

| 验证 | 实际结果 |
|---|---|
| GPU 与运行库 | CTranslate2 检测到 1 张 GPU，支持 `int8_float16`；实际模型加载与推理成功 |
| 双路 WASAPI 采集 | P2418HZm 实体麦克风与 HECATE G1500 MAX 系统回环同时录制；两路各 235808 帧、48 kHz、4.913 秒 |
| 系统原声 | 440 Hz、660 Hz 测试音在系统音轨中检出，非依赖环境麦克风拾音 |
| 暂停 / 继续 | 暂停时播放的 880 Hz 测试音能量小于有效测试音的 1%，未进入有效录音 |
| 采集状态 | 两条音轨 PortAudio 状态异常计数均为 0；系统轨的两处静音间隔按时间补齐 |
| 本地 GPU 转写 | large-v3 + INT8/FP16；两条共约 27 秒的合成中英语音，包含首次模型加载共 10.81 秒，输出 5 段 |
| 断网路径 | GPU 测试阻断 Python socket connect / create_connection；模型只读本地文件，测试通过 |
| 界面集成 | 真实 Qt QProcess 启动 GPU 转写、显示结果、复制纪要素材、取消任务通过；取消不会覆盖之前完整转写 |
| 文件输出 | TXT、Markdown、SRT、JSON、纪要素材和两路原音轨 / 混合音频均生成 |
| 5 项回归测试 | 静音时间对齐、暂停时间排除、不同采样率混合、异常 WAV 头恢复、转写排序与时间戳导出通过 |
| 依赖检查 | `pip check` 通过 |
| 界面检查 | 渲染实际 Qt 窗口并检查中文字体、布局、控件与转写预览；预览在 `artifacts/preview.png` |

机器可读结果：`artifacts/gpu_test.json`、`artifacts/capture_test.json`、`artifacts/gui_test.json`。

## 不应从上述结果推断的结论

- 合成语音测试证明本机执行链路可运行，不等于真实会议识别准确率，也不能据此线性承诺长会议处理时间。
- 尚未测试讯飞耳机、蓝牙通话模式切换、Teams / 腾讯会议 / Zoom 的真实双方讲话。
- 尚未进行数小时连续录制、系统睡眠唤醒或真实断电压力测试。文件恢复用人为损坏 WAV 头的测试验证，不能保证断电零损失。
- 0.4 版可选 Nemotron 预测远端参会者编号；合成样本仍出现编号变化和人物合并，不能视为可靠身份识别。
- `HF_HUB_OFFLINE` 和 socket 测试验证转写代码的离线路径；它们不是对整台 Windows 或其他软件流量的网络审计。

首次正式使用前，建议连接实际耳机，录制一分钟双向对话，确认两路声音、音量和转写结果。

## 2026-09-28 Nemotron 会后标注接入

0.5 实时试用：34 项单元测试通过。62.21 秒合成中英音频按 2×速度逐步发布两路 PCM，在实际 Qt QProcess 中完成实时快照、三个参会者编号及本机标签、暂停续录和最终导出。进程启动到首次文字约 11.39 秒（含导入及冷加载，并非稳态延迟），总墙钟 33.84 秒；PyTorch 峰值分配 1634.95 MiB，不含驱动及桌面。初次/重复运行速度会变化。参见 `artifacts/nemotron_live_check.json` 与 `scripts/verify_nemotron_live.py`。

原生增量 RNNT 解码器在 5 秒合成样本上的 token 与官方 Transformers 流式 `generate` 完全一致；单独 ASR 62 秒样本约 6.6–8 秒处理。没有用真实会议评估 WER/DER；自动语言切换、时间戳、标签和重叠发言仍有错误，不能称为可靠逐人转写。较长会议的缓存稳定性尚未验证。官方 NeMo 多说话者条件解码未接入。

内置回听：新增句子级高亮测试，覆盖合并段落内单句、重叠发言、静音间隔、清空及关闭跟随后的滚动保护。`scripts/verify_playback.py` 使用静音合成 WAV 通过真实 Qt Multimedia 验证解码、时钟推进、暂停、跳转、倍速、点击句子定位、高亮、播放结束及切换会议清理；未进行扬声器听感验证。界面截图 `artifacts/playback_preview.png` 已检查。

短句匹配后续修正：29 项单元测试通过，新增 0.08 秒重叠的短句、竞争说话者、无重叠、发言覆盖不足及人工标签保留测试。`scripts/verify_speaker_edit.py` 实际操作 Qt 对话框并验证保存、版本切换和预览。对用户授权的录音重用已保存预测生成新稿，39.78–40.14 秒标签由未确定改为参会者 2；其余标签、原文和时间戳未改变。旧稿和原始音轨保留。

- 27 项单元测试通过，包含保守的单人/多人/未知标签映射、原文保留、重复标注以及原稿/草稿指针不被覆盖。
- `scripts/verify_diarization_ui.py` 使用已知的 62 秒三人合成音频，转为 48 kHz 后从真实 Qt 按钮启动隔离环境 GPU worker；验证自动切换标注稿、原文及时间戳保留、原稿指针保留和预测区间输出。
- 实际 Qt 截图已检查，见 `artifacts/diarization_gui_preview.png`；机器结果 `artifacts/diarization_ui_check.json`。没有访问真实会议内容。
- 模型返回三组编号，但后半段出现人物合并/编号变化。仅证明接入链路可运行，尚未验证真实会议准确率、长会议标签稳定性或实时逐人字幕。

## 2026-10-02 continuous-meeting regression
- Local evidence: the latest 84m30s meeting recorded zero gaps on both tracks. Live output contained 0 segments and `complete=false`, reason: backlog exceeded 30 seconds. The UI previously reported success despite this incomplete result.
- Its 9B summary used 66 chunks; the previous request payload unloaded the model after every request. Requests now retain it for the task, with explicit CLI cleanup.
- `python -m unittest discover -s tests -q`: 68 tests passed. Added concurrent capture controls, queued second session, exact source routing through processing stages, preview isolation, incomplete-live messaging, queue recovery, opt-out during live drain, and cold-start/sustained-backlog checks.
- `scripts/render_background_preview.py`: isolated Qt layout rendered with an enabled recording button, queued meeting and circular processing icon. No microphone capture or model inference is launched by this preview.
- No full-length GPU throughput benchmark was rerun; sustained device capture under concurrent model inference remains to be verified in a real meeting. Existing running app instances must be reopened to load these changes.
- Follow-up: 69 tests passed after making full summary recomputation the default. A regression runs twice against existing cache files and verifies all input chunks, intermediate reductions and final output invoke generation again. Explicit `--reuse-cache` remains available; elapsed generation time is saved and displayed. These tests use mocked model responses, not a GPU speed benchmark.
