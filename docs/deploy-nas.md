# NAS / 本地部署与 Bot 主动接收

## 入口模式

`wechat-md-server` 保留 Webhook，并新增主动接收模式：

| 平台 | 公网部署 | NAS / 本地部署 |
| --- | --- | --- |
| Telegram | Webhook | Polling |
| 飞书 | Webhook | 长连接 |

同一平台只能选择一种接收方式。Telegram Polling 启动前会清理旧 Webhook，避免 Telegram 端冲突。

## 推荐组合

### NAS 本地模式

```text
NAS:
- wechat-md-server
- obsidian-fast-note-sync
- Obsidian Vault / 同步目录
- SQLite / 缓存 / 生成文件
```

推荐配置：

```env
WECHAT_MD_DEPLOYMENT_MODE=nas
WECHAT_MD_TELEGRAM_RECEIVE_MODE=polling
WECHAT_MD_TELEGRAM_POLL_INTERVAL=2
WECHAT_MD_FEISHU_RECEIVE_MODE=long_connection
```

### VPS 模式

```text
VPS:
- wechat-md-server
- HTTPS 域名 / 反向代理
```

推荐配置：

```env
WECHAT_MD_DEPLOYMENT_MODE=vps
WECHAT_MD_TELEGRAM_RECEIVE_MODE=webhook
WECHAT_MD_FEISHU_RECEIVE_MODE=webhook
```

## FNS 部署

FNS 可以与本项目同机，也可以部署在 NAS、局域网机器或通过 Tailscale / Cloudflare Tunnel / frp 可访问的设备上。只要 `wechat-md-server` 能访问 FNS API，且 FNS 能写入目标 Vault，就可以完成同步。

## Docker Compose 示例

```yaml
services:
  wechat-md-server:
    image: lulalulaluobo/wechat-md-server:1.0.0
    container_name: wechat-md-server
    restart: unless-stopped
    environment:
      WECHAT_MD_APP_MASTER_KEY: "replace-with-your-master-key"
      WECHAT_MD_ADMIN_USERNAME: "admin"
      WECHAT_MD_ADMIN_PASSWORD: "replace-with-your-admin-password"
      WECHAT_MD_SESSION_COOKIE_SECURE: "false"
      WECHAT_MD_RUNTIME_CONFIG_PATH: /app/data/runtime-config.json
      WECHAT_MD_DEFAULT_OUTPUT_DIR: /app/data/workdir-output
      WECHAT_MD_DEPLOYMENT_MODE: "nas"
      WECHAT_MD_TELEGRAM_RECEIVE_MODE: "polling"
      WECHAT_MD_TELEGRAM_POLL_INTERVAL: "2"
      WECHAT_MD_FEISHU_RECEIVE_MODE: "long_connection"
    ports:
      - "8765:8765"
    volumes:
      - ./data:/app/data
```

NAS / 本地模式下，管理页默认只建议在局域网访问。需要公网访问时，请自行配置隧道、VPN 或反向代理。
