# wechat-md-server

[中文说明](README.zh-CN.md)

Local FastAPI service for converting WeChat public articles into Markdown and syncing them to Fast Note Sync for Obsidian.

## Run

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8765
```

Open `http://127.0.0.1:8765` or `http://<your-lan-ip>:8765`.

- Main page: `http://127.0.0.1:8765/` or `http://<your-lan-ip>:8765/`
- Login: built-in single account
- Settings page: `http://127.0.0.1:8765/settings` or `http://<your-lan-ip>:8765/settings`

## Docker

Recommended container base:

- `python:3.14-slim-bookworm`
- `amd64 / x86_64`
- `docker-compose.yml` as the primary deployment entrypoint

Start with Docker Compose:

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

Production-oriented Compose:

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f
```

Open:

- `http://127.0.0.1:8765/login`
- `http://<your-lan-ip>:8765/login`

Container behavior:

- the app listens on `0.0.0.0:8765`
- runtime data is persisted through `./data:/app/data`
- runtime config path is `/app/data/runtime-config.json`
- temporary work output uses `/app/data/workdir-output`

Deployment files:

- `Dockerfile`
- `.dockerignore`
- `docker-compose.yml`
- `docker-compose.prod.yml`

Image size expectation:

- current slim build is typically around `65MB - 80MB`
- exact size depends on image metadata and wheel reuse

Why not Alpine:

- `Pillow` and `cryptography` are easier to keep stable on Debian slim
- the project benefits more from predictable wheels and easier debugging than from saving a few tens of MB

## Required Environment

Runtime secrets now depend on a master key. The service will not load encrypted runtime config without it.

- `WECHAT_MD_APP_MASTER_KEY`
- `WECHAT_MD_ADMIN_USERNAME`
- `WECHAT_MD_ADMIN_PASSWORD`

If `WECHAT_MD_ADMIN_PASSWORD` is omitted on first boot, the service will generate a random initial password and print it once to stdout.

Copy [.env.example](/path/to/wechat-md-server/.env.example) to your deployment environment and replace all placeholders.

## Defaults

- FNS target directory: `00_Inbox/微信公众号`
- Image mode: `wechat_hotlink`
- Runtime config path: `data/runtime-config.json`
- Internal work directory root: `data/workdir/`

Optional environment variables:

- `WECHAT_MD_RUNTIME_CONFIG_PATH`
- `WECHAT_MD_DEFAULT_OUTPUT_DIR`
- `WECHAT_MD_SESSION_COOKIE_SECURE`
- `WECHAT_MD_FNS_BASE_URL`
- `WECHAT_MD_FNS_TOKEN`
- `WECHAT_MD_FNS_VAULT`
- `WECHAT_MD_FNS_TARGET_DIR`
- `WECHAT_MD_IMAGE_MODE`
- `WECHAT_MD_IMAGE_STORAGE_PROVIDER`
- `WECHAT_MD_IMAGE_STORAGE_ENDPOINT`
- `WECHAT_MD_IMAGE_STORAGE_REGION`
- `WECHAT_MD_IMAGE_STORAGE_BUCKET`
- `WECHAT_MD_IMAGE_STORAGE_ACCESS_KEY_ID`
- `WECHAT_MD_IMAGE_STORAGE_SECRET_ACCESS_KEY`
- `WECHAT_MD_IMAGE_STORAGE_PATH_TEMPLATE`
- `WECHAT_MD_IMAGE_STORAGE_PUBLIC_BASE_URL`

## Current Behavior

- The web UI is login-protected.
- Session cookie supports `Secure` mode through `WECHAT_MD_SESSION_COOKIE_SECURE=true`.
- Repeated login failures are rate-limited.
- Sensitive runtime values are encrypted before being written to `runtime-config.json`:
  - FNS token
  - S3 secret access key
  - session secret
- Converted notes are written to the configured Fast Note Sync target.
- FNS mode uses internal temporary work directories instead of writing into the Obsidian inbox path directly.
- Image handling is controlled globally from `/settings`:
  - `wechat_hotlink`: keep original WeChat image URLs in Markdown
  - `s3_hotlink`: upload static images to a generic S3-compatible object store and use `public_base_url/object_key`
- In `s3_hotlink`, GIF and SVG keep the original WeChat image URLs.
- Optional AI polish can generate:
  - frontmatter
  - summary
  - tags
  - template blocks
  - optional `content_polished` body output
- AI body polish is opt-in and can be overridden per single/batch run.
- The settings page supports Clipper-style template import from a JSON file and maps it into the internal interpreter template fields.

## AI Polish

- Built-in AI workflow is optional and disabled by default.
- Supported provider types:
  - `OpenAI Compatible`
  - `Anthropic`
  - `Gemini`
  - `Ollama`
  - `OpenRouter`
