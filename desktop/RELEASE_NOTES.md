## 0.1.3 更新

- **自动备份**：每天自动备份一次资料库，旧备份自动清理；备份文件夹可以设到另一块硬盘或网盘（设置 → 备份）。
- **重算不再卡死**：某条人工决策过期时（例如换模型后实体消失），重算会跳过它并列出编号，而不是整体失败；重算前只做一次备份。
- **实体编号稳定**：重算后每个实体保持原来的编号，网址和 Claude 里引用的编号不再错位。
- **Claude 连接更稳**：应用重启后，Claude Desktop 里的 Rhizome 工具自动重连；迁移资料库后 Claude 不会再写进旧位置。
- **设置只保存你改过的项**：以后版本改进的默认值能生效；设置文件损坏时自动用备份恢复，而不是无法启动。
- **找回导出指令**：设置 → 聊天 Project 设置，可一键复制 RXF 导出指令、下载主题词表；Claude 也能通过 `rhz_rxf_guide` 自己获取。
- **召回修复**：空内容、没有 import 的脚本、Jupyter notebook 都能正常召回；长文本召回不再变慢。
- 选了安装版无法加载的模型时，每页顶部会提示并可一键改回内置模型。
- 自动化测试现在每次提交都运行，发布前必须全部通过。

## 下载 / Download

| 文件 | 说明 |
| --- | --- |
| `Rhizome_x.y.z_x64-setup.exe` | **推荐**。Windows 10/11 64 位。缺少 WebView2 时会自动下载安装 |
| `Rhizome_x.y.z_x64-offline-setup.exe` | 离线版，内置 WebView2 运行时，适合无网络的电脑 |

## 安装 / Install

1. 双击安装包。安装包**尚未代码签名**，Windows 可能提示"Windows 已保护你的电脑"：点 **更多信息 → 仍要运行**。
2. 安装位置可以任选（支持中文和空格路径），默认 `%LOCALAPPDATA%\Rhizome`，不需要管理员权限。
3. 从开始菜单打开 Rhizome。资料库默认在 `%APPDATA%\Rhizome`，可在"设置 → 存储"里迁移；卸载和升级都不会删除资料库。
4. 在"设置"里点 **接入 Claude Desktop**，重启 Claude Desktop 后即可使用 `rhz_*` 工具。

The installer is not code-signed yet: on the SmartScreen prompt choose *More info → Run anyway*.
Per-user install (no admin rights), any folder incl. non-ASCII paths. Your library stays in
`%APPDATA%\Rhizome` (movable in Settings) and survives upgrades and uninstalls.

## 本版本经过的测试 / Tested on a clean Windows runner

安装到 `C:\软件 工具\Rhizome 程序`、资料库位于中文目录 → 启动 → 后台响应 → 收件箱中文文件名论文自动摄入 →
一键写入 Claude Desktop 配置并跑通 MCP → 强制结束窗口后后台自动退出 → 卸载后资料库保留。

使用说明见 [README](https://github.com/Yiqing-dev/Rhizome#readme) 和 [desktop/README.md](https://github.com/Yiqing-dev/Rhizome/blob/claude/wizardly-gauss-gpvqje/desktop/README.md)。
