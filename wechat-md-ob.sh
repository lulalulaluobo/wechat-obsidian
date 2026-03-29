#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_NAME="wechat-md-ob.sh"
SCRIPT_VERSION="0.1.0"
APP_NAME="wechat-md-ob"
INSTALL_DIR="/opt/wechat-md-ob"
COMPOSE_FILE="${INSTALL_DIR}/docker-compose.yml"
DATA_DIR="${INSTALL_DIR}/data"
CONTAINER_NAME="wechat-md-server"
IMAGE_NAME="lulalulaluobo/wechat-md-server:latest"
APP_PORT="8765"
DEFAULT_ADMIN_USERNAME="admin"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

COMPOSE_CMD=""

msg_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
msg_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
msg_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
msg_err() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    msg_err "请使用 root 运行此脚本。"
    exit 1
  fi
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

confirm_action() {
  local prompt="${1:-确认继续吗？ [y/N]: }"
  local answer
  read -r -p "${prompt}" answer
  [[ "${answer:-}" =~ ^[Yy]$ ]]
}

detect_os() {
  if [[ ! -f /etc/os-release ]]; then
    msg_err "无法识别系统版本，当前脚本仅支持 Debian/Ubuntu。"
    exit 1
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *)
      msg_err "当前系统为 ${ID:-unknown}，脚本仅支持 Debian/Ubuntu。"
      exit 1
      ;;
  esac
}

resolve_compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
    return
  fi
  if command_exists docker-compose; then
    COMPOSE_CMD="docker-compose"
    return
  fi
  COMPOSE_CMD=""
}

ensure_compose_cmd() {
  resolve_compose_cmd
  if [[ -z "${COMPOSE_CMD}" ]]; then
    msg_err "未检测到可用的 Docker Compose。"
    exit 1
  fi
}

install_docker_stack() {
  detect_os
  if ! confirm_action "未检测到 Docker / Docker Compose，是否现在自动安装？ [y/N]: "; then
    msg_err "已取消安装 Docker 环境。"
    exit 1
  fi

  msg_info "更新 apt 索引并安装基础依赖..."
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg

  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "${ID}")/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi

  # shellcheck disable=SC1091
  source /etc/os-release
  cat >/etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable
EOF

  msg_info "安装 Docker Engine 与 Compose 插件..."
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  systemctl enable --now docker

  if ! docker version >/dev/null 2>&1; then
    msg_err "Docker 安装后验证失败。"
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    msg_err "Docker Compose 安装后验证失败。"
    exit 1
  fi

  resolve_compose_cmd
  msg_ok "Docker 环境安装完成。"
}

ensure_docker_ready() {
  if ! command_exists docker || ! docker version >/dev/null 2>&1; then
    install_docker_stack
    return
  fi
  resolve_compose_cmd
  if [[ -z "${COMPOSE_CMD}" ]]; then
    install_docker_stack
  fi
}

generate_secret() {
  python3 - <<'PY'
import secrets, string
alphabet = string.ascii_letters + string.digits + "-_"
print("".join(secrets.choice(alphabet) for _ in range(32)))
PY
}

detect_primary_ip() {
  local ip=""
  if command_exists ip; then
    ip=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')
  fi
  if [[ -z "${ip}" ]] && command_exists hostname; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  printf '%s' "${ip}"
}

