# -*- coding: utf-8 -*-
"""llama-server 参数定义表与命令行构建。

这个文件是唯一的参数事实来源:
- PARAM_DEFINITIONS: 界面控件与命令预览都由它生成
- PARAM_GROUP_LABELS / PARAM_GROUPS: 分组与界面标签页
- build_argv / build_command_string: 参数字典 → 命令行
"""

import shlex
from dataclasses import dataclass
from typing import Any, Optional
from pathlib import Path


# ────────────────────────── 参数定义 ──────────────────────────

@dataclass
class ParamDef:
    """单个 llama-server 参数的定义"""
    key: str                          # JSON 键名
    cli_flag: str                     # 长 flag, 如 "--ctx-size"
    cli_short: str = ""               # 短 flag, 如 "-c"
    negation_flag: str = ""           # 负向 flag, 如 "--no-context-shift"
    label: str = ""                   # GUI 显示文字
    group: str = "Advanced"           # 分组: Server/Model/Performance/Sampling/Reasoning/Speculative/Multimodal/Logging/Advanced
    type: str = "str"                 # int/float/str/choice/bool/bool_pair/path
    default: Any = None               # 默认值
    choices: Optional[list] = None    # choice 类型的可选值
    include: str = "non_default"      # always / non_default / never
    help_text: str = ""               # 帮助文字
    alias: str = ""                   # 额外的长别名, 如 "--gpu-layers"
    extra_flags: tuple = ()           # 其余等价写法(含负向别名), 仅用于识别与文档, 不参与命令构建


# 缓存类型可选值
_CACHE_TYPES = ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"]

# 内置 chat 模板列表
_CHAT_TEMPLATES = [
    "", "bailing", "bailing-think", "bailing2", "chatglm3", "chatglm4", "chatml",
    "command-r", "deepseek", "deepseek-ocr", "deepseek2", "deepseek3",
    "exaone-moe", "exaone3", "exaone4", "falcon3", "gemma", "gigachat", "glmedge",
    "gpt-oss", "granite", "granite-4.0", "granite-4.1", "grok-2", "hunyuan-dense",
    "hunyuan-moe", "hunyuan-vl", "kimi-k2", "llama2", "llama2-sys",
    "llama2-sys-bos", "llama2-sys-strip", "llama3", "llama4", "megrez", "minicpm",
    "mistral-v1", "mistral-v3", "mistral-v3-tekken", "mistral-v7",
    "mistral-v7-tekken", "monarch", "openchat", "orion", "pangu-embedded",
    "phi3", "phi4", "rwkv-world", "seed_oss", "smolvlm", "solar-open", "vicuna",
    "vicuna-orca", "yandex", "zephyr",
]

# 三态 0/1 选项
_ZERO_ONE = ["", "0", "1"]


