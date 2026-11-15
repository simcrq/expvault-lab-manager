# 无数据独立发布包

`release/VERSION` 是独立实验管理系统的发布版本。打包只读取指定提交中的程序文件，
不复制当前工作目录，因而不会带入本机的数据、配置、索引、工作空间、备份、上传暂存、依赖或虚拟环境。
未提交的程序改动也不会进入发布包。

```sh
python tools/build_release.py --ref HEAD --output dist
# 或从已建立的版本标签重建
python tools/build_release.py --ref v1.7.0 --output dist/rebuilt
```

输出为 `expvault-lab-manager-<版本>.zip` 和同名 `.zip.sha256`。已有产物不覆盖，请换一个输出目录。
ZIP 只有一个顶层程序目录，附带 `release-manifest.json`，记录提交、版本、文件 SHA256、零初始实验和依赖未捆绑状态。
归档固定顺序、时间与权限，`.cmd` / `.ps1` 转为 CRLF，Mac `.command` 保留可执行权限；从同一提交重建得到相同摘要。

程序从 `lab_manager.py` 启动，默认自动初始化程序旁的 `实验库/`，后续只打开该库，不覆盖已有配置。
`--root` 可显式指定其他库；`--check` 不初始化库，检查发布文件完整性及必需依赖。
目标电脑需要 CPython 3.10–3.13；双击启动器在目标电脑创建 `.runtime/` 并安装 `requirements-release.txt`。
依赖不进入 ZIP。无需把开发机的 `.venv*`、`.runtime/` 或系统 Python 拷入发布包。

发布前验证打包测试，并在临时目录解压运行：

```sh
python -m unittest discover -s tests -p test_release.py -v
python -m pip install -r requirements-release.txt
python lab_manager.py --check
python lab_manager.py --no-open --port 0
```

后面三条在**临时解压目录**执行；通过输出的完整链接检查空库、工作空间和新建实验，测试后删除该临时目录。
不要将运行测试后产生数据的目录重新压缩交付，应交付构建脚本产生的原始 ZIP。

本地没有 Git remote 时，创建本地版本标签并交付 ZIP；不会自动上传第三方发布平台。