read_existing_env_value() {
  local key="$1"
  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    return 0
  fi
  python3 - "${COMPOSE_FILE}" "${key}" <<'PY'
import re
import sys
from pathlib import Path

compose_path = Path(sys.argv[1])
key = sys.argv[2]
text = compose_path.read_text(encoding="utf-8")
pattern = re.compile(rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', re.MULTILINE)
match = pattern.search(text)
if match:
    print(match.group(1))
PY
}

write_compose_file() {
  local master_key="$1"
  local admin_password="$2"
  cat >"${COMPOSE_FILE}" <<EOF
services:
  wechat-md-server:
    image: ${IMAGE_NAME}
    container_name: ${CONTAINER_NAME}
    restart: unless-stopped
    environment:
      WECHAT_MD_APP_MASTER_KEY: "${master_key}"
      WECHAT_MD_ADMIN_USERNAME: "${DEFAULT_ADMIN_USERNAME}"
      WECHAT_MD_ADMIN_PASSWORD: "${admin_password}"
      WECHAT_MD_SESSION_COOKIE_SECURE: "true"
      WECHAT_MD_RUNTIME_CONFIG_PATH: /app/data/runtime-config.json
      WECHAT_MD_DEFAULT_OUTPUT_DIR: /app/data/workdir-output
    ports:
      - "${APP_PORT}:${APP_PORT}"
    volumes:
      - ./data:/app/data
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${APP_PORT}/login', timeout=5).read()"
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
EOF
}

prepare_directories() {
  mkdir -p "${INSTALL_DIR}" "${DATA_DIR}"
  chown -R 999:999 "${DATA_DIR}"
  chmod -R u+rwX "${DATA_DIR}"
}

validate_compose() {
  ensure_compose_cmd
  (cd "${INSTALL_DIR}" && ${COMPOSE_CMD} -f "${COMPOSE_FILE}" config >/dev/null)
}

compose_up() {
  ensure_compose_cmd
  (cd "${INSTALL_DIR}" && ${COMPOSE_CMD} -f "${COMPOSE_FILE}" pull)
  (cd "${INSTALL_DIR}" && ${COMPOSE_CMD} -f "${COMPOSE_FILE}" up -d)
}

show_deploy_summary() {
  local admin_password="$1"
  local ip
  ip="$(detect_primary_ip)"
  local login_url="http://服务器IP:${APP_PORT}/login"
  if [[ -n "${ip}" ]]; then
    login_url="http://${ip}:${APP_PORT}/login"
  fi

  echo
  echo -e "${BOLD}服务信息${NC}"
  echo "安装目录: ${INSTALL_DIR}"
  echo "容器名称: ${CONTAINER_NAME}"
  echo "镜像名称: ${IMAGE_NAME}"
  echo "端口: ${APP_PORT}"
  echo "登录地址: ${login_url}"
  echo "管理员账号: ${DEFAULT_ADMIN_USERNAME}"
  echo "管理员密码: ${admin_password}"
  echo
  msg_warn "请妥善保存管理员密码与主密钥；如需正式暴露公网，建议再配反向代理与 HTTPS。"
}

install_app() {
  require_root
  ensure_docker_ready

  if [[ -f "${COMPOSE_FILE}" ]]; then
    msg_warn "检测到已有安装，自动切换为更新并保留数据模式。"
    update_app
    return
  fi

  msg_info "初始化部署目录..."
  prepare_directories

  local master_key
  local admin_password
  master_key="$(generate_secret)"
  admin_password="$(generate_secret)"

  msg_info "写入 docker-compose.yml..."
  write_compose_file "${master_key}" "${admin_password}"

  msg_info "验证 compose 配置..."
  validate_compose

  msg_info "拉取镜像并启动服务..."
  compose_up

  msg_ok "服务已启动。"
  show_deploy_summary "${admin_password}"
}

update_app() {
  require_root
  ensure_docker_ready

  if [[ ! -d "${INSTALL_DIR}" ]]; then
    msg_warn "未检测到安装目录，自动执行 install。"
    install_app
    return
  fi

  prepare_directories

  local master_key
  local admin_password
  master_key="$(read_existing_env_value "WECHAT_MD_APP_MASTER_KEY")"
  admin_password="$(read_existing_env_value "WECHAT_MD_ADMIN_PASSWORD")"

  if [[ -z "${master_key}" ]]; then
    master_key="$(generate_secret)"
    msg_warn "未找到现有主密钥，已重新生成并写入 compose。"
  fi
  if [[ -z "${admin_password}" ]]; then
    admin_password="$(generate_secret)"
    msg_warn "未找到现有管理员密码，已重新生成并写入 compose。"
  fi

  msg_info "重写 docker-compose.yml 并保留已有数据..."
  write_compose_file "${master_key}" "${admin_password}"
  validate_compose
  compose_up

  msg_ok "服务已更新。"
  show_deploy_summary "${admin_password}"
}

status_app() {
  require_root
  ensure_docker_ready

  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    msg_warn "未找到 ${COMPOSE_FILE}。"
    return
  fi

  local ip
  ip="$(detect_primary_ip)"
  local login_url="http://服务器IP:${APP_PORT}/login"
  if [[ -n "${ip}" ]]; then
    login_url="http://${ip}:${APP_PORT}/login"
  fi

  echo -e "${BOLD}部署状态${NC}"
  echo "安装目录: ${INSTALL_DIR}"
  echo "Compose 文件: ${COMPOSE_FILE}"
  echo "镜像名称: ${IMAGE_NAME}"
  echo "容器名称: ${CONTAINER_NAME}"
  echo "登录地址: ${login_url}"
  echo
  (cd "${INSTALL_DIR}" && ${COMPOSE_CMD} -f "${COMPOSE_FILE}" ps)
}

logs_app() {
  require_root
  ensure_docker_ready
  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    msg_err "未找到 ${COMPOSE_FILE}。"
    exit 1
  fi
  (cd "${INSTALL_DIR}" && ${COMPOSE_CMD} -f "${COMPOSE_FILE}" logs -f)
}

restart_app() {
  require_root
  ensure_docker_ready
  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    msg_err "未找到 ${COMPOSE_FILE}。"
    exit 1
  fi
  (cd "${INSTALL_DIR}" && ${COMPOSE_CMD} -f "${COMPOSE_FILE}" restart)
  msg_ok "服务已重启。"
}

uninstall_app() {
  require_root
  ensure_docker_ready
  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    msg_warn "未检测到 compose 文件，无需卸载。"
    return
  fi

  if ! confirm_action "将停止并移除容器，但默认保留 data 数据。确认继续？ [y/N]: "; then
    msg_warn "已取消卸载。"
    return
  fi

  (cd "${INSTALL_DIR}" && ${COMPOSE_CMD} -f "${COMPOSE_FILE}" down)
  msg_ok "容器已停止并移除。"

  if confirm_action "是否同时删除 ${DATA_DIR} 数据目录？此操作不可恢复。 [y/N]: "; then
    if confirm_action "再次确认删除 ${DATA_DIR}？ [y/N]: "; then
      rm -rf "${DATA_DIR}"
      msg_ok "数据目录已删除。"
    else
      msg_warn "已取消删除数据目录。"
    fi
  fi

  rm -f "${COMPOSE_FILE}"
  msg_ok "Compose 文件已移除，安装目录保留。"
}

show_help() {
  cat <<EOF
${SCRIPT_NAME} v${SCRIPT_VERSION}

用法:
  ${SCRIPT_NAME} install
  ${SCRIPT_NAME} update
  ${SCRIPT_NAME} status
  ${SCRIPT_NAME} logs
  ${SCRIPT_NAME} restart
  ${SCRIPT_NAME} uninstall
  ${SCRIPT_NAME} help

说明:
  - 首次安装会自动生成主密钥和管理员密码
  - 更新会保留 data 目录和已有凭据
  - 当前脚本仅支持 Debian/Ubuntu
  - Docker / Docker Compose 缺失时会先确认再安装
EOF
}

main_menu() {
  while true; do
    clear
    echo -e "${BOLD}${APP_NAME} 部署菜单${NC}"
    echo "1. 安装 / 初始化部署"
    echo "2. 更新镜像并重启"
    echo "3. 查看服务状态"
    echo "4. 查看日志"
    echo "5. 重启服务"
    echo "6. 卸载服务"
    echo "7. 帮助"
    echo "0. 退出"
    echo
    read -r -p "请输入选项: " choice
    case "${choice}" in
      1) install_app ;;
      2) update_app ;;
      3) status_app ;;
      4) logs_app ;;
      5) restart_app ;;
      6) uninstall_app ;;
      7) show_help ;;
      0) exit 0 ;;
      *) msg_warn "无效选项";;
    esac
    echo
    read -r -p "按回车继续..." _
  done
}

require_root

if [[ $# -eq 0 ]]; then
  main_menu
fi

case "${1:-}" in
  install) install_app ;;
  update) update_app ;;
  status) status_app ;;
  logs) logs_app ;;
  restart) restart_app ;;
  uninstall) uninstall_app ;;
  help|-h|--help) show_help ;;
  *)
    msg_err "未知命令: ${1:-}"
    show_help
    exit 1
    ;;
esac
