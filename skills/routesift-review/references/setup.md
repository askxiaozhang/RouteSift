# 接入 RouteSift

安装 skill 后，agent 获得分析流程和查询脚本。查询仍需要同一台机器上运行 RouteSift。该 skill 不会自动安装服务或修改 agent 的 MCP 配置。

## 直接查询

已运行本机看板时，可直接用 skill 内的 `scripts/query.py`，仅需 Python 3 标准库，无需安装 MCP 依赖。默认连接 `http://127.0.0.1:18765`，可通过 `--url` 修改本机端口。脚本只接受回环 HTTP 地址，绕过环境代理并拒绝重定向。

服务未运行时，按 RouteSift 仓库 README 的快速开始部署。查询脚本不会启动第二个采集器。Docker 部署可查询映射到宿主机的回环端口。agent 在远端或隔离容器中运行时，`127.0.0.1` 指向该运行环境，需将查询工具部署在能访问 RouteSift 的宿主机。

## 可选：注册 MCP

在 RouteSift 仓库目录安装 MCP 依赖：

```sh
.venv/bin/python -m pip install -r requirements-mcp.txt
```

使用 uv 创建的环境也可运行 `uv pip install --python .venv/bin/python -r requirements-mcp.txt`。

在支持本地 stdio MCP 的 harness 中添加以下配置，将两个路径换成真实绝对路径，端口与看板保持一致。实际配置文件位置与字段以 harness 为准。

```json
{
  "mcpServers": {
    "routesift": {
      "command": "/absolute/path/to/RouteSift/.venv/bin/python",
      "args": [
        "/absolute/path/to/RouteSift/routesift/mcp_server.py",
        "--url",
        "http://127.0.0.1:18765"
      ]
    }
  }
}
```

harness 负责启动 stdio 进程，连接后应发现四个只读工具。该进程通过现有 HTTP 服务查询，不直接打开数据库。模型选择和凭据由 harness 管理。
