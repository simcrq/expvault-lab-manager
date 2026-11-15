# 第三方组件与许可证

本项目自身的原始代码采用根目录 [LICENSE](LICENSE) 中的 MIT 许可证。第三方组件的版权和许可证保持独立，不能把它们统称为本项目的 MIT 代码。

仓库及独立 ZIP 不包含 Python、Qt、依赖 wheel、虚拟环境或字体文件；依赖由目标电脑从软件包源自行安装。本文件是许可索引，不替代第三方组件随包提供的完整 LICENSE、NOTICE 和版权声明。若之后重新分发依赖、制作包含依赖的 exe/app/安装镜像，须重新核对实际分发文件及其许可证。

## 直接依赖

以下是 2026-11-15 核对的运行环境及项目声明；依赖范围允许更新，最终以实际安装版本的许可文件为准。

| 组件 | 本次核对版本 | 许可证 | 用途 / 官方来源 |
| --- | --- | --- | --- |
| prompt_toolkit | 3.0.53 | BSD-3-Clause | [终端输入](https://github.com/prompt-toolkit/python-prompt-toolkit) |
| rich | 14.3.4 | MIT | [终端显示](https://github.com/Textualize/rich) |
| jsonschema | 4.26.0 | MIT | [输入结构校验](https://github.com/python-jsonschema/jsonschema) |
| filelock | 3.32.7 | MIT | [文件锁](https://github.com/tox-dev/filelock) |
| qrcode | 8.2 | BSD-3-Clause | [二维码](https://github.com/lincolnloop/python-qrcode) |
| psutil | 7.2.2 | BSD-3-Clause | [网卡与进程信息](https://github.com/giampaolo/psutil) |
| cryptography | 49.0.0 | Apache-2.0 OR BSD-3-Clause | [TLS 证书](https://github.com/pyca/cryptography) |
| openpyxl | 3.1.5 | MIT | [可选 Excel / 发布包台账导出](https://openpyxl.readthedocs.io/) |
| PySide6-Essentials | 未安装；声明范围为 >=6.8.3,<7 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only；另有商业许可 | [可选 Qt GUI；6.8.3 上游许可](https://pypi.org/project/PySide6-Essentials/6.8.3/) |

`QR Code` 是 DENSO WAVE INCORPORATED 的注册商标；这与 qrcode 软件包的 BSD 许可是两回事，见该包原始 LICENSE 中的商标说明。

## 本次运行环境中的间接依赖

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| attrs | 26.1.0 | MIT |
| cffi | 2.1.1 | MIT-0 |
| et_xmlfile | 2.0.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| markdown-it-py | 4.3.0 | MIT；包含上游 markdown-it 的许可声明 |
| mdurl | 0.1.2 | MIT |
| pycparser | 3.11 | BSD-3-Clause |
| Pygments | 2.21.0 | BSD-2-Clause |
| referencing | 0.37.0 | MIT |
| rpds-py | 0.30.0 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0；保留包内完整历史许可文本 |
| wcwidth | 0.9.4 | MIT |

以上来自实际安装包的 METADATA 及 LICENSE 文件。它不是所有平台、未来版本或二进制内部所含第三方库的完整物料清单；例如 cryptography wheel 内的 OpenSSL / Rust 组件、Qt wheel 内的第三方代码也有各自的声明。

## 可选 Qt GUI

网页端不需要 Qt。`ev/gui.py` 通过普通 Python 动态导入使用 QtCore、QtGui、QtNetwork、QtWidgets；本项目不静态链接、修改或随仓库捆绑 Qt。

Qt / PySide / Shiboken 的许可证不会因为本项目选择 MIT 而变更。使用 LGPL 路径时，应保留相应版本的完整许可、版权及通知，保障接收者替换相关库和运行替换后程序的权利；若分发 Qt 二进制，还应满足对应源码、安装信息等适用要求。不能把“GUI 可选”或“依赖另装”理解为未来捆绑发行时免于合规。

- [Qt for Python 官方许可说明](https://doc.qt.io/qtforpython-6/commercial/index.html)
- [Qt LGPL / GPL 义务](https://www.qt.io/development/open-source-lgpl-obligations)
- [Qt / PySide 上游源码](https://code.qt.io/cgit/pyside/pyside-setup.git/)

MIT 许可允许本项目原始代码的使用、修改及再分发，但不替代第三方许可、商标授权或代码权属人的授权。
