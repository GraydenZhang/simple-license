# 电子施工许可证系统：客户离线部署

客户只接收 Docker 镜像包、部署文件和 license/license.lic，不接收源码、签发工具或私钥。以下命令在 Linux amd64 的 Docker Compose v2 环境执行。联网构建在开发方完成，客户服务器不需要访问镜像仓库、软件源、CDN 或激活服务器。

## 首次激活

1. 解压交付目录，执行 `sh import-release.sh`。它校验 SHA256SUMS 并通过 `docker load -i school-permit.tar.gz` 导入应用和 PostgreSQL 两个镜像。
2. 复制 `.env.example` 为 `.env`，将权限限制为部署管理员可读（例如 `chmod 600 .env`）。填写随机数据库密码、真实的申请/核验网址和可信代理地址；密码使用足够长的随机字母数字，避免数据库 URL 转义问题。不要沿用开发密码。配置学校本地 HTTPS、DNS、反向代理及核验端口访问控制。
3. 创建 `license/` 目录。确认宿主机 `/etc/machine-id` 存在且是本机持久标识；不要使用容器自己的 machine-id，也不要给每次启动生成随机标识。Compose 的 bind 挂载禁用自动创建源路径。
4. 获取设备指纹（无需已有许可证或运行数据库）：

```sh
docker compose -f docker-compose.yml --profile tools run --rm --no-deps tools fingerprint
```

只将输出的 `sp1-...` 发给软件提供方，不发送原始 machine-id。该指纹是产品域分隔的 SHA-256 派生值，仍属于稳定设备标识，不用于其他产品。

5. 收到授权后，将其保存为 `license/license.lic`。目录应可被 UID 10001 遍历，授权文件可被 UID 10001 读取，例如目录755、文件644；授权许可证含客户信息，应限制宿主机其他用户访问。许可证目录在容器内只读，宿主机标识也只读。
6. 检查授权，然后启动数据库并初始化：

```sh
docker compose -f docker-compose.yml --profile tools run --rm --no-deps tools license-status
docker compose -f docker-compose.yml up -d --pull never db
docker compose -f docker-compose.yml --profile tools run --rm --no-deps tools init
docker compose -f docker-compose.yml up -d --pull never app
```

初始化默认创建系统管理员 admin 和审批员 approver，分别生成独立随机密码并仅在初始化终端显示一次；重复初始化保留已有密码。首次登录只提示建议改密，可选择稍后再说。请妥善接收密码，不复制到报告、构建日志或镜像。必要时可短期安全注入 INIT_ADMIN_PASSWORD、INIT_APPROVER_PASSWORD（12—128位），使用后移除；不要长期放在.env中。

`app` 一个应用容器管理审批与核验两个子进程，端口仍为15001和15002；其中一个异常退出时，父进程停止另一个并退出，由 Docker 重启。根路径404不代表网络隔离。数据库使用 pgdata 卷，私有附件与许可证PNG使用 private 卷；不应公开映射附件目录。

## 授权状态与失败行为

启动两个服务时均验签，但失败不会停止服务或删除数据。每次业务写请求及其事务提交前重新读取许可证并验证，签发业务凭证前另有检查；无缓存，不需要重启便可读取更新的授权。

检查项目包括：严格格式、已信任key_id、Ed25519签名、固定产品 school-permit、设备指纹以及签发/截止时间。有效区间为 **issued_at <= 当前UTC时间 < expires_at**；所有时间必须是 `YYYY-MM-DDTHH:MM:SSZ`，截止时刻不包含在授权范围内。

授权缺失、格式损坏、签名不符、设备不符、产品不符、尚未生效、到期、标识不可读时：业务写接口返回HTTP503和 LICENSE_READ_ONLY。新增邀请、上传/提交申请、审批/驳回、撤销/重签、账户管理及平台设置等业务写入均被禁止。登录、退出、修改自身密码、受原有权限保护的历史查询、材料/凭证下载、扫码核验、CSV导出仍可使用，核验和导出仍正常追加审计日志。初始化、迁移、离线备份及维护命令不受授权限制；维护会按既有保留策略清理日志，如需保存完整证据请先备份。

管理员可访问 `/api/staff/license` 查看当前状态，也可执行上面的 license-status 命令；有效返回退出码0，无效返回2。详细原因不会包含原始宿主机标识或私钥。

## 许可证更新

将新文件放到宿主机 `license/license.new`，校验来源及文件权限后，在**同一目录、同一文件系统**执行：

```sh
mv license/license.new license/license.lic
docker compose -f docker-compose.yml --profile tools run --rm --no-deps tools license-status
```

