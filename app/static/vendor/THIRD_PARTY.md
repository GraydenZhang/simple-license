# 本地前端依赖

| 依赖 | 固定版本 | 许可证 | 来源 |
| --- | --- | --- | --- |
| Bootstrap CSS | 5.3.8 | MIT | https://registry.npmjs.org/bootstrap/-/bootstrap-5.3.8.tgz |
| Bootstrap Icons | 1.13.1 | MIT | https://registry.npmjs.org/bootstrap-icons/-/bootstrap-icons-1.13.1.tgz |

Bootstrap 使用原版 `dist/css/bootstrap.min.css`，用于按钮、表单、可视隐藏等基础组件；交互继续使用项目原生 JavaScript 与 HTML dialog。样式加载顺序为 Bootstrap、原项目 style.css、tokens.css、ux.css。

图标 sprite 从 Bootstrap Icons 的17个原版SVG提取：grid、envelope、file-earmark-text、shield-check、people、gear、clock、arrow-right、plus-lg、check-lg、download、search、buildings、box-arrow-right、box-arrow-up-right、info-circle、x-lg。图标键名与原 Jinja 宏保持兼容。两个静态服务各有独立副本，核验端不会向15001请求图标。

对应许可证保存在本目录的 `bootstrap-LICENSE.txt` 与 `bootstrap-icons-LICENSE.txt`。运行时所有依赖均由本机提供，不依赖CDN。升级时更新版本、来源、许可及副本一致性检查。
