# wechat-md-server

Local FastAPI service for converting WeChat public articles into Markdown and syncing them to Fast Note Sync for Obsidian.

## Run

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`.

- Main page: `http://127.0.0.1:8765/`
- Login: built-in single account
- Settings page: `http://127.0.0.1:8765/settings`

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

## VPS Deployment

Recommended layout:

```text
/opt/wechat-md-server/
├── .env
├── .venv/
├── app/
├── data/
│   ├── runtime-config.json
│   └── workdir/
└── deploy/systemd/wechat-md-server.service.example
```

Recommended deployment steps:

1. Create a virtual environment and install requirements.
2. Copy `.env.example` to `.env` and set a strong `WECHAT_MD_APP_MASTER_KEY`.
3. Set `WECHAT_MD_SESSION_COOKIE_SECURE=true`.
4. Run behind HTTPS reverse proxy such as Nginx or Caddy.
5. Use the systemd sample in [wechat-md-server.service.example](/path/to/wechat-md-server/deploy/systemd/wechat-md-server.service.example).

Recommended reverse-proxy boundary:

- Expose only the web service entrypoint.
- Keep the app itself listening on `127.0.0.1`.
- Let the reverse proxy terminate HTTPS.
- Prefer adding HSTS and host restrictions at the proxy layer.

## Backup And Restore

Back up:

- `data/runtime-config.json`
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
