# RouteSift

**查看电脑与手机的连接去向，把确认过的分流规则同步到 FlClash 和 Shadowrocket。**

Local traffic analytics and shared routing rules for FlClash and Shadowrocket. Inspect per-domain bandwidth from Mihomo, monitor iOS connection logs, and reuse reviewed routing decisions across devices.

![RouteSift 的域名详情，含分时流量、策略组和规则命中情况](assets/domain-detail.png)

*截图使用合成流量。*

## 可以用 RouteSift 做什么

| 问题 | 在哪看 |
| --- | --- |
| 某个域名今天什么时候开始大量走代理？ | 打开「域名详情」，选择精确域名或含子域名，按 5 分钟、1 小时或 1 天查看上传、下载、代理和直连流量。 |
| 这段流量为什么走了这条线路？ | 在「实际分流」看命中规则、策略组和节点。流量概览还提供域名和进程排行、MATCH 兜底榜、流量频次四象限和单日突发。 |
| 哪些目标值得优先审核？ | 审核队列支持按代理流量大小或最近活动排序，也可以用 DeepSeek 生成分流建议和规则示例。 |
| 看到一个域名想设为直连，怎么加规则？ | 在「域名详情」「审核队列」或「手机日志」保存规则，RouteSift 会刷新已接入的 FlClash。 |
| 手机和电脑可以共用规则吗？ | 在「规则管理」复制 Shadowrocket 远程模块地址，添加一次后即可更新共享直连和拦截规则。 |
| 怎样核对 iPhone 上哪些域名走了代理？ | 打开「手机日志」查看 Shadowrocket 的实时诊断记录，也可导入代理日志文件。 |

## 快速开始 · Quick start

需要 **macOS 或 Linux**、**Python 3.11+** 和正在运行的 **Mihomo 内核**。

