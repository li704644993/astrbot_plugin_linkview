# 🔗 LinkView - 磁力链接解析与种子搜索插件

<p align="center">
  <img src="https://img.shields.io/badge/AstrBot-Plugin-blue?style=flat-square" alt="AstrBot Plugin">
  <img src="https://img.shields.io/badge/version-v1.0.0-green?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/author-liting-orange?style=flat-square" alt="Author">
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" alt="License">
</p>

一个基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的磁力链接解析与种子搜索插件。支持自动识别磁力链接、解析文件信息、获取预览截图、关键词搜索种子等功能。

---

## ✨ 功能特性

- 🔍 **磁力链接自动解析** — 检测到 `magnet:` 链接后自动触发解析，显示文件名、大小、文件数等信息
- 🖼️ **截图预览** — 自动获取资源预览截图，直观了解内容
- 🔎 **关键词搜索** — 通过 `/种子搜索` 指令按关键词搜索种子资源并自动解析
- 📦 **合并转发** — 群聊中以合并转发卡片发送，避免刷屏（支持 OneBot v11 协议）
- 🛡️ **图片加噪** — 自动为截图添加随机像素噪点，降低被平台风控的概率
- 🎯 **群白名单** — 支持配置群组白名单，精准控制插件生效范围
- 🧹 **自动清理** — 临时图片文件用完即删，不占用磁盘空间

---

## 📦 安装

### 方式一：通过 AstrBot 插件市场安装（推荐）

在 AstrBot 管理面板中搜索 `LinkView` 或 `astrbot_plugin_linkview`，一键安装即可。

### 方式二：手动安装

将本仓库克隆到 AstrBot 的插件目录下：

```bash
cd /path/to/astrbot/data/plugins
git clone https://github.com/your/astrbot_plugin_linkview.git
```

重启 AstrBot 即可自动加载。

---

## 🚀 使用方法

### 1. 自动解析磁力链接

在聊天中直接发送磁力链接，插件会自动识别并解析：

```
magnet:?xt=urn:btih:xxxxxxxxxxxxxxxxxxxx
```

插件将返回：
- 📄 文件名
- 📦 文件总大小
- 📁 文件数量
- 🖼️ 预览截图（如有）

### 2. 关键词搜索种子

使用 `/种子搜索` 指令搜索资源：

```
/种子搜索 关键词
```

插件会搜索并自动解析第一条结果的磁力链接，返回完整的文件信息和预览截图。

---

## ⚙️ 配置说明

安装后在 AstrBot 管理面板中配置插件参数：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable` | bool | `false` | 全局开关，关闭后插件不响应任何指令和自动解析 |
| `group_whitelist` | list | `[]` | 允许使用的群号白名单，留空表示不限制 |
| `use_forward_message` | bool | `true` | 群聊中是否使用合并转发消息发送截图 |
| `forward_bot_name` | string | `"LinkView"` | 合并转发消息卡片中显示的发送者名称 |
| `forward_bot_uin` | int | `0` | 合并转发消息卡片中显示的发送者 QQ 号 |

### 配置示例

```json
{
  "enable": true,
  "group_whitelist": ["123456789", "987654321"],
  "use_forward_message": true,
  "forward_bot_name": "LinkView Bot",
  "forward_bot_uin": 1234567890
}
```

### 白名单逻辑

- `group_whitelist` 为 **空列表** `[]`：所有群聊和私聊均可使用
- `group_whitelist` 填入群号：仅白名单内的群聊可用，私聊不受限制
- `enable` 为 `false`：全局禁用，所有场景均不响应

---

## 📋 依赖

| 依赖包 | 最低版本 | 用途 |
|--------|---------|------|
| `httpx` | ≥ 0.24.0 | HTTP 异步请求 |
| `Pillow` | ≥ 10.0.0 | 图片处理与加噪 |
| `beautifulsoup4` | ≥ 4.12.0 | HTML 页面解析 |
| `lxml` | ≥ 4.9.0 | HTML 解析加速引擎 |

> 依赖会在插件安装时由 AstrBot 自动安装，无需手动操作。

---

## 🔧 工作原理

```
用户发送磁力链接 / 搜索关键词
          │
          ▼
    ┌─────────────┐
    │  权限校验    │  ← 检查 enable + 白名单
    └─────┬───────┘
          ▼
    ┌─────────────┐
    │ 链接解析 API │  ← whatslink.info API
    └─────┬───────┘
          ▼
    ┌─────────────┐
    │ 下载预览截图  │  ← 并发下载 + 图片加噪
    └─────┬───────┘
          ▼
    ┌─────────────┐
    │  消息组装    │  ← 合并转发 / 普通消息
    └─────┬───────┘
          ▼
    ┌─────────────┐
    │  发送 & 清理 │  ← 发送消息 + 删除临时文件
    └─────────────┘
```

- **链接解析**：通过 [whatslink.info](https://whatslink.info) API 获取磁力链接的文件信息和预览截图
- **种子搜索**：通过 cilisou 搜索引擎检索关键词，提取第一条结果的磁力链接
- **图片加噪**：为每张截图随机添加 5 个像素噪点，使图片 hash 唯一化
- **合并转发**：群聊场景下使用 OneBot v11 的 `send_group_forward_msg` API，将所有信息合并为一张转发卡片

---

## ⚠️ 注意事项

1. **协议支持**：合并转发功能需要 OneBot v11 协议支持（如 [NapCat](https://github.com/NapNeko/NapCatQQ)、[Lagrange](https://github.com/LagrangeDev/Lagrange.Core) 等），其他协议下建议关闭 `use_forward_message`
2. **网络要求**：插件需要访问外部 API 和网站，请确保运行环境能正常访问互联网
3. **首次使用**：安装后需在管理面板将 `enable` 设为 `true` 才能生效
4. **私聊场景**：私聊中不使用合并转发，会直接发送图片消息

---

## 📄 许可证

[MIT License](LICENSE)

---

## 🔗 相关链接

- [AstrBot 主仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档（中文）](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 插件开发文档（English）](https://docs.astrbot.app/en/dev/star/plugin-new.html)