PARAM_DEFINITIONS: list[ParamDef] = [

    # ═══════════════ Server 组 ═══════════════
    ParamDef("host", "--host", label="监听地址", group="Server", type="str", default="127.0.0.1", include="always", help_text="IP 地址(逗号分隔多个), 或以 .sock 结尾绑定 UNIX socket; 多个 TCP 地址时 :: 仅绑定 IPv6"),
    ParamDef("port", "--port", label="监听端口", group="Server", type="int", default=8080, include="always", help_text="端口号"),
    ParamDef("api_key", "--api-key", label="API Key", group="Server", type="str", default="", help_text="API 认证密钥, 多个用逗号分隔"),
    ParamDef("api_key_file", "--api-key-file", label="API Key文件", group="Server", type="path", default="", help_text="存放 API Key 的文件, 每行一个, # 开头的行视为注释"),
    ParamDef("timeout", "--timeout", "-to", label="超时(秒)", group="Server", type="int", default=3600, help_text="服务端读写超时"),
    ParamDef("alias", "--alias", "-a", label="模型别名", group="Server", type="str", default="", help_text="API 中的模型名称, 逗号分隔多个; 留空则自动使用模型文件名(否则 API 会返回完整本地路径)"),
    ParamDef("tags", "--tags", label="模型标签", group="Server", type="str", default="", help_text="模型标签, 逗号分隔(仅信息展示)"),
    ParamDef("threads_http", "--threads-http", label="HTTP线程数", group="Server", type="int", default=-1, help_text="处理 HTTP 请求的线程数"),
    ParamDef("reuse_port", "--reuse-port", label="端口复用", group="Server", type="bool", default=False, help_text="允许多个 socket 绑定同一端口"),
    ParamDef("sse_ping_interval", "--sse-ping-interval", label="SSE心跳(秒)", group="Server", type="int", default=30, help_text="SSE ping 间隔 (-1=禁用)"),
    ParamDef("api_prefix", "--api-prefix", label="API前缀", group="Server", type="str", default="", help_text="API 路径前缀, 不含结尾斜杠"),
    ParamDef("path", "--path", label="静态文件目录", group="Server", type="path", default="", help_text="提供静态文件的目录"),
    ParamDef("cors_origins", "--cors-origins", label="CORS来源", group="Server", type="str", default="", help_text="允许的 CORS 来源, 逗号分隔; 特殊值 localhost 表示仅对 localhost 回显 Origin 头"),
    ParamDef("cors_methods", "--cors-methods", label="CORS方法", group="Server", type="str", default="", help_text="允许的 CORS 方法"),
    ParamDef("cors_headers", "--cors-headers", label="CORS请求头", group="Server", type="str", default="", help_text="允许的 CORS 请求头"),
    ParamDef("cors_credentials", "--cors-credentials", negation_flag="--no-cors-credentials", label="CORS凭证", group="Server", type="bool_pair", default=None, help_text="是否允许 CORS 携带凭证"),
    ParamDef("ssl_key_file", "--ssl-key-file", label="SSL私钥", group="Server", type="path", default="", help_text="PEM 格式 SSL 私钥文件"),
    ParamDef("ssl_cert_file", "--ssl-cert-file", label="SSL证书", group="Server", type="path", default="", help_text="PEM 格式 SSL 证书文件"),
    ParamDef("ui", "--ui", "--webui", negation_flag="--no-ui", label="Web UI", group="Server", type="bool_pair", default=None, extra_flags=("--no-webui",), help_text="启用内置 Web UI"),
    ParamDef("ui_config", "--ui-config", "--webui-config", label="WebUI配置", group="Server", type="str", default="", help_text="Web UI 默认设置的 JSON 字符串"),
    ParamDef("ui_config_file", "--ui-config-file", "--webui-config-file", label="WebUI配置文件", group="Server", type="path", default="", help_text="Web UI 设置的 JSON 文件"),
    ParamDef("metrics", "--metrics", label="Prometheus指标", group="Server", type="bool", default=False, help_text="启用 Prometheus 兼容指标端点"),
    ParamDef("props", "--props", label="属性端点", group="Server", type="bool", default=False, help_text="允许通过 POST /props 修改全局属性"),
    ParamDef("slots", "--slots", negation_flag="--no-slots", label="槽位端点", group="Server", type="bool_pair", default=None, help_text="暴露槽位监控端点"),
    ParamDef("slot_save_path", "--slot-save-path", label="槽位保存目录", group="Server", type="path", default="", help_text="保存槽位 KV 缓存的目录"),
    ParamDef("media_path", "--media-path", label="媒体目录", group="Server", type="path", default="", help_text="本地媒体文件目录(file:// 相对路径)"),
    ParamDef("connect", "--connect", label="P2P隧道", group="Server", type="bool", default=False, help_text="(实验性) 用 llama-connect 为服务器建立点对点隧道 (默认禁用)"),
    ParamDef("connect_code", "--connect-code", label="隧道配对码", group="Server", type="str", default="", help_text="手动指定 --connect 的 40 字符配对码 (默认每次运行自动生成)"),
    ParamDef("embedding", "--embedding", "--embeddings", label="嵌入模式", group="Server", type="bool", default=False, help_text="仅支持嵌入用例"),
    ParamDef("reranking", "--rerank", "--reranking", label="重排序模式", group="Server", type="bool", default=False, help_text="启用重排序端点"),
    ParamDef("pooling", "--pooling", label="池化类型", group="Server", type="choice", default="", choices=["", "none", "mean", "cls", "last", "rank"], help_text="嵌入的池化方式"),
    ParamDef("embd_normalize", "--embd-normalize", label="嵌入归一化", group="Server", type="int", default=2, help_text="-1=无, 0=max, 1=taxicab, 2=欧氏, >2=p范数"),
    ParamDef("sleep_idle_seconds", "--sleep-idle-seconds", label="空闲休眠(秒)", group="Server", type="int", default=-1, help_text="空闲该秒数后服务器休眠 (-1=禁用)"),
    ParamDef("slot_prompt_similarity", "--slot-prompt-similarity", "-sps", label="槽位匹配阈值", group="Server", type="float", default=0.10, help_text="复用槽位所需的提示相似度 (0.0=禁用)"),

    # ═══════════════ Model 组 ═══════════════
    ParamDef("model", "--model", "-m", label="模型文件", group="Model", type="str", default="", include="always", help_text="模型路径(自动注入)"),
    ParamDef("model_url", "--model-url", "-mu", label="模型下载地址", group="Model", type="str", default="", help_text="从 URL 下载并加载模型 (默认未使用)"),
    ParamDef("docker_repo", "--docker-repo", "-dr", label="Docker模型库", group="Model", type="str", default="", help_text="Docker Hub 模型仓库 [<repo>/]<model>[:quant], repo 默认 ai/, quant 默认 latest"),
    ParamDef("hf_repo", "--hf-repo", "-hf", label="HF模型仓库", group="Model", type="str", default="", extra_flags=("-hfr",), help_text="Hugging Face 仓库 <user>/<model>[:quant], quant 默认 Q4_K_M; 可用 mmproj 会自动下载(禁用加 --no-mmproj)"),
    ParamDef("hf_file", "--hf-file", "-hff", label="HF模型文件", group="Model", type="str", default="", help_text="指定 HF 仓库中的具体文件, 覆盖 --hf-repo 的 quant 选择"),
    ParamDef("hf_token", "--hf-token", "-hft", label="HF访问令牌", group="Model", type="str", default="", help_text="Hugging Face 访问令牌 (默认取 HF_TOKEN 环境变量)"),
    ParamDef("ctx_size", "--ctx-size", "-c", label="上下文长度", group="Model", type="int", default=0, help_text="提示上下文大小 (0=从模型加载)"),
    ParamDef("n_gpu_layers", "--n-gpu-layers", "-ngl", label="GPU层数", group="Model", type="str", default="auto", help_text="VRAM 层数 (数字/auto/all)", alias="--gpu-layers"),
    ParamDef("predict", "--predict", "-n", label="预测Token数", group="Model", type="int", default=-1, help_text="预测 token 数 (-1=无限)", alias="--n-predict"),
    ParamDef("keep", "--keep", label="保留Token数", group="Model", type="int", default=0, help_text="从初始提示保留的 token 数 (-1=全部)"),
    ParamDef("batch_size", "--batch-size", "-b", label="批大小", group="Model", type="int", default=2048, help_text="逻辑最大批大小"),
    ParamDef("ubatch_size", "--ubatch-size", "-ub", label="物理批大小", group="Model", type="int", default=512, help_text="物理最大批大小"),
    ParamDef("parallel", "--parallel", "-np", label="并行槽位", group="Model", type="int", default=-1, help_text="服务器槽数 (-1=自动)"),
    ParamDef("swa_full", "--swa-full", label="全尺寸SWA缓存", group="Model", type="bool", default=False, help_text="使用全尺寸 SWA 缓存"),
    ParamDef("split_mode", "--split-mode", "-sm", label="多GPU拆分", group="Model", type="choice", default="", choices=["", "none", "layer", "row", "tensor"], help_text="多 GPU 拆分方式"),
    ParamDef("tensor_split", "--tensor-split", "-ts", label="张量拆分比例", group="Model", type="str", default="", help_text="各 GPU 卸载比例, 逗号分隔, 如 3,1"),
    ParamDef("main_gpu", "--main-gpu", "-mg", label="主GPU", group="Model", type="int", default=0, help_text="主 GPU 索引"),
    ParamDef("device", "--device", "-dev", label="使用设备", group="Model", type="str", default="", help_text="卸载使用的设备列表(none=不卸载)"),
    ParamDef("rpc", "--rpc", label="RPC服务器", group="Model", type="str", default="", help_text="RPC 服务器列表 host:port, 逗号分隔"),
    ParamDef("fit", "--fit", "-fit", label="自动适配显存", group="Model", type="choice", default="", choices=["", "on", "off"], help_text="自动调整未设置参数以适配设备显存 (帮助中默认为 on)"),
    ParamDef("fit_target", "--fit-target", "-fitt", label="适配余量(MiB)", group="Model", type="str", default="", help_text="每设备目标余量, 逗号分隔, 默认 1024"),
    ParamDef("fit_ctx", "--fit-ctx", "-fitc", label="最小适配上下文", group="Model", type="int", default=4096, help_text="--fit 可设置的最小 ctx 大小"),
    ParamDef("cpu_moe", "--cpu-moe", "-cmoe", label="MoE留在CPU", group="Model", type="bool", default=False, help_text="MoE 权重全部保留在 CPU"),
    ParamDef("n_cpu_moe", "--n-cpu-moe", "-ncmoe", label="MoE层留CPU", group="Model", type="int", default=0, help_text="前 N 层 MoE 权重保留在 CPU"),
    ParamDef("moe_cache_mib", "--moe-cache-mib", label="MoE专家缓存", group="Model", type="int", default=0, help_text="常驻 GPU 缓存的 CPU MoE 专家权重上限 (MiB); 多卡时按 --tensor-split 比例拆分, 0=禁用"),
    ParamDef("n_cpu_ffn", "--n-cpu-ffn", "-ncffn", label="FFN层留CPU", group="Model", type="int", default=0, help_text="前 N 层稠密 FFN 权重保留在 CPU (仅稠密模型, MoE 专家权重用 --n-cpu-moe)"),
    ParamDef("override_tensor", "--override-tensor", "-ot", label="覆盖张量缓冲", group="Model", type="str", default="", help_text="张量名模式=缓冲类型, 逗号分隔"),
    ParamDef("check_tensors", "--check-tensors", label="校验张量", group="Model", type="bool", default=False, help_text="检查模型张量数据是否含非法值"),
    ParamDef("override_kv", "--override-kv", label="覆盖元数据", group="Model", type="str", default="", help_text="KEY=TYPE:VALUE 覆盖模型元数据"),

    # 预设模型快捷开关 (help 的 example-specific params 区): 选中即自动下载并使用对应权重
    ParamDef("preset_embd_gemma", "--embd-gemma-default", label="预设·EmbeddingGemma", group="Model", type="bool", default=False, help_text="使用默认 EmbeddingGemma 嵌入模型 (会从网络下载权重)"),
    ParamDef("preset_fim_qwen_1_5b", "--fim-qwen-1.5b-default", label="预设·Qwen2.5-Coder-1.5B", group="Model", type="bool", default=False, help_text="使用默认 Qwen 2.5 Coder 1.5B (会从网络下载权重)"),
    ParamDef("preset_fim_qwen_3b", "--fim-qwen-3b-default", label="预设·Qwen2.5-Coder-3B", group="Model", type="bool", default=False, help_text="使用默认 Qwen 2.5 Coder 3B (会从网络下载权重)"),
    ParamDef("preset_fim_qwen_7b", "--fim-qwen-7b-default", label="预设·Qwen2.5-Coder-7B", group="Model", type="bool", default=False, help_text="使用默认 Qwen 2.5 Coder 7B (会从网络下载权重)"),
    ParamDef("preset_fim_qwen_7b_spec", "--fim-qwen-7b-spec", label="预设·Qwen2.5-Coder-7B+草稿", group="Model", type="bool", default=False, help_text="Qwen 2.5 Coder 7B + 0.5B 草稿模型做投机解码 (会从网络下载权重)"),
    ParamDef("preset_fim_qwen_14b_spec", "--fim-qwen-14b-spec", label="预设·Qwen2.5-Coder-14B+草稿", group="Model", type="bool", default=False, help_text="Qwen 2.5 Coder 14B + 0.5B 草稿模型做投机解码 (会从网络下载权重)"),
    ParamDef("preset_fim_qwen_30b", "--fim-qwen-30b-default", label="预设·Qwen3-Coder-30B-A3B", group="Model", type="bool", default=False, help_text="使用默认 Qwen 3 Coder 30B A3B Instruct (会从网络下载权重)"),
    ParamDef("preset_gpt_oss_20b", "--gpt-oss-20b-default", label="预设·gpt-oss-20b", group="Model", type="bool", default=False, help_text="使用 gpt-oss-20b (会从网络下载权重)"),
    ParamDef("preset_gpt_oss_120b", "--gpt-oss-120b-default", label="预设·gpt-oss-120b", group="Model", type="bool", default=False, help_text="使用 gpt-oss-120b (会从网络下载权重)"),
    ParamDef("preset_vision_gemma_4b", "--vision-gemma-4b-default", label="预设·Gemma3-4B-QAT", group="Model", type="bool", default=False, help_text="使用 Gemma 3 4B QAT (会从网络下载权重)"),
    ParamDef("preset_vision_gemma_12b", "--vision-gemma-12b-default", label="预设·Gemma3-12B-QAT", group="Model", type="bool", default=False, help_text="使用 Gemma 3 12B QAT (会从网络下载权重)"),

    # ═══════════════ Performance 组 ═══════════════
    ParamDef("threads", "--threads", "-t", label="CPU线程数", group="Performance", type="int", default=-1, help_text="生成时 CPU 线程数"),
    ParamDef("threads_batch", "--threads-batch", "-tb", label="批处理线程", group="Performance", type="int", default=-1, help_text="批处理 CPU 线程数"),
    ParamDef("cpu_mask", "--cpu-mask", "-C", label="CPU掩码", group="Performance", type="str", default="", help_text="CPU 亲和掩码(任意长度十六进制)"),
    ParamDef("cpu_range", "--cpu-range", "-Cr", label="CPU范围", group="Performance", type="str", default="", help_text="CPU 亲和范围 lo-hi"),
    ParamDef("cpu_strict", "--cpu-strict", label="严格CPU绑定", group="Performance", type="choice", default="", choices=_ZERO_ONE, help_text="是否使用严格 CPU 绑定"),
    ParamDef("prio", "--prio", label="线程优先级", group="Performance", type="int", default=0, help_text="low(-1)/normal(0)/medium(1)/high(2)/realtime(3)"),
    ParamDef("poll", "--poll", label="轮询级别", group="Performance", type="int", default=50, help_text="等待工作的轮询级别 (0=不轮询)"),
    ParamDef("cpu_mask_batch", "--cpu-mask-batch", "-Cb", label="批CPU掩码", group="Performance", type="str", default="", help_text="批处理 CPU 亲和掩码"),
    ParamDef("cpu_range_batch", "--cpu-range-batch", "-Crb", label="批CPU范围", group="Performance", type="str", default="", help_text="批处理 CPU 亲和范围 lo-hi"),
    ParamDef("cpu_strict_batch", "--cpu-strict-batch", label="批严格绑定", group="Performance", type="choice", default="", choices=_ZERO_ONE, help_text="批处理是否严格 CPU 绑定"),
    ParamDef("prio_batch", "--prio-batch", label="批线程优先级", group="Performance", type="int", default=0, help_text="批处理线程优先级 0-3"),
    ParamDef("poll_batch", "--poll-batch", label="批轮询级别", group="Performance", type="choice", default="", choices=_ZERO_ONE, help_text="批处理轮询级别"),
    ParamDef("flash_attn", "--flash-attn", "-fa", label="Flash Attention", group="Performance", type="choice", default="auto", choices=["auto", "on", "off"], help_text="Flash Attention 开关"),
    ParamDef("cache_type_k", "--cache-type-k", "-ctk", label="KV缓存K类型", group="Performance", type="choice", default="f16", choices=_CACHE_TYPES, help_text="KV cache K 数据类型"),
    ParamDef("cache_type_v", "--cache-type-v", "-ctv", label="KV缓存V类型", group="Performance", type="choice", default="f16", choices=_CACHE_TYPES, help_text="KV cache V 数据类型"),
    ParamDef("load_mode", "--load-mode", "-lm", label="加载模式", group="Performance", type="choice", default="auto", choices=["auto", "none", "mmap", "mlock", "mmap+mlock", "dio"], help_text="模型加载模式"),
    ParamDef("lazy_mode", "--lazy-mode", "-lzm", label="惰性加载", group="Performance", type="choice", default="auto", choices=["on", "auto", "off"], help_text="按需读取部分张量(需 mmap)"),
    ParamDef("numa", "--numa", label="NUMA策略", group="Performance", type="choice", default="", choices=["", "distribute", "isolate", "numactl"], help_text="NUMA 优化策略"),
    ParamDef("kv_offload", "--kv-offload", "-kvo", negation_flag="--no-kv-offload", label="KV卸载", group="Performance", type="bool_pair", default=None, extra_flags=("-nkvo",), help_text="是否启用 KV cache 卸载"),
    ParamDef("op_offload", "--op-offload", negation_flag="--no-op-offload", label="算子卸载", group="Performance", type="bool_pair", default=None, help_text="将主机张量运算卸载到设备"),
    ParamDef("repack", "--repack", "-nr", negation_flag="--no-repack", label="权重重打包", group="Performance", type="bool_pair", default=None, help_text="是否启用权重重打包"),
    ParamDef("no_host", "--no-host", label="禁用Host缓冲", group="Performance", type="bool", default=False, help_text="绕过 host 缓冲以使用额外缓冲"),
    ParamDef("context_shift", "--context-shift", negation_flag="--no-context-shift", label="上下文移位", group="Performance", type="bool_pair", default=None, help_text="无限文本生成时的上下文移位"),
    ParamDef("cont_batching", "--cont-batching", "-cb", negation_flag="--no-cont-batching", label="连续批处理", group="Performance", type="bool_pair", default=None, extra_flags=("-nocb",), help_text="连续批处理 (动态批处理)"),
    ParamDef("warmup", "--warmup", negation_flag="--no-warmup", label="预热", group="Performance", type="bool_pair", default=None, help_text="是否用空运行进行预热"),
    ParamDef("cache_prompt", "--cache-prompt", negation_flag="--no-cache-prompt", label="提示缓存", group="Performance", type="bool_pair", default=None, help_text="是否启用提示缓存"),
    ParamDef("cache_reuse", "--cache-reuse", label="缓存复用块", group="Performance", type="int", default=0, help_text="通过 KV 移位复用缓存的最小块大小 (0=禁用)"),
    ParamDef("cache_ram", "--cache-ram", "-cram", label="缓存上限(MiB)", group="Performance", type="int", default=8192, help_text="提示缓存最大大小 (-1=不限, 0=禁用)"),
    ParamDef("cache_idle_slots", "--cache-idle-slots", negation_flag="--no-cache-idle-slots", label="空闲槽缓存", group="Performance", type="bool_pair", default=None, help_text="新任务时将空闲槽保存到提示缓存 (需启用 --cache-ram)"),
    ParamDef("kv_unified", "--kv-unified", "-kvu", negation_flag="--no-kv-unified", label="统一KV缓冲", group="Performance", type="bool_pair", default=None, extra_flags=("-no-kvu",), help_text="所有序列共享单一 KV 缓冲"),
    ParamDef("kv_unified_per_slot", "--kv-unified-per-slot", label="单槽KV上限", group="Performance", type="int", default=0, help_text="每个并行槽的上下文上限 (0=不设置)"),
    ParamDef("ctx_checkpoints", "--ctx-checkpoints", "-ctxcp", label="上下文检查点数", group="Performance", type="int", default=32, help_text="每槽最大上下文检查点数", alias="--swa-checkpoints"),
    ParamDef("checkpoint_min_step", "--checkpoint-min-step", "-cms", label="检查点间隔", group="Performance", type="int", default=8192, help_text="上下文检查点最小 token 间隔 (0=无限制)"),
    ParamDef("spm_infill", "--spm-infill", label="SPM填充模式", group="Performance", type="bool", default=False, help_text="填充使用 Suffix/Prefix/Middle 模式"),
    ParamDef("perf", "--perf", negation_flag="--no-perf", label="性能计时", group="Performance", type="bool_pair", default=None, help_text="启用内部性能计时"),

    # ═══════════════ Sampling 组 ═══════════════
    ParamDef("seed", "--seed", "-s", label="随机种子", group="Sampling", type="int", default=-1, help_text="RNG 种子 (-1=随机)"),
    ParamDef("temperature", "--temp", "--temperature", label="温度", group="Sampling", type="float", default=0.8, help_text="采样温度"),
    ParamDef("top_k", "--top-k", label="Top-K", group="Sampling", type="int", default=40, help_text="Top-K 采样 (0=禁用)"),
    ParamDef("top_p", "--top-p", label="Top-P", group="Sampling", type="float", default=0.95, help_text="Top-P 采样 (1.0=禁用)"),
    ParamDef("min_p", "--min-p", label="Min-P", group="Sampling", type="float", default=0.05, help_text="Min-P 采样 (0.0=禁用)"),
    ParamDef("top_nsigma", "--top-nsigma", "--top-n-sigma", label="Top-N-Sigma", group="Sampling", type="float", default=-1.0, help_text="Top-N-Sigma 采样 (-1.0=禁用)"),
    ParamDef("typical_p", "--typical", "--typical-p", label="Typical-P", group="Sampling", type="float", default=1.0, help_text="局部典型采样 (1.0=禁用)"),
    ParamDef("xtc_probability", "--xtc-probability", label="XTC概率", group="Sampling", type="float", default=0.0, help_text="XTC 概率 (0.0=禁用)"),
    ParamDef("xtc_threshold", "--xtc-threshold", label="XTC阈值", group="Sampling", type="float", default=0.1, help_text="XTC 阈值 (1.0=禁用)"),
    ParamDef("repeat_penalty", "--repeat-penalty", label="重复惩罚", group="Sampling", type="float", default=1.0, help_text="重复序列惩罚 (1.0=禁用)"),
    ParamDef("repeat_last_n", "--repeat-last-n", label="惩罚窗口", group="Sampling", type="int", default=64, help_text="惩罚考虑的最近 token 数 (0=禁用)"),
    ParamDef("presence_penalty", "--presence-penalty", label="存在惩罚", group="Sampling", type="float", default=0.0, help_text="重复存在惩罚 (0.0=禁用)"),
    ParamDef("frequency_penalty", "--frequency-penalty", label="频率惩罚", group="Sampling", type="float", default=0.0, help_text="重复频率惩罚 (0.0=禁用)"),
    ParamDef("dry_multiplier", "--dry-multiplier", label="DRY强度", group="Sampling", type="float", default=0.0, help_text="DRY 采样倍率 (0.0=禁用)"),
    ParamDef("dry_base", "--dry-base", label="DRY基数", group="Sampling", type="float", default=1.75, help_text="DRY 采样基值"),
    ParamDef("dry_allowed_length", "--dry-allowed-length", label="DRY允许长度", group="Sampling", type="int", default=2, help_text="DRY 采样允许长度"),
    ParamDef("dry_penalty_last_n", "--dry-penalty-last-n", label="DRY惩罚窗口", group="Sampling", type="int", default=64, help_text="DRY 惩罚最近 token 数 (0=禁用)"),
    ParamDef("dry_sequence_breaker", "--dry-sequence-breaker", label="DRY序列断开符", group="Sampling", type="str", default="", help_text="DRY 断开符; none 表示不使用"),
    ParamDef("adaptive_target", "--adaptive-target", label="Adaptive目标", group="Sampling", type="float", default=-1.0, help_text="adaptive-p 目标概率 (负数=禁用)"),
    ParamDef("adaptive_decay", "--adaptive-decay", label="Adaptive衰减", group="Sampling", type="float", default=0.9, help_text="adaptive-p 目标自适应衰减率 0.0-0.99"),
    ParamDef("dynatemp_range", "--dynatemp-range", label="动态温度范围", group="Sampling", type="float", default=0.0, help_text="动态温度范围 (0.0=禁用)"),
    ParamDef("dynatemp_exp", "--dynatemp-exp", label="动态温度指数", group="Sampling", type="float", default=1.0, help_text="动态温度指数"),
    ParamDef("mirostat", "--mirostat", label="Mirostat", group="Sampling", type="int", default=0, help_text="Mirostat 采样 (0=禁用,1/2)"),
    ParamDef("mirostat_lr", "--mirostat-lr", label="Mirostat LR", group="Sampling", type="float", default=0.1, help_text="Mirostat 学习率"),
    ParamDef("mirostat_ent", "--mirostat-ent", label="Mirostat熵", group="Sampling", type="float", default=5.0, help_text="Mirostat 目标熵"),
    ParamDef("samplers", "--samplers", label="采样器顺序", group="Sampling", type="str", default="", help_text="按顺序使用的采样器, 分号分隔"),
    ParamDef("sampler_seq", "--sampler-seq", "--sampling-seq", label="采样器序列", group="Sampling", type="str", default="", help_text="采样器简化序列, 默认 edskypmxt"),
    ParamDef("ignore_eos", "--ignore-eos", label="忽略EOS", group="Sampling", type="bool", default=False, help_text="忽略结束符继续生成 (等价 --logit-bias EOS-inf)"),
    ParamDef("logit_bias", "--logit-bias", "-l", label="Logit偏置", group="Sampling", type="str", default="", help_text="TOKEN_ID(+/-)BIAS, 如 15043+1"),
    ParamDef("grammar", "--grammar", label="语法约束", group="Sampling", type="str", default="", help_text="BNF 风格语法, 约束生成内容"),
    ParamDef("grammar_file", "--grammar-file", label="语法文件", group="Sampling", type="path", default="", help_text="从文件读取语法"),
    ParamDef("json_schema", "--json-schema", "-j", label="JSON Schema", group="Sampling", type="str", default="", help_text="约束生成内容为指定 JSON 结构"),
    ParamDef("json_schema_file", "--json-schema-file", "-jf", label="Schema文件", group="Sampling", type="path", default="", help_text="从文件读取 JSON Schema"),
    ParamDef("backend_sampling", "--backend-sampling", "-bs", label="后端采样", group="Sampling", type="bool", default=False, help_text="将采样卸载到后端(实验性)"),

    # ═══════════════ Reasoning 组 ═══════════════
    ParamDef("reasoning", "--reasoning", "-rea", label="推理模式", group="Reasoning", type="choice", default="auto", choices=["auto", "on", "off"], help_text="推理/思考模式"),
    ParamDef("reasoning_effort", "--reasoning-effort", label="推理力度", group="Reasoning", type="choice", default="default", choices=["default", "minimal", "low", "medium", "high", "xhigh", "max"], help_text="推理力度级别"),
    ParamDef("reasoning_budget", "--reasoning-budget", label="推理预算", group="Reasoning", type="int", default=-1, help_text="思考 token 预算 (-1=无限,0=立即结束)"),
    ParamDef("reasoning_budget_message", "--reasoning-budget-message", label="预算耗尽提示", group="Reasoning", type="str", default="", help_text="推理预算耗尽时注入的消息"),
    ParamDef("reasoning_format", "--reasoning-format", label="推理格式", group="Reasoning", type="choice", default="auto", choices=["auto", "none", "deepseek", "deepseek-legacy"], help_text="推理内容的返回格式"),
    ParamDef("reasoning_preserve", "--reasoning-preserve", negation_flag="--no-reasoning-preserve", label="保留推理轨迹", group="Reasoning", type="bool_pair", default=None, help_text="在完整历史中保留推理轨迹"),
    ParamDef("jinja", "--jinja", negation_flag="--no-jinja", label="Jinja模板", group="Reasoning", type="bool_pair", default=None, help_text="聊天是否使用 Jinja 模板引擎"),
    ParamDef("chat_template", "--chat-template", label="聊天模板", group="Reasoning", type="choice", default="", choices=_CHAT_TEMPLATES, help_text="自定义 Jinja 聊天模板"),
    ParamDef("chat_template_file", "--chat-template-file", label="聊天模板文件", group="Reasoning", type="path", default="", help_text="从文件读取 Jinja 聊天模板"),
    ParamDef("chat_template_kwargs", "--chat-template-kwargs", label="模板参数", group="Reasoning", type="str", default="", help_text="JSON 对象字符串, 传给模板解析器"),
    ParamDef("skip_chat_parsing", "--skip-chat-parsing", negation_flag="--no-skip-chat-parsing", label="跳过聊天解析", group="Reasoning", type="bool_pair", default=None, help_text="强制纯内容解析器, 不解析思考/工具调用"),
    ParamDef("prefill_assistant", "--prefill-assistant", negation_flag="--no-prefill-assistant", label="预填充助手", group="Reasoning", type="bool_pair", default=None, help_text="最后一条为助手消息时是否预填充"),
    ParamDef("special", "--special", "-sp", label="特殊Token", group="Reasoning", type="bool", default=False, help_text="输出中启用特殊 token"),
    ParamDef("reverse_prompt", "--reverse-prompt", "-r", label="反向提示", group="Reasoning", type="str", default="", help_text="生成到该提示时停止并交还控制权 (交互模式)"),
    ParamDef("escape", "--escape", "-e", negation_flag="--no-escape", label="转义序列", group="Reasoning", type="bool_pair", default=None, help_text="是否处理转义序列 \\n \\r \\t 等"),

    # ═══════════════ Speculative 组 ═══════════════
    ParamDef("spec_type", "--spec-type", label="投机解码类型", group="Speculative", type="choice", default="", choices=["", "none", "draft-simple", "draft-eagle3", "draft-mtp", "draft-dflash", "draft-dspark", "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod", "ngram-cache"], help_text="投机解码类型, 逗号分隔"),
    ParamDef("spec_draft_model", "--spec-draft-model", "-md", label="草稿模型", group="Speculative", type="path", default="", help_text="投机解码用的草稿模型路径", alias="--model-draft"),
    ParamDef("spec_draft_hf", "--spec-draft-hf", "-hfd", label="草稿HF仓库", group="Speculative", type="str", default="", extra_flags=("-hfrd", "--hf-repo-draft"), help_text="同 --hf-repo, 但用于草稿模型 (默认未使用)"),
    ParamDef("spec_draft_ngl", "--spec-draft-ngl", "-ngld", label="草稿GPU层数", group="Speculative", type="str", default="auto", help_text="草稿模型 VRAM 层数 (数字/auto/all)", alias="--n-gpu-layers-draft", extra_flags=("--gpu-layers-draft",)),
    ParamDef("spec_draft_device", "--spec-draft-device", "-devd", label="草稿设备", group="Speculative", type="str", default="", help_text="草稿模型卸载设备列表 (none=不卸载, 默认跟随 --device)", alias="--device-draft"),
    ParamDef("spec_draft_n_max", "--spec-draft-n-max", label="草稿最大Token", group="Speculative", type="int", default=3, help_text="投机解码每次草稿的 token 数"),
    ParamDef("spec_draft_n_min", "--spec-draft-n-min", label="草稿最小Token", group="Speculative", type="int", default=0, help_text="投机解码最少使用的草稿 token 数"),
    ParamDef("spec_draft_n_ubatch", "--spec-draft-n-ubatch", label="草稿物理批大小", group="Speculative", type="int", default=0, help_text="草稿上下文物理最大批大小 (0=同 --n-ubatch; 调小可省显存)"),
    ParamDef("spec_draft_p_split", "--spec-draft-p-split", "--draft-p-split", label="草稿拆分概率", group="Speculative", type="float", default=0.10, help_text="投机解码拆分概率"),
    ParamDef("spec_draft_p_min", "--spec-draft-p-min", "--draft-p-min", label="草稿最小概率", group="Speculative", type="float", default=0.0, help_text="投机解码最小概率(贪心)"),
    ParamDef("spec_synth_len", "--spec-synth-len", label="合成接受长度", group="Speculative", type="str", default="", help_text="目标平均合成接受长度(含目标 token), 仅基准测试用"),
    ParamDef("spec_synth_rates", "--spec-synth-rates", label="合成接受概率", group="Speculative", type="str", default="", help_text="各位置无条件合成接受概率, 逗号分隔, 仅基准测试用"),
    ParamDef("spec_draft_threads", "--spec-draft-threads", "-td", label="草稿CPU线程", group="Speculative", type="int", default=-1, help_text="草稿模型生成线程数", alias="--threads-draft"),
    ParamDef("spec_draft_threads_batch", "--spec-draft-threads-batch", "-tbd", label="草稿批线程", group="Speculative", type="int", default=-1, help_text="草稿模型批处理线程数", alias="--threads-batch-draft"),
    ParamDef("spec_draft_cpu_mask", "--spec-draft-cpu-mask", "-Cd", label="草稿CPU掩码", group="Speculative", type="str", default="", help_text="草稿模型 CPU 亲和掩码", alias="--cpu-mask-draft"),
    ParamDef("spec_draft_cpu_range", "--spec-draft-cpu-range", "-Crd", label="草稿CPU范围", group="Speculative", type="str", default="", help_text="草稿模型 CPU 亲和范围", alias="--cpu-range-draft"),
    ParamDef("spec_draft_cpu_strict", "--spec-draft-cpu-strict", "--cpu-strict-draft", label="草稿严格绑定", group="Speculative", type="choice", default="", choices=_ZERO_ONE, help_text="草稿模型是否严格 CPU 绑定"),
    ParamDef("spec_draft_prio", "--spec-draft-prio", "--prio-draft", label="草稿线程优先级", group="Speculative", type="int", default=0, help_text="草稿模型线程优先级 0-3"),
    ParamDef("spec_draft_poll", "--spec-draft-poll", "--poll-draft", label="草稿轮询级别", group="Speculative", type="choice", default="", choices=_ZERO_ONE, help_text="草稿模型轮询级别"),
    ParamDef("spec_draft_cpu_mask_batch", "--spec-draft-cpu-mask-batch", "-Cbd", label="草稿批CPU掩码", group="Speculative", type="str", default="", help_text="草稿模型批处理 CPU 亲和掩码", alias="--cpu-mask-batch-draft"),
    ParamDef("spec_draft_cpu_strict_batch", "--spec-draft-cpu-strict-batch", "--cpu-strict-batch-draft", label="草稿批严格绑定", group="Speculative", type="choice", default="", choices=_ZERO_ONE, help_text="草稿模型批处理严格 CPU 绑定"),
    ParamDef("spec_draft_prio_batch", "--spec-draft-prio-batch", "--prio-batch-draft", label="草稿批优先级", group="Speculative", type="int", default=0, help_text="草稿模型批处理线程优先级"),
    ParamDef("spec_draft_poll_batch", "--spec-draft-poll-batch", "--poll-batch-draft", label="草稿批轮询", group="Speculative", type="choice", default="", choices=_ZERO_ONE, help_text="草稿模型批处理轮询级别"),
    ParamDef("spec_draft_override_tensor", "--spec-draft-override-tensor", "-otd", label="草稿张量缓冲", group="Speculative", type="str", default="", help_text="草稿模型张量缓冲覆盖", alias="--override-tensor-draft"),
    ParamDef("spec_draft_cpu_moe", "--spec-draft-cpu-moe", "-cmoed", label="草稿MoE留CPU", group="Speculative", type="bool", default=False, help_text="草稿模型 MoE 权重保留在 CPU", alias="--cpu-moe-draft"),
    ParamDef("spec_draft_n_cpu_moe", "--spec-draft-n-cpu-moe", "-ncmoed", label="草稿MoE层留CPU", group="Speculative", type="int", default=0, help_text="草稿模型前 N 层 MoE 保留在 CPU", alias="--n-cpu-moe-draft", extra_flags=("--spec-draft-ncmoe",)),
    ParamDef("spec_draft_backend_sampling", "--spec-draft-backend-sampling", negation_flag="--no-spec-draft-backend-sampling", label="草稿后端采样", group="Speculative", type="bool_pair", default=None, help_text="将草稿采样卸载到后端"),
    ParamDef("spec_draft_sampling", "--spec-draft-sampling", label="草稿采样方式", group="Speculative", type="choice", default="", choices=["", "greedy", "probabilistic"], help_text="草稿模型如何采样: greedy 取 argmax; probabilistic 按概率抽样, 再由目标模型拒绝采样校验 (默认 greedy)"),
    ParamDef("spec_draft_type_k", "--spec-draft-type-k", "-ctkd", label="草稿KV-K类型", group="Speculative", type="choice", default="f16", choices=_CACHE_TYPES, help_text="草稿模型 KV cache K 类型", alias="--cache-type-k-draft"),
    ParamDef("spec_draft_type_v", "--spec-draft-type-v", "-ctvd", label="草稿KV-V类型", group="Speculative", type="choice", default="f16", choices=_CACHE_TYPES, help_text="草稿模型 KV cache V 类型", alias="--cache-type-v-draft"),
    ParamDef("spec_ngram_mod_n_min", "--spec-ngram-mod-n-min", label="ngram-mod最小", group="Speculative", type="int", default=48, help_text="ngram-mod 最少使用的 ngram token 数"),
    ParamDef("spec_ngram_mod_n_max", "--spec-ngram-mod-n-max", label="ngram-mod最大", group="Speculative", type="int", default=64, help_text="ngram-mod 最多使用的 ngram token 数"),
    ParamDef("spec_ngram_mod_n_match", "--spec-ngram-mod-n-match", label="ngram-mod查找长度", group="Speculative", type="int", default=24, help_text="ngram-mod 查找长度"),
    ParamDef("spec_ngram_simple_size_n", "--spec-ngram-simple-size-n", label="ngram-simple-N", group="Speculative", type="int", default=12, help_text="ngram-simple 查找 n-gram 长度"),
    ParamDef("spec_ngram_simple_size_m", "--spec-ngram-simple-size-m", label="ngram-simple-M", group="Speculative", type="int", default=48, help_text="ngram-simple 草稿 m-gram 长度"),
    ParamDef("spec_ngram_simple_min_hits", "--spec-ngram-simple-min-hits", label="ngram-simple最小命中", group="Speculative", type="int", default=1, help_text="ngram-simple 最小命中次数"),
    ParamDef("spec_ngram_map_k_size_n", "--spec-ngram-map-k-size-n", label="ngram-map-k-N", group="Speculative", type="int", default=12, help_text="ngram-map-k 查找 n-gram 长度"),
    ParamDef("spec_ngram_map_k_size_m", "--spec-ngram-map-k-size-m", label="ngram-map-k-M", group="Speculative", type="int", default=48, help_text="ngram-map-k 草稿 m-gram 长度"),
    ParamDef("spec_ngram_map_k_min_hits", "--spec-ngram-map-k-min-hits", label="ngram-map-k最小命中", group="Speculative", type="int", default=1, help_text="ngram-map-k 最小命中次数"),
    ParamDef("spec_ngram_map_k4v_size_n", "--spec-ngram-map-k4v-size-n", label="ngram-map-k4v-N", group="Speculative", type="int", default=12, help_text="ngram-map-k4v 查找 n-gram 长度"),
    ParamDef("spec_ngram_map_k4v_size_m", "--spec-ngram-map-k4v-size-m", label="ngram-map-k4v-M", group="Speculative", type="int", default=48, help_text="ngram-map-k4v 草稿 m-gram 长度"),
    ParamDef("spec_ngram_map_k4v_min_hits", "--spec-ngram-map-k4v-min-hits", label="ngram-map-k4v最小命中", group="Speculative", type="int", default=1, help_text="ngram-map-k4v 最小命中次数"),
    ParamDef("lookup_cache_static", "--lookup-cache-static", "-lcs", label="静态查找缓存", group="Speculative", type="path", default="", help_text="用于查找解码的静态缓存文件"),
    ParamDef("lookup_cache_dynamic", "--lookup-cache-dynamic", "-lcd", label="动态查找缓存", group="Speculative", type="path", default="", help_text="用于查找解码的动态缓存文件"),
    ParamDef("spec_default", "--spec-default", label="默认投机配置", group="Speculative", type="bool", default=False, help_text="启用内置的默认投机解码配置"),

    # ═══════════════ Multimodal 组 ═══════════════
    ParamDef("mmproj", "--mmproj", "-mm", label="多模态投影", group="Multimodal", type="path", default="", help_text="多模态投影文件路径"),
    ParamDef("mmproj_url", "--mmproj-url", "-mmu", label="投影下载地址", group="Multimodal", type="str", default="", help_text="多模态投影文件的下载 URL"),
    ParamDef("mmproj_auto", "--mmproj-auto", negation_flag="--no-mmproj-auto", label="自动投影", group="Multimodal", type="bool_pair", default=None, extra_flags=("--no-mmproj",), help_text="是否自动使用投影文件 (用 -hf 时自动下载投影; --no-mmproj 为等价关闭写法)"),
    ParamDef("mmproj_offload", "--mmproj-offload", negation_flag="--no-mmproj-offload", label="投影GPU卸载", group="Multimodal", type="bool_pair", default=None, help_text="多模态投影是否启用 GPU 卸载"),
    ParamDef("mmproj_device", "--mmproj-device", "-mmdev", label="投影设备", group="Multimodal", type="str", default="", help_text="多模态投影使用的设备 (none=不卸载, 默认跟随 --device)"),
    ParamDef("image_min_tokens", "--image-min-tokens", label="图像最小Token", group="Multimodal", type="int", default=0, help_text="每张图最少 token 数 (0=从模型读取)"),
    ParamDef("image_max_tokens", "--image-max-tokens", label="图像最大Token", group="Multimodal", type="int", default=0, help_text="每张图最多 token 数 (0=从模型读取)"),
    ParamDef("mtmd_batch_max_tokens", "--mtmd-batch-max-tokens", label="图像批Token上限", group="Multimodal", type="int", default=1024, help_text="编码图像时每批最大图像 token 数"),
    ParamDef("video_fps", "--video-fps", label="视频帧率", group="Multimodal", type="float", default=4.0, help_text="目标视频帧率"),
    ParamDef("video_timestamp_interval", "--video-timestamp-interval", label="视频时间戳间隔", group="Multimodal", type="int", default=5000, help_text="文本时间戳间隔(毫秒)"),
    ParamDef("video_ffmpeg_dir", "--video-ffmpeg-dir", label="ffmpeg目录", group="Multimodal", type="path", default="", help_text="含 ffmpeg/ffprobe 的目录"),

    # ═══════════════ Logging 组 ═══════════════
    ParamDef("log_disable", "--log-disable", label="禁用日志", group="Logging", type="bool", default=False, help_text="完全禁用日志输出"),
    ParamDef("log_file", "--log-file", label="日志文件", group="Logging", type="path", default="", help_text="将日志写入文件"),
    ParamDef("log_jsonl", "--log-jsonl", negation_flag="--no-log-jsonl", label="JSONL日志", group="Logging", type="bool_pair", default=None, help_text="以 JSONL 格式输出日志(禁用彩色)"),
    ParamDef("log_colors", "--log-colors", label="彩色日志", group="Logging", type="choice", default="", choices=["", "on", "off", "auto"], help_text="彩色日志输出方式"),
    ParamDef("verbose", "--verbose", "-v", label="详细日志", group="Logging", type="bool", default=False, help_text="输出全部日志(调试用)", alias="--log-verbose"),
    ParamDef("log_verbosity", "--verbosity", "-lv", label="日志级别", group="Logging", type="int", default=3, help_text="0=通用,1=错误,2=警告,3=信息,4=跟踪,5=调试", alias="--log-verbosity"),
    ParamDef("log_prefix", "--log-prefix", negation_flag="--no-log-prefix", label="日志前缀", group="Logging", type="bool_pair", default=None, help_text="日志消息是否带前缀"),
    ParamDef("log_timestamps", "--log-timestamps", negation_flag="--no-log-timestamps", label="日志时间戳", group="Logging", type="bool_pair", default=None, help_text="日志消息是否带时间戳"),
    ParamDef("log_prompts_dir", "--log-prompts-dir", label="提示词日志目录", group="Logging", type="path", default="", help_text="将提示词记录到目录(调试用)"),
    ParamDef("offline", "--offline", label="离线模式", group="Logging", type="bool", default=False, help_text="强制使用缓存, 禁止网络访问"),

    # ═══════════════ Advanced 组 ═══════════════
    ParamDef("cache_list", "--cache-list", "-cl", label="缓存模型清单", group="Advanced", type="bool", default=False, help_text="列出本地模型缓存中的模型清单(打印后直接退出, 不启动服务)"),
    ParamDef("completion_bash", "--completion-bash", label="Bash补全脚本", group="Advanced", type="bool", default=False, help_text="输出可直接 source 的 bash 补全脚本(不启动服务)"),
    ParamDef("rope_scaling", "--rope-scaling", label="RoPE缩放", group="Advanced", type="choice", default="", choices=["", "none", "linear", "yarn"], help_text="RoPE 频率缩放方法 (未指定时模型默认 linear)"),
    ParamDef("rope_scale", "--rope-scale", label="RoPE缩放因子", group="Advanced", type="float", default=1.0, help_text="RoPE 上下文缩放因子"),
    ParamDef("rope_freq_base", "--rope-freq-base", label="RoPE基频", group="Advanced", type="float", default=0, help_text="RoPE 基础频率 (0=从模型加载)"),
    ParamDef("rope_freq_scale", "--rope-freq-scale", label="RoPE频率缩放", group="Advanced", type="float", default=0, help_text="RoPE 频率缩放因子 (0=不设置)"),
    ParamDef("yarn_orig_ctx", "--yarn-orig-ctx", label="YaRN原始上下文", group="Advanced", type="int", default=0, help_text="YaRN: 模型原始上下文大小"),
    ParamDef("yarn_ext_factor", "--yarn-ext-factor", label="YaRN外推因子", group="Advanced", type="float", default=-1.0, help_text="YaRN: 外推混合因子 (0.0=完全插值)"),
    ParamDef("yarn_attn_factor", "--yarn-attn-factor", label="YaRN注意力因子", group="Advanced", type="float", default=-1.0, help_text="YaRN: 缩放 sqrt(t) 或注意力幅度"),
    ParamDef("yarn_beta_slow", "--yarn-beta-slow", label="YaRN beta-slow", group="Advanced", type="float", default=-1.0, help_text="YaRN: 高修正维度或 alpha"),
    ParamDef("yarn_beta_fast", "--yarn-beta-fast", label="YaRN beta-fast", group="Advanced", type="float", default=-1.0, help_text="YaRN: 低修正维度或 beta"),
    ParamDef("lora", "--lora", label="LoRA适配器", group="Advanced", type="str", default="", help_text="LoRA 适配器路径, 逗号分隔多个"),
    ParamDef("lora_scaled", "--lora-scaled", label="LoRA缩放", group="Advanced", type="str", default="", help_text="FNAME:SCALE 形式, 逗号分隔多个"),
    ParamDef("lora_init_without_apply", "--lora-init-without-apply", label="LoRA仅加载", group="Advanced", type="bool", default=False, help_text="只加载 LoRA 不应用, 稍后通过 API 应用"),
    ParamDef("control_vector", "--control-vector", label="控制向量", group="Advanced", type="str", default="", help_text="控制向量文件, 逗号分隔多个"),
    ParamDef("control_vector_scaled", "--control-vector-scaled", label="控制向量缩放", group="Advanced", type="str", default="", help_text="FNAME:SCALE 形式, 逗号分隔多个"),
    ParamDef("control_vector_layer_range", "--control-vector-layer-range", label="控制向量层范围", group="Advanced", type="str", default="", help_text="START END, 含首尾"),
    ParamDef("models_dir", "--models-dir", label="路由模型目录", group="Advanced", type="path", default="", help_text="路由服务器的模型目录"),
    ParamDef("models_preset", "--models-preset", label="路由预设文件", group="Advanced", type="path", default="", help_text="路由服务器的模型预设 INI 文件"),
    ParamDef("models_max", "--models-max", label="路由最大模型数", group="Advanced", type="int", default=4, help_text="路由服务器同时加载的最大模型数 (0=不限)"),
    ParamDef("models_autoload", "--models-autoload", negation_flag="--no-models-autoload", label="路由自动加载", group="Advanced", type="bool_pair", default=None, help_text="路由服务器是否自动加载模型"),
    ParamDef("ui_mcp_proxy", "--ui-mcp-proxy", negation_flag="--no-ui-mcp-proxy", label="MCP代理", group="Advanced", type="bool_pair", default=None, extra_flags=("--webui-mcp-proxy", "--no-webui-mcp-proxy"), help_text="启用 MCP CORS 代理(实验性, 勿在不可信环境使用)"),
    ParamDef("tools", "--tools", label="内置工具", group="Advanced", type="str", default="", help_text="启用内置工具, 逗号分隔; all 表示全部 (read_file/write_file/edit_file/file_glob_search/grep_search/exec_shell_command/get_info); 出于安全默认将 CORS 限制为 localhost"),
    ParamDef("tools_runtime", "--tools-runtime", label="工具运行环境", group="Advanced", type="str", default="", help_text="docker:/podman:/ssh: 形式, 默认宿主机"),
    ParamDef("mcp_servers_config", "--mcp-servers-config", label="MCP服务配置", group="Advanced", type="path", default="", help_text="MCP 服务器定义 JSON 文件(实验性)"),
    ParamDef("mcp_servers_json", "--mcp-servers-json", label="MCP服务JSON", group="Advanced", type="str", default="", help_text="内联 MCP 服务器定义 JSON(实验性)"),
    ParamDef("agent", "--agent", "-ag", negation_flag="--no-agent", label="智能体模式", group="Advanced", type="bool_pair", default=None, extra_flags=("-no-ag",), help_text="启用 CORS 代理与全部内置工具(实验性)"),
]

