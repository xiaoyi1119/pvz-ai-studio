# PVZ AI Studio

一个面向经典版《植物大战僵尸》的 Windows 本地 AI 控制与直播观测工具。它把游戏内存状态转换为结构化语义数据，让支持工具调用的大模型观察战场、选择动作，并由插件在后台执行与验证。

> English summary: A Windows-native semantic agent and live dashboard for classic Plants vs. Zombies. It reads structured game state from memory and executes validated game actions without relying on screenshot coordinates or foreground mouse control.

## 项目特点

- 独立桌面窗口，完整显示原生 4:3 游戏画面。
- 读取阳光、波次、卡片、植物、僵尸、掉落物、地形与割草机状态。
- 使用 `place_plant`、`shovel`、`fire_cob`、`select_seeds` 等语义动作控制游戏。
- 动作前进行合法性检查，动作后重新读取内存验证结果。
- 后台操作，不移动系统鼠标，不要求游戏持续获得焦点。
- DeepSeek Agent 每轮独立请求，不积累隐藏对话历史。
- API、模型、频道头像与名称均可在桌面设置中配置。
- 模型保存前可执行真实工具调用测试，避免“接口能连但模型不能控制游戏”。
- 共享 FFmpeg 捕获进程、异常尺寸检测、断流重建与单客户端流复用。
- 实时展示路线压力、割草机状态、模型延迟、APM 与 AI 决策记录。

## 与 Computer Use 方案的区别

| 对比项 | PVZ AI Studio | 通用 Computer Use |
|---|---|---|
| 状态来源 | 读取结构化游戏内存 | 分析截图或视频帧 |
| 操作方式 | 语义动作和游戏内部函数 | 鼠标、键盘和屏幕坐标 |
| 窗口焦点 | 可在后台运行 | 通常需要前台窗口 |
| 视觉误差 | 不受动画、遮挡、分辨率影响 | 可能受缩放、遮挡和识别误差影响 |
| 延迟与 Token | 状态紧凑，通常更低 | 图像输入通常更重 |
| 动作验证 | 执行后读取内存确认 | 通常需要再次看图判断 |
| 通用性 | 依赖特定游戏版本与偏移 | 可适配更多软件和游戏 |

它的核心优势不是“模拟人类看屏幕”，而是为模型提供准确、低延迟、可验证的游戏接口。相应代价是版本适配成本更高，只能支持已经验证过内存结构的经典版游戏。

## 游戏版本兼容性

连接时会读取游戏 EXE 的 PE 时间戳并选择对应内存偏移。窗口存在并不代表版本受支持；未知版本会拒绝连接，避免错误写入内存。

| 状态 | 版本 |
|---|---|
| 支持 | 原版英文 `1.0.0.1051` |
| 支持 | 英文 `1.2.0.1065` |
| 支持 | 英文年度版 `1.2.0.1073`、`1.2.0.1096` |
| 支持 | 中文年度版 `1.1.0.1056` 及 2012-06、2012-07 变体 |
| 支持 | 日文年度版 `1.1.0.1056` |
| 未适配 | 西班牙语 `1.0.4.7924`、`1.0.7.3556` |
| 未适配 | 俄语 `1.0.7.3467` |
| 未适配 | 德/西/法/意年度版 `1.2.0.1093` |
| 不支持 | 重制版、高清版、未知修改版及其他平台版本 |

主要实测目标是 `1.0.0.1051` 同内核中文版封装。其他列为“支持”的版本具有偏移表，但仍建议先备份存档并自行验证。

## 模型与 API

- **DeepSeek**：主要验证目标，支持模型发现、工具调用测试和 Agent 运行。
- **OpenAI 兼容接口**：可以填写自定义 Base URL，但模型必须支持 OpenAI 风格的函数工具调用。
- **OpenRouter**：模型列表与 Bearer 鉴权结构兼容，但当前 Agent 仍包含 DeepSeek 专属 `thinking` 参数，因此尚未承诺所有 OpenRouter 模型可用。
- 不支持工具调用或不支持强制工具调用的模型无法控制游戏。

API Key 仅写入软件目录中的本机 `.env`，前端状态接口不会回传完整密钥。请勿把自己的 `.env` 提交到 GitHub 或发送给他人。

## 快速开始

### 使用桌面发行包

1. 完整解压发行 ZIP。
2. 启动受支持的窗口版游戏。
3. 双击 `PVZ AI Studio.exe`。
4. 打开“设置”，填写 API 地址和 API Key。
5. 获取模型并执行“测试并保存”。
6. 点击“启动 Agent”；软件会自动连接游戏进程。
7. 主菜单与关卡切换仍由玩家手动完成。

本仓库不包含游戏本体。使用者必须自行准备合法的游戏副本。

### 从源码运行

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python.exe desktop.py
```

也可以双击 `start_desktop.cmd`。

### 构建 Windows 应用

```powershell
.\build_desktop.cmd
```

输出位于 `dist\PVZ AI Studio\`。应用窗口图标位于 `assets/pvz.ico`；缺少该文件时会使用 PyInstaller 默认图标。

## HTTP 接口

- `GET /api/status`：游戏连接与 Agent 状态。
- `GET /api/state`：结构化游戏状态。
- `GET /api/events`：后台事件与模型/动作日志。
- `GET /api/capture/stream`：共享 MJPEG 游戏画面。
- `POST /api/agent/start`、`POST /api/agent/stop`：启动或停止 Agent。
- `POST /api/actions`：执行单个语义动作。
- `GET/POST /api/settings`：读取安全设置或保存本机配置。

## 已知限制与风险

- 仅支持 Windows 与 32 位经典版 PvZ 进程。
- 版本偏移不匹配时不会连接。
- 主菜单、下一关和关卡选择目前由玩家手动完成。
- 原生种植回退会绕过部分游戏 UI；特殊地形、升级植物、教学规则与冷却仍需继续审计。
- 游戏更新、汉化补丁或第三方修改可能改变窗口标题、PE 时间戳与内存布局。
- 使用前建议备份存档。本项目不对存档损坏、游戏崩溃或 API 费用负责。

## 隐私与仓库边界

仓库不会包含：

- 游戏程序及资源文件；
- 用户存档；
- `.env` 与 API Key；
- 本地日志和状态快照；
- 构建缓存和个人浏览器配置。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前测试覆盖动作协议、规则、可中断等待、JPEG 尺寸解析、模型发现、模型工具调用测试、设置持久化与频道资料保存。

## 许可证与致谢

项目采用 [MIT License](LICENSE)。第三方来源与参考项目见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

Plants vs. Zombies、应用中用于识别兼容游戏的图标及相关商标归其权利人所有。本项目是非官方研究与实验工具，与 Electronic Arts、PopCap Games 无关联。
