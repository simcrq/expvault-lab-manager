# 命令行、扫描与备份恢复

日常管理推荐网页，回到 [新手指南](../README.md)。本页供需要命令行操作的使用者查阅。

下面示例在程序目录执行，Mac 可将 `python` 换成 `python3`。先安装同目录依赖；
独立包使用 `requirements-release.txt`，源码使用 `requirements.txt`，桌面插件另需 `requirements-gui.txt`。

## 明确实验库位置

源码入口会自动寻找库根目录。独立发布包的数据默认位于程序旁的 `实验库/`，
用 CLI 操作该库时显式指定根目录：

```sh
python expvault.py --root "实验库" list
python expvault.py --root "实验库" --help
```

后续示例均显式指定根目录。新建空库可以执行一次：

```sh
python expvault.py init "实验库" --operator "实验员" --project "二维材料器件"
```

不要用空配置覆盖已有库。其他工作空间的实际根目录可在网页工作空间列表查看，替换 `--root` 即可。

## 常用操作

```sh
python expvault.py --root "实验库" new "样品 S12 退火对照" --device S12 --param "温度=350 C"
python expvault.py --root "实验库" list
python expvault.py --root "实验库" show E20261111-001
python expvault.py --root "实验库" add E20261111-001 "测量数据.csv"
python expvault.py --root "实验库" note E20261111-001 "仪器预热 30 分钟后开始测量"
python expvault.py --root "实验库" edit E20261111-001 --status done
python expvault.py --root "实验库" search S12
```

把示例编号替换为 `list` 中显示的真实编号。文件名有空格时加引号，导入文件默认复制，保留源文件。
每个命令都能加 `-h` 查看参数，例如 `python expvault.py backup -h`。

`enter` 可进入单个实验的交互工作区，使用 `add`、`note`、`ls`、`backup` 等短命令；`exit` 退出。
网页支持的完整功能与 CLI 参数未必逐一对应，工作空间选择、仪器对照和双向同步设置以网页为准。

## 本机扫描与双向同步的区别

直接把文件放入实验目录后，执行本机扫描以更新登记和索引：

```sh
python expvault.py --root "实验库" sync
python expvault.py --root "实验库" verify E20261111-001
```

这里的 `sync` **只扫描本机目录**，不连接另一台电脑。两台电脑的双向自动同步在网页「自动同步」配置，
见 [自动同步指南](sync.md)。运行自动同步期间，通过网页修改数据；外部脚本直接写文件时先暂停自动同步。

`verify` 重算 SHA256，帮助发现丢失或被修改的文件；它不会自动恢复文件。索引损坏时可从元数据重建：

```sh
python expvault.py --root "实验库" rebuild
```

请先保留现有实验目录和备份，不要把重建索引当作删除原始资料的理由。

## 配置和创建备份

各工作空间单独配置备份目标。先查看帮助与已有目标，再添加自己的移动硬盘路径：

```sh
python expvault.py --root "实验库" targets
python expvault.py --root "实验库" targets -h
python expvault.py --root "实验库" backup --to "/Volumes/实验备份盘/课题备份"
python expvault.py --root "实验库" snapshots
```

Windows 将目标换为实际路径，例如 `E:\课题备份`。目标路径由自己选择，确认有可用空间。
备份保存快照和按内容去重的文件对象。日常备份不加 `--prune`；需要清理旧快照时先了解保留策略并确认已有可恢复副本。
备份只覆盖当前工作空间，不自动覆盖所有子空间与空间目录记录；完整搬家请按 README 复制整个库。

## 恢复到一个新的空目录

先查看快照，再恢复到独立目录进行核对，保留正在使用的库：

```sh
python expvault.py --root "实验库" snapshots
python expvault.py --root "实验库" restore latest --to "恢复核对"
```

多个备份目标时使用 `restore -h` 查看 `--target` 等选项，选择正确来源。
恢复内容布局取决于原库路径配置；先在新目录核对快照、实验元数据与原始文件，再按实际布局迁移，避免在目标路径重复嵌套一层 `数据/`。

误删、异常覆盖或磁盘故障时，先停止同步与外部写入，保留原目录和备份副本。不要直接使用 `clean`、
`--overwrite` 或批量删除去“修复”未知问题。

## 导出台账和旧数据整理

```sh
python expvault.py --root "实验库" export
python expvault.py --root "实验库" adopt "旧实验目录" --title "旧样品 IV 测试"
```

`adopt` 默认复制旧目录，核对后再自行整理来源；新手不要先加 `--move`。
Excel 导出使用 `openpyxl`，独立包的依赖清单已包含；源码环境缺少它时可降级导出 CSV。
需要指定文件路径、课题筛选或其他选项时，以相应命令的 `-h` 为准。