- Built-in providers are read-only presets; you can also add custom providers.
- Multiple models can be configured and one current model is selected for execution.
- `Test AI Connectivity` validates the currently selected model/provider pair without running a full article conversion.
- Interpreter-related settings include:
  - context template
  - prompt template
  - frontmatter template
  - body template
  - optional extra body block generation
  - optional full body polish output (`content_polished`)

## Settings UI

- `/settings` provides a server-backed admin settings page.
- The settings page includes overview cards, FNS connection detection, image mode selection, and inline form validation hints.
- FNS config can be imported from clipboard or pasted JSON in this format:
  - `api`
  - `apiToken`
  - `vault`
- Clipboard import only fills the form. Settings are not persisted until you click save.
- Secret fields are masked on reload and never returned in plaintext from the settings API.
- S3 image settings are entered manually in the settings page. There is no Obsidian plugin or R2 config file dependency.
- The AI section now separates:
  - current model selection
  - provider management
  - model pool management
  - interpreter template configuration
- Single conversion clears the article URL field after success.
- Batch creation clears the textarea and uploaded file selection after success.

## Bot Integrations

- Telegram Bot webhook is supported for single-article conversion.
- Feishu Bot webhook is supported for single-article conversion.
- Both bot flows:
  - accept one WeChat article link per message
  - immediately acknowledge receipt
  - run conversion asynchronously
  - send a completion reply with title, sync path, and image mode
- Feishu v1 currently supports:
  - private chat only
  - `open_id` whitelist (can be left empty during bootstrap)
  - developer-server webhook mode
- If Feishu message sending fails due to missing app permissions, the webhook request is still accepted and the failure is logged instead of crashing the webhook handler.

## Reset Admin Password

If `.env` has changed but an existing `runtime-config.json` already contains an admin password hash, use the offline reset command instead of expecting `.env` to overwrite it automatically.

Python CLI:

```powershell
python -m app.cli.reset_admin_password --password "new-secret"
python -m app.cli.reset_admin_password --random
python -m app.cli.reset_admin_password --username admin --password "new-secret"
```

PowerShell wrapper:

```powershell
.\scripts\reset-admin-password.ps1 -Password "new-secret"
.\scripts\reset-admin-password.ps1 -Random
```

Notes:

- The command requires the correct `WECHAT_MD_APP_MASTER_KEY`.
- Resetting the password also rotates `session_secret`, so existing login sessions are invalidated.
- The command only updates admin credentials. It does not change FNS or S3 settings.

## VPS Deployment

Recommended layout:

```text
/opt/wechat-md-server/
├── .env
├── data/
│   ├── runtime-config.json
│   ├── workdir/
│   └── workdir-output/
├── docker-compose.yml
└── deploy/systemd/wechat-md-server.service.example
```

Recommended deployment steps:

1. Copy `.env.example` to `.env` and set a strong `WECHAT_MD_APP_MASTER_KEY`.
2. Set `WECHAT_MD_SESSION_COOKIE_SECURE=true`.
3. Create the host `data/` directory if needed.
4. Edit `docker-compose.prod.yml` and replace the placeholder runtime secrets.
5. Run `docker compose -f docker-compose.prod.yml pull` and `docker compose -f docker-compose.prod.yml up -d`.
6. The production compose file still publishes `8765` directly, so you can access it with `http://<server-ip>:8765` if needed.
7. If you later want reverse proxy only, change the port binding back to loopback and place Nginx or Caddy in front.
8. If you prefer a non-container deployment, the systemd sample in [wechat-md-server.service.example](/path/to/wechat-md-server/deploy/systemd/wechat-md-server.service.example) remains available.

Recommended reverse-proxy boundary:

- Expose only the web service entrypoint.
- The app can listen on `0.0.0.0`; restrict exposure at the host firewall or proxy layer as needed.
- Let the reverse proxy terminate HTTPS.
- Prefer adding HSTS and host restrictions at the proxy layer.

## Backup And Restore

Back up:

- `data/runtime-config.json`
- `data/workdir/` if you intentionally keep successful temp artifacts
- the `.env` file or at least `WECHAT_MD_APP_MASTER_KEY`

Restore:

1. Restore project files and install dependencies.
2. Restore `runtime-config.json`.
3. Restore the exact same `WECHAT_MD_APP_MASTER_KEY`.
4. Start the service.

If the master key changes, encrypted runtime secrets can no longer be decrypted.

## Development Notes

- Do not commit integration output directories such as `_integration_output/` or `_integration_output_v2/`.
- Do not commit `.env` or `data/runtime-config.json`.
- Use [start-server.ps1](/path/to/wechat-md-server/scripts/start-server.ps1) for local Windows startup if you want a fixed command entrypoint.
