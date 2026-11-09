# llama-shell — llama.cpp 模型管理器

Windows 桌面 GUI，用于管理本地 llama.cpp 模型：浏览 `.gguf` 模型、可视化调整
`llama-server` 启动参数、一键启动/停止服务、实时 GPU/RAM 监控，并直接从
GitHub 官方 release 更新 llama.cpp 本体（按本机显卡选后端），保持参数面板同步。

纯本地运行，无任何联网依赖（仅更新检测与帮助页访问需要网络）。

## 功能

- **模型管理**：扫描模型目录下的 `.gguf`，记住每个模型的独立参数（可一键重置为
  首次扫描时的原始值）；历史模型目录下拉切换（最多记 10 条），支持删除历史记录、
  不存在的目录自动剔除，来回切换目录不丢参数。
- **上下文预设**：内置 4k~64k 预设，可在对话框自定义（自动命名、查重、校验范围），
  预设持久化到 `config.json`。
- **参数面板**：按 `llama-server --help` 原样展示全部 250+ 个参数（9 个分组标签页），
  参数名不做翻译，中文含义与默认值由悬停提示给出；支持 int / float / 开关 /
  三态开关 / 下拉枚举 / 路径等控件。路径类参数（`--api-key-file`、`--mmproj`、
  `--models-dir` 等 22 个）带「…」浏览按钮：文件参数开文件对话框、目录参数开
  文件夹对话框、`--lora` / `--control-vector` 支持多选后按逗号追加去重。
- **命令预览**：实时生成完整启动命令，可一键复制，也支持「额外参数」自由拼接。
- **服务控制**：启动 / 停止 / 重启 `llama-server`，就绪轮询（`/health`），
  日志按模型 + 时间戳落盘，一键打开；「网页运行」直接在浏览器打开服务的 Web UI。
  运行中模型在列表中绿色高亮，选中项蓝色高亮。
  **重启＝先卸载再加载**：确认旧进程退出、监听端口释放后才拉起新实例；
  卸载失败（进程杀不掉）会取消重启并提示 PID。
- **退出保护**：有模型在运行时关闭窗口会先弹确认框，说明「关闭即卸载模型」；
  取消则保持运行，确认后才结束进程（结束失败也会提示 PID）。
- **实时监控**：nvidia-smi + psutil 后台线程采集 GPU 利用率/显存/温度与内存占用。
- **自动更新**：比对 GitHub release 的 build 号 → 确认 → 后台下载（断点续传 +
  sha256 校验）→ 就地替换（旧文件留备份，失败自动回滚）→ 自动重启本程序，
  当前版本号显示在标题栏；有模型在跑时只下载不替换，下次启动自动生效。
  全程不调用 winget、不生成 PowerShell 脚本。
  下载前会核两件事：**目标包的后端必须与已装的后端同档**（cuda 12.4 的机器只会
  下 cuda 12.4，上游漏发时明确告知退档而不是静默降级），**目标目录必须可写且放得下**。
  未检测到 llama.cpp 时启动即弹窗，可一键下载安装（后端按显卡自动适配）。
- **换机自适应**：`llama-server` 的位置每次启动按现场重新解析（配置记录 → 程序目录旁
  → 运行中的进程 → PATH → WinGet → 用户目录 → 各固定盘根），找到就静默写回配置，
  所以把程序目录拷贝到别的机器、或对面机器是手动解压的，更新照样能用；覆盖别人的
  目录前会先确认一次归属（本程序装的静默接管，野生的问一次，WinGet 管的不碰）。
- **参数表对账**：每次启动读一次 `llama-server --help`，与内置参数表比对增删，
  新参数自动归入「新增功能」标签页并弹报告，llama.cpp 升级后无需手改界面。
- **帮助站点**：内置单文件帮助中心（白话版使用说明 + 参数速查、每张卡片附一句
  「白话解说」+ 常见需求配方），可直接打开，也可部署到任意静态托管后改
  `help_url` 指向线上地址。
- **双主题**：深色 / 浅色两套配色（与 Mo-Shen 启动器同一套色板方案，语义 token 一一对应），
  标题栏最右侧图标一键切换（☾ 切深色 / ☀ 切浅色）并记进 `config.json`；
  标题栏深浅跟随系统 API，切主题即时生效。
- **多显示器适配**：悬停提示按指针所在显示器工作区定位，副屏（含负坐标区）不跑偏。

## 环境要求

- Windows 10/11
- Python 3.10+（开发与测试在 3.12）
- 依赖：`ttkbootstrap`、`psutil`（psutil 缺失时仅 RAM 监控不可用，其余功能正常）
- llama.cpp：无需预装 —— 首次启动会弹安装窗，从官方 release 下载并按显卡自动选择
  CUDA / Vulkan / ROCm / SYCL / CPU 后端；也认自己在 PATH 或常见位置装的一份
  （首次更新时会确认一次是否接管，确认后按目录记住，之后不再追问）

```bat
pip install -r requirements.txt
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
llama_params.py    参数定义表（唯一事实来源）+ 白话解说 + 命令行构建
param_ui.py        参数行控件与参数面板
tooltip.py         通用悬停提示（全进程单例）
screens.py         Windows 多显示器几何
ui_theme.py        ttkbootstrap 主题封装（换肤 + 自定义样式 + 字体）
server_process.py  llama-server 子进程管理
updater.py         本地版本查询、build 号比对、参数表对账
llama_release.py   GitHub release 查询 / 下载 / 校验 / 就地替换
llama_install.py   按本机显卡挑后端、安装目录预检
llama_locate.py    运行时定位已装的 llama-server（换机/副本场景）+ 目录归属判定
boot_check.py      启动期依赖自检与一键 pip 安装（仅标准库）
monitor.py         GPU/RAM 监控线程
help/index.html    静态帮助站点（单文件，可部署到 EdgeOne Pages 等）
tools/             开发期脚本，不参与运行时
                     gen_help_params.py  生成 help 站点的参数速查（含白话解说，缺条告警）
                     install_selftest.py 安装/更新链路自检（替换、回滚、pending）
                     theme_selftest.py   30 套主题换肤回归
                     boot_selftest.py    依赖探测与降级窗
```

## 说明

- 参数名与当前版本 `llama-server --help` 保持一致，这是刻意设计（见
  `llama_params.py` 文件头注释）；改参数只改 `PARAM_DEFINITIONS` 一处，
  界面与命令预览随之更新。
- 帮助站点**已冻结**（2026-11-01 起暂停更新）：`help/index.html` 保持现状，参数表增删时
  不再重跑生成脚本；白话文案仍留在 `llama_params.PARAM_PLAIN`，需要恢复时再跑
  `python tools/gen_help_params.py` 即可。
- 界面配色只有一个来源：所选 ttkbootstrap 主题的色板（`ui_theme.py` 的
  `ut.color("bg")` 这类取色函数）。业务代码里不写十六进制色值；
  换肤＝`style.theme_use(name)`，ttk 控件自动跟随，需要手工同步的只有三处
  （Windows 标题栏、`Text` 的 tag、悬停提示的独立 Toplevel）。
- 加主题＝在主题面板里多放一组预览块（30 套预设已覆盖浅/深两态），界面代码不改。
- 更新只接管本程序安装目录里的副本（`llama_server_cmd` 指向的可执行文件所在目录）；
  命令只是裸名字（PATH 里别人装的）时，「更新」会明确提示无法接管。
- 有模型在跑时不会覆盖 exe/dll（Windows 拒绝写被加载的 dll），而是只下载校验并
  存为 pending，下次启动在界面出来之前落地，参数表对账自然按新版跑。

## License

[MIT](LICENSE)
