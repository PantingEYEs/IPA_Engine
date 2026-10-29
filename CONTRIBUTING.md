# 源码仓库维护约定

本仓库对应完整开发项目的 `release/remote/` 子目录，只提供 README、CONTRIBUTING
及逐文件白名单内的 `source/`。原始数据、运行时词典、模型参数、私有工具、完整开发历史
和二进制包不进入新的公开提交。测试使用的小型合成词典属于源码 fixture。
旧版本 tag、Git 历史和已发布附件不改写。

`develop` 用于开发源码；`main` 通过 `--no-ff` 合并接收源码。两分支均不提供
客户端更新入口、VERSION 或 pre-release 文件夹。禁止推送完整私有项目历史、强推或 mirror。
公开同步在完整本地项目运行 `release/scripts/release/publish_sources.py`；
构建报告和发布脚本仅留在完整项目，不属于 source 白名单。

客户端版本和安装资源统一从 GitHub Releases 获取，合同见根 README。
`vX.Y.Z-dev` 对应 Pre-release，`vX.Y.Z` 对应正式 Release；附件合同一致。
现有版本附件不可覆盖，新增版本使用新 tag。引擎与词典版本独立，未变词典按哈希复用。
当前未选定项目级软件许可证；数据按每版随附来源声明使用。
