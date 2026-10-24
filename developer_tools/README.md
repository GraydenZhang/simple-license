# 开发者离线签发与构建

本目录只供开发者本地使用，Docker构建上下文和客户导出清单均排除此目录。任何私钥不得放到源码、镜像或客户交付目录。不提供开发默认签名密钥，也不接受环境变量替换后端信任公钥。

## 生成密钥

安装项目依赖后执行（Python3.12+）：

```sh
python developer_tools/issuer.py keygen --key-id school-2026 --public-out public-2026.json
```

两次隐藏输入至少12位的私钥保护口令。私钥以加密PKCS8 PEM仅保存于开发者用户目录 `~/.school-permit-signing/school-2026.pem`，命令不能指定其他私钥保存路径，拒绝覆盖已有密钥。Linux目录700、文件600；Windows须由开发者检查并限制NTFS ACL为当前开发者（Unix模式位不能替代Windows ACL）。离线备份此加密目录与口令，分开保管；遗失后不能恢复或继续使用该密钥签发。源码仓库和客户备份中均不存私钥。测试使用的临时密钥仅在操作系统测试临时目录中生成，绝不用于交付。

将导出的**公钥集合**审阅后合并到 `app/license_public_keys.json`，内容为key_id到Base64编码32字节原始Ed25519公钥的JSON对象。公钥可入库，私钥不可。当前该文件故意为空；构建会拒绝空信任集合，防止携带测试密钥或任意默认密钥发布。不要用测试文件中的公钥代替开发者正式公钥。

## 签发

客户只提供fingerprint输出，开发者执行：

```sh
python developer_tools/issuer.py issue --key-id school-2026 --product school-permit \
  --customer '示例学校' --fingerprint '客户提供的sp1-设备指纹' \
  --expires-at 2027-10-24T16:00:00Z --out license.lic
```

命令隐藏询问私钥保护口令，不允许命令行传入口令。issued_at默认当前UTC，可用 `--issued-at` 显式指定；license_id默认UUID，可用 `--license-id` 指定字母数字下划线连字符编号。输出已存在则拒绝覆盖。示例截止时刻是北京时间2027-10-25 00:00，等价于授权覆盖北京时间10月24日全天；截止瞬间无效。产品参数虽可签其他值，当前后端只接受固定 school-permit。许可证是客户信息，不是私钥；只将license.lic交给客户。

## 签名字节协议 v1

签发和验证都导入 `app/license_protocol.py`，不各自实现一套序列化。

外层JSON恰有payload、signature两个字段；signature为严格标准Base64编码Ed25519签名。拒绝重复JSON键、额外字段、无效类型、超16KiB文件。payload恰有：version（整数1，布尔值不接受）、key_id、product、customer、fingerprint、issued_at、expires_at、license_id（其余都是字符串）。字符串不自动修剪或Unicode归一化；禁止控制字符和孤立代理码点，长度限制见协议。时间严格为整秒UTC `YYYY-MM-DDTHH:MM:SSZ`。

待签名字节为ASCII前缀 `school-permit/license/v1` + 单个零字节 + `json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')`。字段排序是Python字符串键排序，此固定字段集均为ASCII；无浮点值、无BOM、无换行、无空格。Unicode使用JSON的ASCII转义。外层的缩进和键顺序不影响验签。此为本产品明确定义的规范格式，**不声称实现RFC8785**。

设备指纹是 `sp1-` + SHA256(`school-permit/device/v1` ASCII + 零字节 + 规范化machine-id ASCII)的64位小写十六进制。机器标识仅去除首尾空白并转小写，须为32位十六进制且不全零。后端不输出原始标识，不以容器ID、MAC或瞬时信息替代。

## 密钥轮换

生成新key_id的新密钥，将新旧公钥同时加入集合并发布过渡镜像；旧许可证仍可验证。客户先导入过渡镜像，再原子替换为新密钥签发的许可证。确认全部迁移后，未来镜像可移除旧公钥。移除前应逐台确认许可证key_id，否则会进入只读。私钥泄露需要新镜像移除旧公钥并重发许可证，纯离线系统不能通过外部服务器即时撤销已离线运行的旧镜像。

## 编译和导出

在有Docker Buildx的Linux构建机执行：

```sh
sh deployment/build-release.sh 1.2.0
docker pull --platform linux/amd64 postgres:16-alpine
# 先在隔离数据卷完成运行验收，再导出。目标目录必须不存在。
sh deployment/export-release.sh 1.2.0 /secure-deliveries/school-permit-1.2.0
```

采用Nuitka standalone编译整个应用包，两个服务及初始化/迁移/备份/维护入口共用一个可执行程序；SQLAlchemy动态驱动显式包含。模板、浏览器静态文件、字体和验签公钥作为运行资源保留。最终阶段只COPY编译输出，不先COPY源码再删除。分发检查若发现.py/.pyc、测试、开发工具或疑似私钥会失败，必须排查构建输出，不得绕过检查发布。最终镜像含必要共享库、标准库/第三方编译组件及可见静态资源；不承诺零逆向恢复风险。

导出目录仅白名单复制部署文件，并保存应用+数据库镜像为school-permit.tar.gz及SHA256SUMS；未复制源码、签发工具、私钥、开发.env或任何业务数据。授权license.lic独立放入客户包license目录。必须复核最终镜像文件清单与所有层，验证Linux amd64离线load、双端口、首次初始化、迁移、中文PNG/二维码、附件、期限变化、重启、更新和备份恢复；源码测试通过不等于容器验收通过。

构建机可以联网下载基础镜像和编译依赖；“离线运行/部署”不等于在没有任何预置基础镜像和依赖的机器上离线构建。正式发布应记录镜像ID及第三方依赖，必要时固定基础镜像digest。Nuitka使用方式参考[官方手册](https://nuitka.net/user-documentation/user-manual.html)，Ed25519使用[cryptography官方说明](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)。
