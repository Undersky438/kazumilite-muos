# KazumiLite-muOS

KazumiLite-muOS 是一个面向 muOS 掌机的非官方轻量番剧客户端，优先适配
RG35XX Pro、muOS 2601.x Jacaranda 和 640×480 屏幕。

它提供中文、手柄优先的界面，用于浏览番剧目录、搜索作品、查看分集、切换线路，
并通过掌机上的 MPV 播放外部视频源。

> 本项目是非官方社区项目，与 Predidit、Kazumi、muOS 以及所使用的数据源均无隶属或授权关系。

## 功能

- 中文热门番剧目录、收藏、历史记录和断点续播
- 拼音搜索与中文候选输入
- 稀饭和 AGE 等外部线路的搜索与详情查询
- 多线路切换，以及 HLS / MP4 播放回退
- 480p 优先的掌机播放设置
- 完整手柄操作，不依赖触摸屏或外接键盘
- 运行环境检查、启动日志和 MPV 播放日志
- 播放器退出后的画面清理，减少返回菜单时的残留画面

## 运行环境

- muOS 2601.x Jacaranda
- AArch64 掌机；当前主要在 Anbernic RG35XX Pro 上测试
- PortMaster 运行时
- 已连接 Wi-Fi

项目不会修改固件、启动分区、系统库或系统启动项，也不会安装后台服务。

## 安装

1. 从 [Releases](../../releases) 下载最新的 `.muxapp`。
2. 将安装包复制到 SD 卡的 `ARCHIVE` 文件夹。
3. 在 muOS 中打开“应用”→“归档管理器”，选择安装包。
4. 重新进入应用列表，启动 KazumiLite。

如果安装后仍显示旧版本，先退出应用并重新进入应用列表；必要时备份后删除
`MUOS/application/KazumiLite`，再重新安装。

## 手柄操作

### 主界面

| 按键 | 功能 |
| --- | --- |
| 方向键 | 移动选择 |
| A | 确认 |
| B | 返回；主界面退出 |
| Y | 打开搜索 |
| START | 刷新热门目录 |

### 搜索输入

| 按键 | 功能 |
| --- | --- |
| 方向键 | 移动软键盘光标 |
| A | 输入当前字母 |
| L1 / R1 | 选择上一个 / 下一个中文候选 |
| Y | 确认候选或输入空格 |
| X | 删除 |
| SELECT | 切换拼音输入和直接输入 |
| START | 开始搜索 |

### 详情和播放

| 按键 | 功能 |
| --- | --- |
| A | 播放或暂停 |
| B | 返回 |
| X | 收藏或取消收藏 |
| 左右 / L1 / R1 | 切换线路；播放中快退 / 快进 |
| Y | 搜索相关内容 |

## 数据和日志

程序只在自己的目录保存数据：

```text
MUOS/application/KazumiLite/data/
├─ state.json          # 收藏、历史、最近搜索和热门缓存
├─ log.txt             # 启动、按键和网络日志
├─ mpv.log             # 最近一次播放器日志
└─ diagnostics.txt     # 运行环境检查结果
```

遇到问题时，请提供 `log.txt` 和 `mpv.log`，并说明掌机型号、muOS 版本以及是否联网。

## 从源码构建

源码主要使用 Python 标准库和 PortMaster 提供的运行时依赖。Windows 下可使用：

```powershell
./build.ps1 -Version 0.2.3-r2
```

生成的安装包位于 `output/`。不要把 `data/state.json`、日志、缓存、
`__pycache__` 或个人测试文件提交到仓库。

### 源码结构

```text
KazumiLite/data/
├─ app.py          # 应用入口、页面状态和手柄事件
├─ ui.py           # SDL 界面绘制
├─ ime.py          # 拼音输入和候选栏
├─ player.py       # MPV 播放与运行环境检查
├─ sources.py      # 番剧目录、搜索和播放线路适配
├─ http_client.py  # HTTP 请求与错误处理
├─ state_store.py  # 收藏、历史和搜索记录
├─ config.py       # 共享路径、版本及界面常量
└─ backend.py      # 兼容旧导入路径的公共入口
```

不联网的基础测试可以使用：

```powershell
python -m unittest discover -s tests -v
```

## 外部服务与安全边界

本项目只提供客户端程序，不内置或分发番剧视频、小说、播放列表或付费内容。
外部数据源可能改变接口、域名或访问规则，因此搜索和播放功能不保证长期可用。
请仅访问你有权访问的内容，并遵守相关网站的服务条款和当地法律。

项目不会尝试绕过 DRM、验证码、登录限制或付费访问控制。

## 许可证

本项目代码采用 **GNU General Public License v3.0 or later**（GPL-3.0-or-later）。

字体、拼音词库、图标和其他第三方材料可能采用不同许可证，详见
`KazumiLite/licenses/` 中的版权和许可文件。第三方网站内容不属于本项目许可证范围。
