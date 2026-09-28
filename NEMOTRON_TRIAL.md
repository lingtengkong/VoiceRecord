# Nemotron 3 Diarization 本地试验

> 本文保留早期独立试验的环境与结果。当前版本已接入 Nemotron 实时文字及说话者标签，会后也已串联转写、分离和摘要；最新入口以 [README](README.md) 为准。下文“尚未接入”描述的是试验时点。

日期：2026-09-28。Windows，RTX 4060 Ti 8GB。没有读取或上传真实会议；推理时阻断 Python socket 连接。

## 安装与兼容性

- 官方模型：<https://huggingface.co/nvidia/Nemotron-3-Diarization>，固定 revision `f667ed73aee57d40cc39428eb768b4fd87a0a29e`。
- 参数实际计数：99,226,504。Safetensors 权重约 378.6 MiB；另下载了约 102.1 MiB 的 Q8 GGUF。
- 官方 NeMo-Speech.cpp v0.1.0 Windows CUDA 包通过发布方 SHA256 校验，但加载新模型报 `pre_ln transformer variant is not supported`。该路线没有完成推理。
- 成功路线：独立 Python 3.12 环境，PyTorch 2.11.0+cu128，Transformers 5.18.0.dev0（源码 revision `07338b6c74a578868368e6e549dea83414e4b8cb`），librosa 1.0.0。`pip check` 通过。
- 环境位于数据目录 `tools/nemotron-python`；模型位于 `models/nemotron-3-diarization`。未修改现有录音程序的 Python 环境。后续 0.4 版已接入可选的会后标注按钮，使用低延迟分块模式处理已保存的音轨，实时逐人标签尚未接入。

## 资源实测

| 测试 | 音频长度 | 模型加载 | 处理时间 | PyTorch 峰值显存分配 | 进程峰值 RAM |
|---|---:|---:|---:|---:|---:|
| GPU，全段模式 | 62.21 秒 | 0.93 秒 | 0.44 秒 | 413 MiB | 1431 MiB |
| CPU，全段模式，4 线程 | 62.21 秒 | 0.69 秒 | 1.49 秒 | 0 | 1138 MiB |
| GPU，低延迟分块模式 | 62.21 秒 | 0.94 秒 | 2.47 秒 | 405 MiB | 1436 MiB |
| GPU，重叠讲话样本 | 30 秒 | 0.95 秒 | 0.38 秒 | 406 MiB | 1414 MiB |

加载时间不包含 Python 启动和依赖导入。处理时间包含特征提取和模型推理，不含最终标签提取。显存为 PyTorch 分配统计，不包括全部 CUDA 驱动上下文开销或桌面占用。RAM 每 50ms 采样，属于近似峰值。不能线性外推数小时会议；长文件应使用带缓存的分块模式。

## 分离结果与限制

- 初始 Windows 合成语音中，Kangkang 与 Yaoyao 对相同文本生成了逐样本完全相同的波形，不能作为两个已知不同说话人；已将第三路替换为 Zira Desktop，重新生成样本并复测。
- 修正后的交替发言样本采用 Huihui、Kangkang、Zira Desktop 三个合成语音配置，每种出现两次，含中英文。全段模式输出两组，合并了两路中文配置。
- 分块模式输出三组，但同一配置第二次发言的标签发生变化；人数正确不等于逐段身份正确。
- 重叠样本将三路分别从 0、7、14 秒加入，输出三组，并在约 7.44–9.54 秒、14.05–16.25 秒出现相应重叠标签。
- 这些是合成样本上的观察，不是经过人工标注的真实会议 DER 评测。还未验证真实会议、多人回声、长时间标签稳定性或实时集成。
- 当前 Transformers 开发版启动时有一条图像处理器文档校验日志，未阻止音频推理；结果文件和退出码均正常。

结论：硬件与运行链路可行，资源要求低。标签准确性和稳定性仍需真实多人样本验证，尚不适合作为无需核对的身份标注。

## 复跑

使用独立环境中的 Python 执行 `scripts/test_nemotron.py`，传入 `--model`（模型目录）、`--audio`（16kHz 单声道 WAV）、`--device cpu` 或 `cuda`、`--output`（结果 JSON）。可选 `--streaming` 测试带缓存分块模式；`--reference` 接收合成测试的已知发言区间。

本机结果：`artifacts/nemotron3_cuda.json`、`nemotron3_cpu.json`、`nemotron3_overlap.json`、`nemotron3_streaming.json`。依赖快照：`artifacts/nemotron-requirements-lock.txt`。以上测试产物不随源码上传。