挂载的是目录而非文件，避免原子替换后容器仍读取旧inode。不需要重启或联网。普通容器重启和镜像升级不会改变宿主机machine-id；新镜像仍需包含签发此许可证的信任公钥。

## 备份和恢复

备份内容必须包括 PostgreSQL 数据库、private 卷（uploads、images以及界面标志）、部署配置及授权文件。口令配置需要单独加密保存；授权不替代数据备份。下列为一致备份示例，请使用新的、仅管理员可访问的备份目录：

```sh
mkdir backup-20261024
chmod 700 backup-20261024
docker compose -f docker-compose.yml stop app
docker compose -f docker-compose.yml exec -T db pg_dump -U permits -d permits -Fc > backup-20261024/database.dump
docker compose -f docker-compose.yml --profile tools run --rm --no-deps \
  --entrypoint /bin/tar tools -C /var/lib/campus-permits -czf - . > backup-20261024/private.tar.gz
cp -R license backup-20261024/license
docker compose -f docker-compose.yml start app
```

逐条确认退出码和备份文件有效后再认定成功。生产数据库容器本地socket配置若要求密码，请使用受限权限PGPASSFILE，不在命令行或日志中打印密码。SQLite独立部署可使用 `school-permit backup 新目标目录`；该命令在PostgreSQL配置下会明确拒绝并提示使用pg_dump。

恢复时停止应用，用新的Compose项目名和新卷启动空数据库，避免覆盖唯一现有数据副本；核对 `.env`。把数据库和private卷作为同一个集合还原：

```sh
docker compose -f docker-compose.yml exec -T db pg_restore -U permits -d permits --exit-on-error < backup-20261024/database.dump
docker compose -f docker-compose.yml --profile tools run --rm --no-deps \
  --entrypoint /bin/tar tools -C /var/lib/campus-permits -xzf - < backup-20261024/private.tar.gz
docker compose -f docker-compose.yml --profile tools run --rm --no-deps tools migrate
docker compose -f docker-compose.yml up -d --pull never app
```

只有在目标库为空且目标卷为新卷时使用上述恢复命令。授权已失效仍允许恢复、查询、下载和导出；设备迁移后若需新增业务，必须重新签发绑定新设备的授权。

## 版本更新和回退

1. 停止应用并完成一致备份，保留旧镜像、旧配置、原授权和备份校验信息。
2. 使用新交付包校验并 docker load 导入镜像，更新 `.env` 的 SCHOOL_PERMIT_VERSION，保留DB_PASSWORD、持久化卷和许可证目录。
3. 执行 `tools migrate`、`tools license-status`，然后 `up -d --pull never app`。迁移命令与源码版Alembic共用0001/0002/0003版本及修订操作；未知数据库版本会拒绝迁移。以后新增修订必须同步编译入口。
4. 验证登录、历史附件、申请、审批、签发和扫码，再恢复对外服务。不得执行 `docker compose down -v`，它会删除业务卷。
5. 失败时停服务。若未发生数据库结构变化，可切回旧版本镜像；若发生结构变化，应恢复升级前的**数据库及private卷整套备份到新卷**，并恢复匹配的旧版本镜像与配置。禁止在仍运行的旧库上覆盖备份，禁止只回退镜像而忽略数据库版本。

从旧版双应用容器Compose迁移：先停旧approval/verify并备份，保留旧Compose项目名及两个卷的真实名称，通过顶层name或显式external卷映射复用。新配置默认项目名 school-permit，直接运行可能创建空的新卷，不能据此判断旧数据丢失。确认卷映射、UID10001文件权限和端口无冲突后再启动；不要删除旧卷。

## 设备迁移与安全边界

新服务器获取新的fingerprint，开发者签发新许可证，完成数据库与附件一致迁移后检查授权再开放写入。不要通过复制旧服务器machine-id来“迁移授权”。纯离线授权不能即时远程撤销旧服务器许可证；迁移后旧服务器停用和数据清理由组织流程保证。

本方案**不能可靠防止整台虚拟机克隆、管理员篡改程序或信任公钥、离线系统时间回滚**。同machine-id的克隆可能得到相同指纹；有宿主机最高权限者可伪造只读挂载来源。数字签名保护授权数据不被未持私钥者合法改签，编译提高源码获取成本，但程序和第三方组件仍可逆向分析；浏览器HTML/JS/CSS始终可见。不要称其为不可破解的DRM。授权机制不替代用户鉴权、备份、主机安全和校内网络访问控制。
