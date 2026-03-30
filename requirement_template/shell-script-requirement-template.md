# Shell 脚本需求文档模板

> 本文件已按 `wechat-md-ob.sh` 当前行为同步，可直接作为后续维护与增强依据。

## 1. 基础信息

- 脚本名称（显示名）: wechat-md-ob 部署脚本
- 输出文件名（例如 `myops.sh`）: wechat-md-ob.sh
- 版本号（例如 `0.1.0`）: 0.1.0
- 简短描述: 在 Debian/Ubuntu 上一键安装、更新、查看、重启和卸载 wechat-md-server Docker 服务
- 命令别名（例如 `m`）: wmob

## 2. 运行环境

- 目标系统（可多选）: Ubuntu / Debian
- 必须 root 运行: 是
- 是否支持无参数菜单模式: 是
- 默认语言风格: 中文

## 3. 功能清单（必须）

请列出必须实现的功能（按优先级）:

1. 检测并确认安装 Docker / Docker Compose
2. 在 `/opt/wechat-md-ob` 写入 `docker-compose.yml`
3. 自动创建 `data/` 并修正为 `999:999` 权限
4. 首次部署自动生成主密钥和管理员密码
5. 支持安装、更新、状态、日志、重启、卸载

## 4. 命令接口设计

请定义 CLI 子命令（示例：`tool update`、`tool docker ps`）:

- 子命令1:
  - 用法: `./wechat-md-ob.sh install`
  - 参数: 无
  - 行为: 检测环境、必要时安装 Docker、初始化部署目录、写 compose、启动服务
- 子命令2:
  - 用法: `./wechat-md-ob.sh update`
  - 参数: 无
  - 行为: 保留已有数据和凭据，重写 compose，拉新镜像并重启
- 子命令3:
  - 用法: `./wechat-md-ob.sh status`
  - 参数: 无
  - 行为: 输出 compose 路径、容器状态、镜像和登录地址
- 子命令4:
  - 用法: `./wechat-md-ob.sh logs`
  - 参数: 无
  - 行为: 跟随输出 docker compose logs
- 子命令5:
  - 用法: `./wechat-md-ob.sh restart`
  - 参数: 无
  - 行为: 重启容器
- 子命令6:
  - 用法: `./wechat-md-ob.sh uninstall`
  - 参数: 无
  - 行为: 停止并移除容器；默认不删除 data，二次确认后才可删数据
- 子命令7:
  - 用法: `./wechat-md-ob.sh help`
  - 参数: 无
  - 行为: 输出帮助说明

## 5. 菜单设计（如果启用菜单模式）

- 菜单项1: 安装 / 初始化部署
- 菜单项2: 更新镜像并重启
- 菜单项3: 查看服务状态
- 菜单项4: 查看日志
- 菜单项5: 重启服务
- 菜单项6: 卸载服务
- 菜单项7: 退出

## 6. 依赖与安装策略

- 包管理器策略: 仅 apt
- 需要自动安装的依赖: curl / ca-certificates / gnupg / docker-ce / docker-ce-cli / containerd.io / docker-buildx-plugin / docker-compose-plugin
- 禁止自动安装的依赖: nginx / certbot / firewall / database

## 7. 安全与风险控制

- 涉及高风险操作（删除/防火墙/SSH改端口）: 是
- 是否要求二次确认: 是
- 是否要求自动备份关键配置后再改动: 否
- 禁止执行的操作: 未确认时删除 `/opt/wechat-md-ob/data`

## 8. 日志与可观测性

- 是否记录本地日志: 否
- 日志路径: 不单独落本地文件，直接通过 `docker compose logs`
- 是否允许外部 telemetry 上报: 否

## 9. 输出与交付要求

- 生成脚本时还需输出:
  - [x] 使用说明
  - [x] 功能列表
  - [x] 风险提示
  - [x] 回滚建议
- 是否需要附带测试命令清单: 是

## 10. 示例输入输出（可选但强烈建议）

- 示例命令: `./wechat-md-ob.sh install`
- 预期输出: 成功创建 `/opt/wechat-md-ob`、拉起容器、打印登录地址和自动生成的管理员凭据

## 11. 备注

- compose 内容固定使用 `your-namespace/wechat-md-server:latest`
- 端口固定 `8765`
- 凭据直接写入 `/opt/wechat-md-ob/docker-compose.yml`
- 当前阶段不引入 `.env`、Nginx、HTTPS、systemd 封装