# ────────────────────────── 白话解说 ──────────────────────────
# 帮助站点每张参数卡片底部的「白话解说」(键 = ParamDef.key)。
# 新增/同步参数时顺手补一条; 缺条目时 gen_help_params.py 会逐条告警。
PARAM_PLAIN: dict[str, str] = {
    "host": "服务器“住在”哪个地址。默认 127.0.0.1 = 只有这台电脑自己能连；想让局域网里别人也能访问，就改成 0.0.0.0。",
    "port": "服务器的“门牌号”（端口）。默认 8080，一般不用改；只有端口被占用时才需要换一个。",
    "api_key": "给服务器“上锁”的密码。填了之后，任何人访问 API 都必须带上这个密码，防止陌生人乱用。多个用逗号隔开。",
    "api_key_file": "把上面的密码写进一个文件里，比直接写在命令行更安全、更好管理。每行一个，用 # 开头的行不算数。",
    "timeout": "服务器等对方多久没动静就算“断开”。默认 3600 秒（1 小时），一般不用动。",
    "alias": "给模型起个好认的“外号”。留空就直接显示文件名；如果不想把本地文件路径暴露给别人，就填个别名。",
    "tags": "给模型贴几个“标签”方便辨认，只是展示用，不影响任何功能。",
    "threads_http": "用几个“人手”同时接待网页请求。默认 -1 = 自动判断，一般不用管。",
    "reuse_port": "允许两个程序抢同一个“门牌号”（端口）。一般默认关闭就行。",
    "sse_ping_interval": "服务器每隔几秒给网页发个“我还活着”的信号，防止长时间没动静连接被断开。-1 = 关掉这个功能。",
    "api_prefix": "给 API 地址前面统一加一段路径前缀，用于区分或路由。一般留空。",
    "path": "指定一个文件夹，服务器顺便把这个文件夹里的网页/文件也对外开放。",
    "cors_origins": "允许哪些“别的网站”来调用你的服务器。填 localhost = 只放行本机网页，更安全。",
    "cors_methods": "允许别的网页用哪些方式请求（比如 GET 读取、POST 写入）。按实际需要填。",
    "cors_headers": "允许别的网页额外携带哪些信息（请求头）。一般用默认即可。",
    "cors_credentials": "要不要允许别的网页带上你的登录凭证（Cookie）来访问。涉及敏感信息，默认关更安全。",
    "ssl_key_file": "HTTPS 加密用的“钥匙”（私钥）文件。想用 https:// 访问时才需要填。",
    "ssl_cert_file": "HTTPS 加密用的“证书”文件，和上面的私钥配套使用。想加密传输才需要。",
    "ui": "要不要打开自带的网页聊天界面。开着就有现成的网页窗口可以用，默认开着即可。",
    "ui_config": "用一段 JSON 预设网页界面的显示/行为。一般不用填。",
    "ui_config_file": "把上面网页界面的预设设置存成一个文件，启动时自动读取。",
    "metrics": "打开一个“体检报告”接口，供监控软件读取服务器运行状态。用不到就保持关闭。",
    "props": "允许别人通过 POST 接口临时修改服务器的全局属性。默认关闭更安全。",
    "slots": "要不要开放查看“槽位”占用情况的接口。一般默认即可。",
    "slot_save_path": "指定一个文件夹，把“槽位”的中间缓存保存到里面，重启后可以复用。",
    "media_path": "指定一个文件夹放本地图片/文件，网页里可以用相对路径引用它们。",
    "connect": "实验功能：建一个点对点隧道，让外网也能访问你的服务器（穿透内网）。一般用不到。",
    "connect_code": "连接隧道用的配对码。通常自动生成，一般不用手动填。",
    "embedding": "只让模型做“文本转向量”（嵌入），不用于聊天，供其他程序检索使用。",
    "reranking": "开启“重排序”接口，帮搜索引擎把更相关的结果排到前面。",
    "pooling": "选一种方法，把一整段文字的向量“浓缩”成一个代表值。做检索才会用到，按需选。",
    "embd_normalize": "要不要把向量“拉齐到同一长度”方便互相比较。用默认 2 就行。",
    "sleep_idle_seconds": "长时间没人用时让服务器“睡觉”省资源。-1 = 永不睡觉。一般默认。",
    "slot_prompt_similarity": "新来的问题和缓存里旧问题有多像，就直接复用旧结果，省去重复计算。0 = 不复用。",
    "model": "指定加载哪个模型文件。程序里会自动帮你填好，一般不用手写。",
    "model_url": "直接填一个网址，让程序从网上下载模型再加载。默认不用。",
    "docker_repo": "从 Docker 仓库里拉取模型。用 Docker 部署的人才需要填。",
    "hf_repo": "填 Hugging Face 上的模型地址（格式：用户名/模型名），程序自动下载。默认量化版本是 Q4_K_M。",
    "hf_file": "指定 HF 仓库里具体用哪一个文件，用来覆盖上面默认的量化版本。",
    "hf_token": "下载需要登录的私有模型时，填上你的 HF 令牌（相当于密码）。",
    "ctx_size": "模型最多能“记住”多长的文字（上下文）。越长记性越好但越占显存。0 = 用模型自带的默认值。",
    "n_gpu_layers": "把模型多少“层”放到显卡上算。放得越多越快但越占显存；auto/all = 能放多少放多少。",
    "predict": "一次最多让模型生成多长内容。-1 = 不限长度，一直写到自然结束。",
    "keep": "保留对话开头多少字不丢（用于长对话裁剪）。-1 = 全部保留。",
    "batch_size": "每批一次性处理多少内容，影响速度和显存。默认 2048，一般不用动。",
    "ubatch_size": "内部真正分批处理的大小。调小能省显存，调大可能更快。一般默认。",
    "parallel": "同时能接待几个“客人”（请求）。越多越占显存，一个人用填 1 就够。",
    "swa_full": "是否用完整尺寸的缓存（更稳但更占显存）。一般默认即可。",
    "split_mode": "多张显卡时怎么分配活儿。layer = 按层分，row = 按行分。默认自动。",
    "tensor_split": "多卡时各张卡分担多少比例。比如“3,1”表示一张分 3 份、另一张分 1 份。",
    "main_gpu": "指定用第几张显卡当主力（从 0 开始数）。一般默认 0。",
    "device": "指定把模型放到哪些设备（显卡/CPU）。none = 全用 CPU。一般不用动。",
    "rpc": "借用其他电脑的显卡来算（远程计算）。格式：地址:端口。一般用不到。",
    "fit": "让程序自动算好参数，尽量让你的显卡装得下模型。默认开启，小白友好。",
    "fit_target": "自动适配时，给每张显卡留多少空闲显存当“安全余量”。默认 1024。",
    "fit_ctx": "自动省显存时，上下文长度最少保底多少。默认 4096。",
    "cpu_moe": "让 MoE 模型（混合专家）的大部分权重待在内存里、不上显卡，从而在小显存上也能跑大模型。",
    "n_cpu_moe": "把 MoE 模型前 N 层的专家权重放内存（不上显卡），进一步省显存。",
    "moe_cache_mib": "允许把多少专家权重常驻在显卡缓存里，在速度和显存之间取平衡。0 = 不常驻。",
    "n_cpu_ffn": "把普通模型前 N 层的计算层放内存来省显存（只对非 MoE 模型生效）。",
    "override_tensor": "高级微调：手动指定某些层放在哪里。不懂别碰。",
    "check_tensors": "加载时检查模型数据有没有异常/损坏，发现问题会报警。默认关闭。",
    "override_kv": "高级项：手动覆盖模型里的参数信息。不懂别动。",
    "preset_embd_gemma": "一键启用 Google 的 EmbeddingGemma 做嵌入，会自动联网下载权重。",
    "preset_fim_qwen_1_5b": "一键启用 Qwen2.5 代码模型（1.5B），会自动联网下载权重。",
    "preset_fim_qwen_3b": "一键启用 Qwen2.5 代码模型（3B），会自动联网下载权重。",
    "preset_fim_qwen_7b": "一键启用 Qwen2.5 代码模型（7B），会自动联网下载权重。",
    "preset_fim_qwen_7b_spec": "用 Qwen 7B 代码模型 + 一个很小的草稿模型来加速生成，会自动下载权重。",
    "preset_fim_qwen_14b_spec": "用 Qwen 14B 代码模型 + 一个小草稿模型加速生成，会自动下载权重。",
    "preset_fim_qwen_30b": "一键启用 Qwen3 代码模型（30B），会自动联网下载权重。",
    "preset_gpt_oss_20b": "一键启用 gpt-oss-20b 模型，会自动联网下载权重。",
    "preset_gpt_oss_120b": "一键启用 gpt-oss-120b 模型，会自动联网下载权重。",
    "preset_vision_gemma_4b": "一键启用 Gemma3 4B 模型（支持看图），会自动联网下载权重。",
    "preset_vision_gemma_12b": "一键启用 Gemma3 12B 模型（支持看图），会自动联网下载权重。",
    "threads": "用几个 CPU 核心来干活（生成时）。-1 = 自动。一般不用动。",
    "threads_batch": "处理大批量数据时用几个 CPU 核心。-1 = 自动。",
    "cpu_mask": "高级项：精确指定用哪几个具体的 CPU 核心（十六进制）。一般不用碰。",
    "cpu_range": "高级项：指定用第几个到第几个 CPU 核心。一般不用碰。",
    "cpu_strict": "是否强制线程只能锁在指定的核心上。一般默认即可。",
    "prio": "给计算线程设优先级。0 = 正常，越高越优先。一般不用改。",
    "poll": "高级项：线程忙等检查任务的频率。一般默认。",
    "cpu_mask_batch": "高级项：批量处理时指定用哪些核心。一般不用碰。",
    "cpu_range_batch": "高级项：批量处理时用第几到第几个核心。一般不用碰。",
    "cpu_strict_batch": "批量处理时是否强制锁核心。一般默认。",
    "prio_batch": "批量处理线程的优先级（0-3）。一般默认。",
    "poll_batch": "高级项：批量处理时的轮询频率。一般默认。",
    "flash_attn": "一种又省显存又快的算法。默认 auto（能开就开）；老显卡（GTX 10 系等）不支持才需要关。",
    "cache_type_k": "缓存 K 用什么精度存储。f16 最准最占显存；q8_0 省一半显存且质量几乎无损，长上下文强烈推荐。",
    "cache_type_v": "缓存 V 用什么精度，和上面的 K 一样。想省显存就选 q8_0。",
    "load_mode": "模型怎么加载进内存。auto 自动选；mlock 把模型锁在内存里防被挤走（更稳定）。",
    "lazy_mode": "按需读取模型（用到哪块才读哪块），能省内存，需要 mmap 支持。",
    "numa": "高级项：多 CPU 插槽机器上的优化策略。单机一般不用。",
    "kv_offload": "要不要把 KV 缓存放到显卡。放显卡更快但更占显存，关掉则省显存但更慢。",
    "op_offload": "是否把部分运算挪到显卡执行，一般能提速。默认即可。",
    "repack": "是否把模型权重重新打包以提速/省显存。一般默认开启。",
    "no_host": "高级项：跳过一块中转内存来省显存，可能影响兼容性。一般不用。",
    "context_shift": "对话太长放不下时，自动把最早的内容“挤掉”，好让长对话能一直继续下去。",
    "cont_batching": "把多个请求动态拼在一起算，效率更高（多人/多任务时）。一般默认开启。",
    "warmup": "启动时先空跑一次，让计算“热身”，测速更准。默认开启。",
    "cache_prompt": "把算过的开头内容存起来，下次同样开头就直接用，能省不少时间。默认开启。",
    "cache_reuse": "高级项：允许把缓存里的内容“平移”复用。0 = 关闭。",
    "cache_ram": "提示缓存最多用多少内存。-1 = 不限，0 = 禁用。",
    "cache_idle_slots": "空闲时把当前状态存到缓存，新任务来了能更快进入状态（需先开启 --cache-ram）。",
    "kv_unified": "让多个请求共用一块缓存，更省显存。一般默认开启。",
    "kv_unified_per_slot": "限制每个槽位最多用多少上下文，防止某个请求吃光显存。0 = 不限。",
    "ctx_checkpoints": "每个槽位最多保存几个“快照”（检查点），用于长对话回退。",
    "checkpoint_min_step": "每隔多少字存一次检查点。0 = 不限，间隔越小存得越频繁。",
    "spm_infill": "高级项：代码补全时用 SPM 的填充方式。一般不用。",
    "perf": "记录每一步花了多少时间，方便排查哪里慢。用不上就关掉。",
    "seed": "给随机性加个“固定值”。填同一个数，两次生成结果就一样；-1 = 每次都不一样。",
    "temperature": "控制模型的“放飞程度”。越高越有创意但越容易跑偏，越低越稳但越死板。写代码 0.2~0.6，写小说 0.8~1.1。",
    "top_k": "只允许从最可能的 K 个字里选下一个字。0 = 关闭，40 是常用值。",
    "top_p": "把可能的下一个字按概率加起来，只从前 95% 里面选。1.0 = 关闭。",
    "min_p": "把概率太低的“胡话”候选直接剔除，能减少乱写。0 = 关闭。",
    "top_nsigma": "高级采样：只留概率与最高值相差不大的候选。-1 = 关闭。",
    "typical_p": "选词时偏向“看起来正常”的候选，减少突兀。-1.0 = 关闭。",
    "xtc_probability": "高级项：控制一种“故意不选最火的词”的玩法概率。0 = 关闭。",
    "xtc_threshold": "高级项：两个最强候选差距多大才算“太明显”而触发特殊处理。1.0 = 关闭。",
    "repeat_penalty": "对刚说过的词降权，避免车轱辘话。1.0 = 不惩罚（默认）。",
    "repeat_last_n": "往前数多少字来检查是否重复。0 = 不检查。",
    "presence_penalty": "只要某个词出现过，就降低它再出现的几率（避免反复讲同一个话题）。0 = 关。",
    "frequency_penalty": "某个词出现得越频繁就越压着它别再出现，减少啰嗦。0 = 关。",
    "dry_multiplier": "一种专门治“AI 打官腔、复读机”的力度。0 = 关闭，越大越爱说人话。",
    "dry_base": "DRY 惩罚强度的“基数”，配合上面使用。默认 1.75。",
    "dry_allowed_length": "允许连续重复多长的内容不惩罚（太短会误伤正常句子）。默认 2。",
    "dry_penalty_last_n": "DRY 向前数多少字来检查重复。0 = 不查。",
    "dry_sequence_breaker": "指定一些字符，一旦重复到这就强制打断。none = 不用。",
    "adaptive_target": "高级项：自适应采样想要的目标概率。负数 = 关闭。",
    "adaptive_decay": "高级项：目标概率随时间“衰减”的速度（0~0.99）。",
    "dynatemp_range": "让温度在一个区间内自动浮动（不固定）。0 = 关闭。",
    "dynatemp_exp": "控制动态温度变化的“形状”。一般默认 1.0。",
    "mirostat": "一种能自动调温度、让输出一直有信息量的算法。0 = 不用，1 或 2 选一种。",
    "mirostat_lr": "Mirostat 自动调整的快慢（学习率）。默认 0.1。",
    "mirostat_ent": "Mirostat 想达到的“信息丰富程度”目标值。默认 5。",
    "samplers": "高级项：用哪些采样器、按什么顺序处理。一般留空用默认。",
    "sampler_seq": "高级项：用一串字母缩写指定采样顺序。默认 edskypmxt。",
    "ignore_eos": "让模型无视“该结束了”的信号，一直往下写。默认关闭。",
    "logit_bias": "手动给某些字词“加分”（更爱选）或“减分”（不爱选）。例如 15043+1。",
    "grammar": "强制模型输出符合某种格式（如 JSON、特定结构），格式不对就重来。",
    "grammar_file": "把上面的格式规则写进文件再引用，方便复用。",
    "json_schema": "强制模型输出的 JSON 严格符合你定义的字段结构，方便程序解析。",
    "json_schema_file": "把 JSON 结构定义存成文件再引用。",
    "backend_sampling": "实验功能：把选词计算交给显卡后端做。一般默认关闭。",
    "reasoning": "让模型回答前先“过一遍脑子”（深度思考）。auto = 看模型支不支持自动开启。",
    "reasoning_effort": "思考要用多大力气：minimal = 随便想想，high = 使劲想。默认 default。",
    "reasoning_budget": "限制“思考”最多花多少字。-1 = 不限，0 = 不思考直接答。",
    "reasoning_budget_message": "思考超预算时，自动给模型塞一句提示（比如“请直接回答”）。",
    "reasoning_format": "思考过程以什么格式返回给程序。auto 自动，一般用默认。",
    "reasoning_preserve": "要不要把“思考过程”也存进对话历史里。默认随设置。",
    "jinja": "用不用 Jinja 模板来组织聊天消息格式。一般默认开启。",
    "chat_template": "选聊天消息的“组装格式”，不同模型要配不同模板，一般会自动选对。",
    "chat_template_file": "用自己写的模板文件，给特殊需求用。",
    "chat_template_kwargs": "给模板传一些额外参数（JSON）。一般不用。",
    "skip_chat_parsing": "把模型的输出全部当普通文本，不解析“思考”和“工具调用”。某些场景更稳定。",
    "prefill_assistant": "提前替助手写个开头，引导它往你想要的方向说。",
    "special": "输出时要不要保留特殊标记符号。一般默认关闭。",
    "reverse_prompt": "让模型生成到某个词就停下来（用于多人对话切换角色）。交互模式用。",
    "escape": "要不要把 \\n（换行）、\\t（空格）这类转义字符当真处理。一般默认。",
    "spec_type": "选择加速方式（投机解码类型）。不懂就用默认，能明显提速。",
    "spec_draft_model": "指定一个又小又快的“打草稿”模型，配合大模型一起加速生成。",
    "spec_draft_hf": "从 Hugging Face 下载草稿模型，用法同 --hf-repo。",
    "spec_draft_ngl": "草稿模型放多少层到显卡。auto = 自动。",
    "spec_draft_device": "指定草稿模型放在显卡还是内存，默认跟主模型。",
    "spec_draft_n_max": "小草稿模型一次最多“预写”几个字。默认 3。",
    "spec_draft_n_min": "草稿最少预写几个字。默认 0。",
    "spec_draft_n_ubatch": "草稿模型的处理批大小，调小能省显存。",
    "spec_draft_p_split": "高级项：草稿拆分概率。默认 0.1。",
    "spec_draft_p_min": "高级项：草稿的最低概率（贪心）。默认 0。",
    "spec_synth_len": "仅做测试用的目标接受长度，平时不用填。",
    "spec_synth_rates": "仅做测试用的接受概率，平时不用填。",
    "spec_draft_threads": "草稿模型用几个 CPU 核心干活。-1 = 自动。",
    "spec_draft_threads_batch": "草稿模型批量处理时用几个核心。-1 = 自动。",
    "spec_draft_cpu_mask": "高级项：草稿用哪几个具体的核心。一般不用碰。",
    "spec_draft_cpu_range": "高级项：草稿用第几到第几个核心。一般不用碰。",
    "spec_draft_cpu_strict": "草稿线程是否强制锁在指定核心。一般默认。",
    "spec_draft_prio": "草稿线程的优先级（0-3）。一般默认。",
    "spec_draft_poll": "高级项：草稿等待任务的轮询频率。一般默认。",
    "spec_draft_cpu_mask_batch": "高级项：草稿批量处理用哪些核心。一般不用碰。",
    "spec_draft_cpu_strict_batch": "草稿批量处理时是否强制锁核心。一般默认。",
    "spec_draft_prio_batch": "草稿批量处理的优先级。一般默认。",
    "spec_draft_poll_batch": "高级项：草稿批量处理的轮询频率。一般默认。",
    "spec_draft_override_tensor": "高级项：手动指定草稿模型某层放哪里。一般不用碰。",
    "spec_draft_cpu_moe": "把草稿模型的 MoE 权重放内存，省显存。",
    "spec_draft_n_cpu_moe": "把草稿模型前 N 层的 MoE 权重放内存，省显存。",
    "spec_draft_backend_sampling": "把草稿模型的选词计算交给后端。一般默认。",
    "spec_draft_sampling": "草稿模型怎么选词：greedy = 总选最可能的（稳），probabilistic = 带随机（更活）。",
    "spec_draft_type_k": "草稿模型的缓存 K 用什么精度。想省显存就选 q8_0。",
    "spec_draft_type_v": "草稿模型的缓存 V 用什么精度。想省显存就选 q8_0。",
    "spec_ngram_mod_n_min": "高级项：ngram 预测最少用几个历史字。",
    "spec_ngram_mod_n_max": "高级项：ngram 预测最多用几个历史字。",
    "spec_ngram_mod_n_match": "高级项：去历史里找重复片段时看的长度。",
    "spec_ngram_simple_size_n": "高级项：用 N 个连续字去历史里找重复。",
    "spec_ngram_simple_size_m": "高级项：找到后一口气预写 M 个字。",
    "spec_ngram_simple_min_hits": "高级项：同一段内容至少出现过几次才敢拿来预测。",
    "spec_ngram_map_k_size_n": "高级项：用 N 个连续字查找重复（map-k 变体）。",
    "spec_ngram_map_k_size_m": "高级项：找到后预写 M 个字（map-k 变体）。",
    "spec_ngram_map_k_min_hits": "高级项：至少命中几次才用（map-k 变体）。",
    "spec_ngram_map_k4v_size_n": "高级项：用 N 个连续字查找重复（map-k4v 变体）。",
    "spec_ngram_map_k4v_size_m": "高级项：找到后预写 M 个字（map-k4v 变体）。",
    "spec_ngram_map_k4v_min_hits": "高级项：至少命中几次才用（map-k4v 变体）。",
    "lookup_cache_static": "高级项：用一个预生成的查找缓存文件加速。",
    "lookup_cache_dynamic": "高级项：用一个动态维护的查找缓存加速。",
    "spec_default": "直接启用内置的推荐投机配置，一键加速，省心。",
    "mmproj": "让文字模型“看得懂图”的转接头文件。加载视觉模型时才用。",
    "mmproj_url": "直接填网址，让程序下载上面那个视觉转接头。",
    "mmproj_auto": "是否自动下载配套的视觉转接头。默认自动，--no-mmproj 可关闭。",
    "mmproj_offload": "是否把视觉转接头放到显卡（更快但更占显存）。",
    "mmproj_device": "指定转接头放显卡还是内存，默认跟主模型。",
    "image_min_tokens": "每张图片最少换算成多少个“字”来处理。0 = 用模型默认。",
    "image_max_tokens": "每张图片最多换算成多少个字。0 = 用模型默认。",
    "mtmd_batch_max_tokens": "处理图片时每批最多算多少字，影响速度和显存。",
    "video_fps": "模型理解视频时每秒抽几帧画面。默认 4，越高看得越细但也越耗资源。",
    "video_timestamp_interval": "输出文字里的时间戳每隔几毫秒标一次。默认 5000。",
    "video_ffmpeg_dir": "指定 ffmpeg 程序所在目录（处理视频需要它）。一般已内置。",
    "log_disable": "让程序闭嘴，什么都不打印。想画面干净时用。",
    "log_file": "把运行日志存到文件里，方便排查问题。",
    "log_jsonl": "用机器能读的 JSON 格式记日志（方便程序处理，但就没彩色了）。",
    "log_colors": "日志要不要带颜色区分。auto = 自动（终端支持就显示）。",
    "verbose": "事无巨细全部打印出来，排查问题时用，平时别开。",
    "log_verbosity": "日志的详略程度：3 = 正常信息，5 = 最细（调试），0 = 最简。",
    "log_prefix": "每行日志要不要加一个前缀标记。默认随设置。",
    "log_timestamps": "每行日志要不要带时间，方便按时间排查。",
    "log_prompts_dir": "把每次收到的提问存到一个文件夹（调试用），平时别开。",
    "offline": "强制断网运行，只用本地缓存，防止偷偷联网下载。",
    "cache_list": "打印出本地缓存了哪些模型，然后直接退出（不启动服务）。",
    "completion_bash": "生成一段命令行补全脚本（方便打字），生成后退出。",
    "rope_scaling": "高级项：长上下文时位置编码的缩放方法。不懂别动，默认 linear。",
    "rope_scale": "高级项：把上下文按多少倍扩展，配合上面使用。一般不用改。",
    "rope_freq_base": "高级项：位置编码的基础频率。0 = 用模型自带。",
    "rope_freq_scale": "高级项：位置编码的频率缩放。0 = 不设置。",
    "yarn_orig_ctx": "高级项：扩展长上下文前，模型原始的上下文长度（YaRN 需要）。",
    "yarn_ext_factor": "高级项：YaRN 扩展时外推/插值的混合比例。一般默认。",
    "yarn_attn_factor": "高级项：YaRN 扩展时对注意力的缩放。一般默认。",
    "yarn_beta_slow": "高级项：YaRN 的高频修正参数。一般默认。",
    "yarn_beta_fast": "高级项：YaRN 的低频修正参数。一般默认。",
    "lora": "加载一个“小补丁”（LoRA 微调模型），改变模型风格或能力。多个用逗号隔开。",
    "lora_scaled": "加载 LoRA 并指定作用强度，例如“文件:1.0”。",
    "lora_init_without_apply": "先把 LoRA 加载进来但不生效，稍后再通过接口启用。",
    "control_vector": "加载一个控制向量（另一种轻量调教），控制模型某个方向。",
    "control_vector_scaled": "加载控制向量并指定强度，例如“文件:1.0”。",
    "control_vector_layer_range": "高级项：控制向量作用在模型的第几层到第几层。",
    "models_dir": "指定路由模式（多模型自动切换）下，模型放在哪个文件夹。",
    "models_preset": "用 INI 配置文件定义路由服务器的模型预设。",
    "models_max": "路由模式下最多同时加载几个模型。0 = 不限。",
    "models_autoload": "路由模式下是否自动把模型加载进来。默认随设置。",
    "ui_mcp_proxy": "实验功能：让网页界面通过一个代理访问外部工具。有安全风险，非可信环境别开。",
    "tools": "允许模型调用电脑工具（读写文件、搜索、执行命令等）。非常强大，默认只允许本机网页使用。",
    "tools_runtime": "让上面的工具在 Docker 等隔离环境里运行，更安全。",
    "mcp_servers_config": "用一个 JSON 文件配置外部工具服务器（实验功能）。",
    "mcp_servers_json": "直接把 MCP 服务器配置写成 JSON 填进来（实验功能）。",
    "agent": "实验功能：一键打开全部工具和代理，让模型能自己动手干活。有风险，谨慎开启。",
}

