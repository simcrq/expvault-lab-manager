"""expvault —— 半导体/材料实验数据管理。

模块划分：
    util       通用工具（哈希、原子写、终端表格）
    config     配置与运行上下文
    db         SQLite 索引（可从 meta.json 重建）
    experiment 实验记录的创建、登记、扫描、校验
    backup     内容寻址快照备份
    report     Excel / HTML 报表、统计、体检
    issue      设备与工艺问题记录
    importer   后台复制、校验、去重与文件标签（不依赖 GUI）
    phone_import 临时局域网手机上传与待导入暂存
    gui        可选 Qt 桌面导入插件
    web        本机 / 局域网网页管理 HTTP 服务与浏览器界面
    web_service 网页操作、后台任务、上传暂存与持久化结果
    cli        命令行入口
"""

__version__ = "1.0.0"