在 FlClash 打开「外部控制器」。FlClash 0.8.92 中，开关位于「工具 → 基本配置」底部，开启后监听 `127.0.0.1:9090`。其他 Mihomo 客户端在内核配置中设置本机 `external-controller`。 [FlClash 设置源码](https://github.com/chen08209/FlClash/blob/v0.8.92/lib/views/config/general.dart#L388-L433) · [Mihomo API 文档](https://wiki.metacubex.one/en/api/)

```sh
git clone https://github.com/askxiaozhang/RouteSift.git
cd RouteSift
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp -n .env.example .env
chmod 600 .env
.venv/bin/python server.py
```

打开 [本机看板](http://127.0.0.1:18765)。如果控制器设了密钥，在启动前把相同的值填进 `.env`。首次连接成功后，看板会显示「采集中」。可以从「流量概览」点击现有目标，也可在「域名详情」输入具体域名。

Enable your client's loopback controller, run the commands above, then open the dashboard. Set `MIHOMO_SECRET` in `.env` if your controller requires authentication.

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MIHOMO_URL` | `ws://127.0.0.1:9090/connections` | Mihomo 的 WebSocket 地址 |
| `MIHOMO_SECRET` | 空 | 外部控制器的 Bearer 密钥 |
| `AUDITOR_PORT` | `18765` | 本机看板和规则接口端口 |
| `AUDITOR_DB` | `data/traffic.db` | 电脑流量、手机日志和分流规则的 SQLite 路径 |
| `AUDITOR_LAN_IP` | 空 | 额外监听的本机局域网 IPv4 地址 |
| `AUDITOR_LAN_PASSWORD` | 空 | 启用局域网访问时使用的登录密码 |
| `SHADOWROCKET_MODULE_TOKEN` | 空 | 远程模块的随机令牌，至少 32 个字符。局域网访问已启用且令牌有效时生成订阅地址 |
| `SHADOWROCKET_LOG_URL` | 空 | 手机诊断页面显示的地址，例如 `http://192.168.1.50:1082/api/log` |
| `SHADOWROCKET_LOG_TZ` | `Asia/Singapore` | 实时诊断日志使用的 IANA 时区 |

## 局域网访问

在 `.env` 中将 `AUDITOR_LAN_IP` 设为运行 RouteSift 的机器的局域网 IP，并为 `AUDITOR_LAN_PASSWORD` 设置密码。重启后，同一局域网的设备可以通过 `http://<局域网IP>:18765` 打开看板，用用户名 `routesift` 和上面的密码登录。换网络导致 IP 变化时，更新该地址并重启。

在运行 FlClash 的同一台机器上启动 RouteSift。控制器和 FlClash 规则集使用 `127.0.0.1`。手机可通过局域网看板查看日志、添加规则，并从同一台机器更新 Shadowrocket 模块。

## 把审核结论加进 FlClash

在「域名详情」或「审核队列」选择目标，查看流量走势与命中规则。队列可按代理流量大小或最近活动排序，最近时间取有代理流量的 5 分钟窗口起点。规则支持精确域名、域名后缀、进程名和 IP / CIDR。

「带入审核」会预填 DeepSeek 的建议。点「保存审核」后，RouteSift 的规则集立即更新。比如保存 `static.example.com` 为精确域名直连，在 FlClash 拉取规则集后，效果等同于 `DOMAIN,static.example.com,DIRECT`。已审核目标默认从候选和代理分析中折叠，可通过「显示已审核」查看。

### 首次挂载覆写脚本

以下菜单适用于 FlClash 0.8.92。

1. 在 RouteSift「规则管理」点「下载覆写脚本」，保存 `flclash-override.js`。
2. 在 FlClash 打开「工具 → 进阶配置 → 脚本 → 添加」。粘贴脚本内容，或在编辑器菜单选择「外部获取 → 通过文件导入」，保存为 `RouteSift`。 [脚本导入入口](https://github.com/chen08209/FlClash/blob/v0.8.92/lib/pages/editor.dart#L226-L239)
3. 在 FlClash 打开「配置」，点当前订阅旁的「⋯ → 更多 → 覆写」。选择「脚本」模式，再选中 `RouteSift`，返回配置页应用。 [脚本选择入口](https://github.com/chen08209/FlClash/blob/v0.8.92/lib/views/profiles/overwrite.dart#L344-L409)

如果当前订阅已有覆写脚本，将 RouteSift 的逻辑合并到现有 `main(config)`。如果从标准模式切换，还要把原来的附加规则纳入脚本，才能随脚本模式加载。 [FlClash 配置合并逻辑](https://github.com/chen08209/FlClash/blob/v0.8.92/lib/controller.dart#L628-L671)

### 更新和撤销

保存、修改或撤销审核规则时，RouteSift 会通过本机控制器刷新当前配置已加载的 `traffic-auditor-*` 规则集。FlClash 也会每 5 分钟拉取一次。可在「代理 → 右上角 ⋯ → 提供者」手动同步，并在「规则」模式下用新连接验证。 [FlClash 同步入口](https://github.com/chen08209/FlClash/blob/v0.8.92/lib/views/proxies/providers.dart#L39-L69)

回退时，在 RouteSift「规则管理 → 审核记录」撤销对应记录。规则变更对之后建立的连接生效。旧版覆写脚本可重新下载更新，以使用 `MATCH_TARGET` 对应的规则提供者。

| 审核结论 | 生效方式 |
| --- | --- |
| `DIRECT` | 命中时直连，同步 `traffic-auditor-direct` |
| `KEEP_PROXY` | 命中时交回原配置处理，同步 `traffic-auditor-keep-proxy` |
| `MATCH_TARGET` | 使用当前配置最后一条 MATCH 规则的目标策略组，同步 `traffic-auditor-match-target` |
| `REJECT` | 命中时拦截，同步 `traffic-auditor-reject` |
| `IGNORE` | 只影响审核筛选 |

多个审核目标重叠时，MATCH_TARGET 优先，其次保留原分流、拦截、直连。每个配置的 MATCH_TARGET 使用该配置自己的兜底策略。其他 Mihomo 客户端可参考 [覆写脚本](deploy/flclash-override.js) 配置相同的 HTTP rule providers 和规则顺序。规则集使用 [Mihomo classical YAML 格式](https://wiki.metacubex.one/en/config/rule-providers/)。

## 手机和电脑共享分流规则

1. 启用局域网访问。运行 `openssl rand -hex 32` 生成令牌，把结果填入 `.env` 的 `SHADOWROCKET_MODULE_TOKEN`，然后重启 RouteSift。
2. iPhone 和 Mac 连到同一局域网，在 RouteSift「规则管理 → 接入 Shadowrocket」复制订阅地址。在 Shadowrocket「配置 → 模块 → 添加」粘贴地址并启用，首页「全局路由」选「配置」。如果此前导入过文件版模块，停用旧版后使用远程模块。
3. 后续在 RouteSift 保存规则即可更新订阅内容。FlClash 会尝试立即刷新。Shadowrocket 可在「配置 → 模块」点「更新模块」，也可在「设置 → 更新 → 模块」开启自动后台更新。手机按系统安排拉取，更新时需能访问运行中的 RouteSift。 [Shadowrocket 模块更新说明](https://github.com/LOWERTOP/Shadowrocket/wiki#自动更新)

电脑和手机共享按域名或 IP 匹配的 `DIRECT` 和 `REJECT` 规则。例如，保存域名后缀 `weixin.qq.com` 为直连，可覆盖 `mp.weixin.qq.com` 等子域名。「手机日志」的目标列表也有「添加规则」入口，保存后进入同一规则库。订阅地址只读取模块内容，看板与日志仍需登录。模块可下载为文件供离线导入。

手机专用规则可在「接入 Shadowrocket」里单独填写，按写入顺序优先于共享规则。例如，让 `dingtalk.com` 及其子域名直连，让 `gemini.google.com` 走手机当前选择的代理。

```text
DOMAIN-SUFFIX,dingtalk.com,DIRECT
DOMAIN,gemini.google.com,PROXY
```

可以在 Shadowrocket「数据 → 代理」查看新连接的走向，在「数据 → 统计」查看用量。 [Shadowrocket 功能说明](https://apps.apple.com/us/app/shadowrocket/id932747118)

## iPhone / iPad 日志

### 用 Safari 查看实时记录

1. iPhone 和 Mac 连到同一 Wi-Fi。保持 Shadowrocket 已连接，在「设置 → 诊断」开启「启用日志记录」和「允许访问」，确认「地址」显示手机的局域网 IP 和端口。 [诊断日志设置与接口示例](https://github.com/summerKK/shadowrocket-dashboard#%EF%B8%8F-shadowrocket-配置说明)
2. 在运行 RouteSift 的 Mac 上，把该地址填入 `.env` 的 `SHADOWROCKET_LOG_URL`，如 `http://192.168.1.50:1082/api/log`。`SHADOWROCKET_LOG_TZ` 用于解释手机日志时间，默认 `Asia/Singapore`。重启 RouteSift。
3. iPhone 用 Safari 打开 `http://<Mac 的 IP>:18765/#phone-logs`，按 RouteSift 的局域网账号登录。数据来源选「实时采集」，页面会自动更新。Mac 需要持续运行 RouteSift，手机更换 Wi-Fi 地址后需要更新诊断地址。

RouteSift 读取手机诊断流里的分流记录，按域名和时间统计次数。页面分别提供「实时采集」和「文件导入」两个数据来源。

### 导入手机代理日志

在 Shadowrocket「数据 → 代理」打开一份有连接记录的日志，从右上角菜单导出数据库。在 RouteSift「手机日志」上传导出的文件，并确认记录时区。页面显示各域名或 IP 的连接次数、时间趋势、最近连接时间和日志标注的直连、代理或拦截。无法识别的策略显示为「去向未知」。点列表里的目标可查看精确趋势，输入 `dingtalk.com` 并选「含子域名」可一起看子域名。

单个文件最多 32 MiB、10 万条记录。日志导出提供连接记录，页面按次数统计。手机记录保留目标域名或 IP、时间、分流去向和去重所需的记录编号。重复导入会跳过已有记录，完整 URL 与查询参数留在原日志文件中。

## DeepSeek 一键分析

在本机 `.env` 配置 `DEEPSEEK_API_KEY`，或通过 `DEEPSEEK_ENV_FILE` 引用另一个含 DeepSeek 配置的 `.env`，然后重启。默认使用 `deepseek-flash` 和 `https://api.deepseek.com`，可通过 `DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL` 修改。 [DeepSeek JSON 输出文档](https://api-docs.deepseek.com/guides/json_mode/)

在「审核队列」选择统计周期及「全部代理」或「MATCH 兜底」，点击「DeepSeek 一键分析」。RouteSift 先在本机统计，再按代理用量选出最多 24 个待审核域名发给模型。发送内容为完整域名、汇总流量与活跃天数。IP、进程名、节点名、连接明细和审核备注留在本机。

结果包含流量汇总、建议理由与试行验证方法。可以复制生成的 Mihomo 精确域名规则，也可把单条建议带入审核，确认后按上面的步骤同步 FlClash。DeepSeek 不可用时仍可看到本地流量汇总。

## 数据和采样口径

- RouteSift 从 `/connections` 读取活跃连接快照，按连接 ID 算流量增量，以 5 分钟窗口聚合到 SQLite。窗口切换、约每 15 秒或正常退出时批量落库。连接基线与流量增量同事务保存，重连或重启可以续算。
- 新观测到的连接按首帧累计值入账，可能包含采样前产生的流量。两帧之间结束的连接以及最后一帧之后的尾流量可能漏采。趋势图的灰色区间代表无采样。
- 完整域名和子域名可以分别统计。主域名归并使用离线公共后缀表。没有域名的连接按目标 IP 记录，缺失代理链的流量归入「去向未知」。
- 频次以观察到代理字节的自然日计数。突发分析对比昨天与采样覆盖相近的历史日期，至少需要 7 个可比日期。
- 看板和规则接口默认监听本机回环地址。启用局域网访问后，额外在配置的私有 IPv4 地址上提供服务。流量、审核结论和连接基线存放于本地 SQLite。

## 常驻运行

### Docker Compose

在项目根目录准备 `.env`，填写宿主机 Mihomo 的 `MIHOMO_SECRET`。如果已有使用同一数据库和端口的 RouteSift 进程，先退出该进程，再构建并后台启动容器。

```sh
docker compose up -d --build
```

在宿主机打开 [本机看板](http://127.0.0.1:18765)。容器设置了 `restart: unless-stopped`，可通过 `docker compose ps` 查看 HTTP 健康状态，通过 `docker compose logs -f routesift` 查看日志。Mihomo 是否已连接可在看板确认。

- **连接 Mihomo**　容器默认通过 `ws://host.docker.internal:9090/connections` 访问宿主机。如果控制器端口不同，在 `.env` 中设置 `DOCKER_MIHOMO_URL`，例如 `ws://host.docker.internal:9191/connections`。使用 Linux Docker Engine 时，确保控制器可从 Docker 网桥访问，并设置控制器密钥。 [Docker 容器访问宿主机说明](https://docs.docker.com/compose/how-tos/networking/#custom-dns-with-extra_hosts)
- **局域网访问**　在 `.env` 设置 `AUDITOR_LAN_IP` 和 `AUDITOR_LAN_PASSWORD`。默认宿主机的 `127.0.0.1:18765` 免密，`<AUDITOR_LAN_IP>:18765` 使用 `routesift` 账号登录。Compose 将它们映射到容器内两个端口。若自定义 `AUDITOR_PORT`，请确保它与容器内的 `AUDITOR_LAN_PORT`（默认 `18766`）不同。Mac 换网后，更新 `.env` 的局域网地址并运行 `docker compose up -d`。
- **数据目录**　Compose 将 `./data` 挂载到 `/app/data`，重建容器后仍使用同一个 SQLite 数据库。若自定义 `AUDITOR_DB`，可将数据库路径设在此目录下。

若 DeepSeek 凭据存放在宿主机的另一个 `.env`，可创建本地 `docker-compose.override.yml`，以只读方式挂载到容器。替换下面的宿主机文件路径。

```yaml
services:
  routesift:
    environment:
      DEEPSEEK_ENV_FILE: /run/secrets/deepseek.env
    volumes:
      - /absolute/path/to/deepseek.env:/run/secrets/deepseek.env:ro
```

### 系统服务

**macOS**　将 [launchd 模板](deploy/com.flclash.auditor.plist) 中的 `WORKSPACE_PATH` 替换为项目绝对路径。创建项目下的 `data/`，将修改后的模板保存到 `~/Library/LaunchAgents/com.flclash.auditor.plist`。退出前台进程后加载。

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.flclash.auditor.plist"
```

停止时运行 `launchctl bootout "gui/$(id -u)/com.flclash.auditor"`。

**Linux**　按实际路径修改 [systemd 用户服务模板](deploy/flclash-auditor.service) 的 `WorkingDirectory` 与 `ExecStart`，保存为 `~/.config/systemd/user/flclash-auditor.service`，退出前台进程后运行下列命令。

```sh
systemctl --user daemon-reload
systemctl --user enable --now flclash-auditor.service
```

## 排查与开发

- **控制器尚未监听**　检查客户端是否启动内核并开启了外部控制器，以及 `MIHOMO_URL` 的端口是否一致。
- **鉴权失败**　检查 `MIHOMO_SECRET` 是否和控制器一致。修改 `.env` 后重启 RouteSift。
- **虚拟环境没有 pip**　如果 `.venv` 由 `uv venv` 创建，可以运行 `.venv/bin/python -m ensurepip --upgrade` 补齐 pip，或使用 `uv pip install --python .venv/bin/python -r requirements.txt` 安装依赖。
- **进程列为空**　在代理客户端开启「查找进程」，然后检查后续采样。
- **精确域名没有数据**　切换到「含子域名」，检查流量是否发往 API、CDN 等子域名。
- **手机模块无法更新**　确认 RouteSift 正在运行，iPhone 能访问 Mac 的局域网地址，且订阅地址与「接入 Shadowrocket」里显示的一致。
- **手机实时日志没连接**　保持 Shadowrocket 已连接，检查诊断日志和「允许访问」是否开启。手机换网后核对诊断页面显示的地址。

[采集](collector.py)负责快照与增量，[存储](storage.py)负责 SQLite 和审核记录，[分析](analyzer.py)与[域名详情](domain_insights.py)负责聚合，[服务](server.py)托管[看板](static/index.html)和[规则导出](rule_exporter.py)。

## License

[MIT](LICENSE)