# ────────────────────────── 分组与索引 ──────────────────────────

# 分组 → 界面标签页名称(标签页顺序即此处的顺序)
PARAM_GROUP_LABELS: dict[str, str] = {
    "Server": "服务器",
    "Model": "模型",
    "Performance": "性能",
    "Sampling": "采样",
    "Reasoning": "推理",
    "Speculative": "投机",
    "Multimodal": "多模态",
    "Logging": "日志",
    "Advanced": "高级",
}

# 分组 → 该组参数(按定义顺序, 运行时可能因版本更新而变化, 由 rebuild_indexes 重建)
PARAM_GROUPS: dict[str, list[ParamDef]] = {}


def rebuild_indexes() -> None:
    """在运行时修改 PARAM_DEFINITIONS 后重建分组表。

    必须原地清空再填, 不能重新绑定: 其它模块是 `from llama_params import PARAM_GROUPS`
    按值导入的, 一旦重新绑定它们手里的还是旧字典, 新参数就不会出现在面板上。
    """
    PARAM_GROUPS.clear()
    for p in PARAM_DEFINITIONS:
        PARAM_GROUPS.setdefault(p.group, []).append(p)


rebuild_indexes()


def find_param_by_flag(flag: str) -> "ParamDef | None":
    """按 flag 查找参数定义(长名/短名/别名/负向/等价写法)。

    先做大小写敏感的精确匹配, 找不到再退回大小写不敏感——短名里存在仅大小写
    不同的写法(-c 与 -C, -cb 与 -Cb), 直接不区分大小写会把它们混到一起。
    """
    for sensitive in (True, False):
        for p in PARAM_DEFINITIONS:
            for v in (p.cli_flag, p.cli_short, p.alias, p.negation_flag, *p.extra_flags):
                if not v:
                    continue
                if (v == flag) if sensitive else (v.lower() == flag.lower()):
                    return p
    return None


