# llama-shell — llama.cpp 模型管理器

Windows 桌面 GUI，用于管理本地 llama.cpp 模型：浏览 `.gguf` 模型、可视化调整
`llama-server` 启动参数、一键启动/停止服务、实时 GPU/RAM 监控，并通过 winget
保持 llama.cpp 本体与参数面板同步更新。

纯本地运行，无任何联网依赖（仅更新检测与帮助页访问需要网络）。

## 功能

- **模型管理**：扫描模型目录下的 `.gguf`，记住每个模型的独立参数（可一键重置为
  首次扫描时的原始值）；历史模型目录下拉切换（最多记 10 条），支持删除历史记录、
  不存在的目录自动剔除，来回切换目录不丢参数。
- **上下文预设**：内置 4k~64k 预设，可在对话框自定义（自动命名、查重、校验范围），
  预设持久化到 `config.json`。
- **参数面板**：按 `llama-server --help` 原样展示全部 250+ 个参数（9 个分组标签页），
  参数名不做翻译，中文含义与默认值由悬停提示给出；支持 int / float / 开关 /
  三态开关 / 下拉枚举 / 路径等控件。
- **命令预览**：实时生成完整启动命令，可一键复制，也支持「额外参数」自由拼接。
- **服务控制**：启动 / 停止 / 重启 `llama-server`，就绪轮询（`/health`），
  日志按模型 + 时间戳落盘，一键打开；「网页运行」直接在浏览器打开服务的 Web UI。
  运行中模型在列表中绿色高亮，选中项蓝色高亮。
- **实时监控**：nvidia-smi + psutil 后台线程采集 GPU 利用率/显存/温度与内存占用。
- **自动更新**：检测 winget 源上的新版本 → 确认 → 后台升级 → 自动重启本程序，
  当前版本号显示在标题栏；升级日志与常见错误码中文释义落盘排查。
  未检测到 llama.cpp 时启动即弹窗提醒，附可复制的项目地址。
- **参数表对账**：每次启动读一次 `llama-server --help`，与内置参数表比对增删，
  新参数自动归入「新增功能」标签页并弹报告，llama.cpp 升级后无需手改界面。
- **帮助站点**：内置单文件帮助页（使用说明 + 参数精讲 + 常见需求配方），
  可直接打开，也可部署到任意静态托管后改 `help_url` 指向线上地址。
- **多显示器适配**：悬停提示按指针所在显示器工作区定位，副屏（含负坐标区）不跑偏。

## 环境要求

- Windows 10/11
- Python 3.10+（开发与测试在 3.12）
- 依赖：`customtkinter`、`psutil`（psutil 缺失时仅 RAM 监控不可用，其余功能正常）
- llama.cpp：`winget install ggml.llamacpp`（或自行安装并保证 `llama-server` 在 PATH）

```bat
pip install customtkinter psutil
```

## 运行

双击 `启动.vbs`（无控制台窗口启动），或：

```bat
pythonw main.py
```

首次运行会在程序目录自动生成 `config.json`（界面配置）与 `models_config.json`
（各模型参数），默认模型目录为程序上一级。所有路径均支持相对写法，整个文件夹
拷贝到其他电脑后仍然可用。

## 目录结构

```
main.py            入口 + 界面装配与事件分发
app_config.py      路径常量与 config.json / models_config.json 读写
llama_params.py    参数定义表（唯一事实来源）+ 命令行构建
param_ui.py        参数行控件与参数面板
tooltip.py         通用悬停提示（全进程单例）
screens.py         Windows 多显示器几何
ui_theme.py        配色常量与原生控件样式
server_process.py  llama-server 子进程管理
updater.py         版本查询、winget 升级、参数表对账
monitor.py         GPU/RAM 监控线程
help/index.html    静态帮助站点（单文件，可部署到 EdgeOne Pages 等）
tools/             开发期脚本，不参与运行时
                     gen_help_params.py  生成 help 站点的「全参数参考」段落
                     debug_winget.py     winget 版本识别逐字段诊断
```

## 说明

- 参数名与当前版本 `llama-server --help` 保持一致，这是刻意设计（见
  `llama_params.py` 文件头注释）；改参数只改 `PARAM_DEFINITIONS` 一处，
  界面与命令预览随之更新。
- 所有 winget 调用显式限定 `--source winget`，规避 msstore 源的证书校验失败。
- 手动 `winget upgrade` 不经过本程序更新流程也不会脱节：重启程序后启动对账
  会自动报告参数表增删。

## License

[MIT](LICENSE)