# ────────────────────────── 命令行构建 ──────────────────────────

def _is_equal(a, b) -> bool:
    """宽松相等比较，处理类型差异"""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        if isinstance(a, bool) or isinstance(b, bool):
            return a == b
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) == float(b)
        return str(a) == str(b)
    except Exception:
        return False


def _format_arg(p: ParamDef, val: Any) -> list[str]:
    """将单个参数格式化为 argv 片段"""
    flag = p.cli_flag  # 优先长 flag

    if p.type == "bool":
        return [flag] if val else []

    if p.type == "bool_pair":
        if val is True:
            return [flag]
        if val is False:
            return [p.negation_flag] if p.negation_flag else [flag]
        return []  # None = 默认

    if p.type == "choice":
        if val == "" or val is None:
            return []
        return [flag, str(val)]

    if p.type in ("int", "float", "str", "path"):
        if val is None or val == "":
            return []
        return [flag, str(val)]

    return []


def build_argv(params: dict, server_cmd: str = "llama-server") -> list[str]:
    """根据参数字典构建 llama-server 命令行 argv"""
    argv = [server_cmd]

    # extra_args 先解析出来, 后面判重与追加都用同一份
    extra = str(params.get("extra_args") or "").strip()
    extra_tokens: list[str] = []
    if extra:
        try:
            extra_tokens = shlex.split(extra, posix=True)
        except ValueError:
            extra_tokens = extra.split()

    # 自动别名: 未显式设置 --alias 时, 用模型文件名代替 --model 的完整路径。
    # 否则 llama-server 会把模型路径(如 H:\llama.cpp\models\xxx.gguf)直接当作
    # API 中的模型名返回给调用方, 客户端上显示成一长串本地地址。
    if not str(params.get("alias") or "").strip() and str(params.get("model") or "").strip():
        if not any(t == "--alias" or t.startswith("--alias=") or t == "-a" for t in extra_tokens):
            stem = Path(str(params["model"])).stem
            if stem:
                params = {**params, "alias": stem}

    for p in PARAM_DEFINITIONS:
        if p.include == "never" or not p.cli_flag:
            continue

        val = params.get(p.key, p.default)

        if p.include == "non_default" and _is_equal(val, p.default):
            continue

        argv.extend(_format_arg(p, val))

    # 追加 extra_args
    if extra_tokens:
        argv.extend(extra_tokens)

    return argv


def build_command_string(params: dict, server_cmd: str = "llama-server") -> str:
    """构建可读的命令字符串"""
    argv = build_argv(params, server_cmd)
    parts = []
    for a in argv:
        if " " in a or "\t" in a:
            parts.append(f'"{a}"')
        else:
            parts.append(a)
    return " ".join(parts)


def get_default_params() -> dict:
    """获取所有参数的默认值字典"""
    return {p.key: p.default for p in PARAM_DEFINITIONS}
