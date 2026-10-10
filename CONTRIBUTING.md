# 发布仓库维护约定

此 GitHub 仓库只对应完整本地项目的 `release/remote/` 子目录。`source/` 是可独立构建 Rust 引擎、运行 CLI 和引擎测试的精简项目快照，包含唯一必要的编译后 24 位运行时词典及 manifest；`pre-release/` 是客户端消费的发布资源。两处可有意包含相同词典字节，但职责不同。未处理的原始数据、观测数据库、采集目录、编译前词典、训练权重、私有报告、构建缓存和完整项目历史留在本地。

源码同步和 Git 导出共用逐文件白名单，不按文件扩展名自动放行。source 白名单仅允许引擎、Android 包装层、CLI、必要测试/fixture、文档，以及 `data/dictionary/compiled/lexicon.ipad` 和 `lexicon.manifest.json`。不得加入资料采集或预处理、编译前词典数据/工具、GRU 训练或预训练参数、发布脚本、数据库、真实来源抽样用例、构建结果或报告。新增运行时依赖须逐项审查并加入白名单；不允许符号链接或从原始数据库导出测试语料到源码树。同步会核验词典完整性、字节数和 SHA-256，并从公开 manifest 移除本地源数据库路径。Rust 是唯一引擎实现，旧 C++ 引擎已从公开源码移除。公开源码不代表项目已经选定软件许可证；当前不授予项目级许可证。

## 一条命令发布

先运行 `python3 engine_android/scripts/build_android_library.py --output-dir data/engine_android/builds/24`，再在完整本地项目根目录发布这些独立产物：

~~~sh
python3 release/scripts/release/publish_release.py \
  --classes-jar data/engine_android/builds/24/classes.jar \
  --native data/engine_android/builds/24/libipa_graph_android.so \
  --dictionary data/engine_android/builds/24/lexicon.ipad \
  --dictionary-manifest data/engine_android/builds/24/lexicon.manifest.json \
  --build-report data/engine_android/builds/24/build-report.json \
  --version v0.0.3-dev \
  --message 'Release v0.0.3-dev'
~~~

调用前自行提交源码等非 `release/remote/` 改动；脚本只暂存发布目录。它接收独立的 Java `classes.jar`、ARM64 JNI 库、编译词典、manifest 和构建报告，使用 Android SDK D8 生成 DEX；根据报告验证产物哈希与 Rust 运行时源码摘要，自动同步 `source/`、生成默认 `{version}-client.1` 批次和版本元数据，清理旧产物目录，更新 `board-update.json`，校验并推送 `release/remote/` 的公开导出历史。脚本不接收或打包 AAR。发布工具仅保留在完整本地项目，不包含于 source 快照。同版本另发批次用 `--batch`；代理默认取 macOS 系统配置，也可用 `--git-proxy` 指定。

脚本内部自动完成两次提交和推送：先发布完整资源，取得真实的公开 SHA，再用该 SHA 生成入口的 `artifactCommit` 并发布入口。调用者无需手动打包、导出、填 SHA 或分阶段推送。若产物发布失败，原入口仍引用旧资源。

## 客户端合同与历史

IPA Board 固定读取 `codex/ipa-engine-v0.0.1-dev` 分支的根 [board-update.json](board-update.json)。分支名不随引擎版本改变；客户端按清单下载配套 `classes.dex`、`libipa_graph_android.so` 及裸 `lexicon.ipad`（IPADWG01 v2）。最新产物见 [预发布索引](pre-release/README.md)，字段与下载合同见根 [README](README.md) 的“IPA Board 独立资源更新”。

最新目录只保留当前版本元数据和客户端批次，旧目录的删除记录纳入 Git。已发布资源在历史提交中保持不可变，旧入口仍可从它固定的 `artifactCommit` 下载；回滚从相应 Git 提交恢复。引擎与词典版本独立，词典字节未变时保持原词典版本。引擎更新须配套发布 DEX/JNI，并保持 GraphEngine 接口、候选合同及已安装词典兼容；新词典须兼容仍在使用的旧引擎。

## Git 分支与推送范围

本地完整项目分支和远程客户端分支都叫 `codex/ipa-engine-v0.0.1-dev`，但对应不同历史。发布脚本只推送本地 `codex/remote-v0.0.1-dev` 到 `public/codex/ipa-engine-v0.0.1-dev`，导出树及全部历史都必须仅含公开文件。禁止推送完整项目分支、force、`--all` 或 `--mirror`。历史映射、远程分叉或校验异常时先排查，不盲目迁移或覆盖历史。完整维护说明在本地 `release/docs/远程发布流程.md`。

开发发布不创建或更新 `main`。将来向远程 `main` 合并时，在公开仓库使用 `git merge --no-ff codex/ipa-engine-v0.0.1-dev`，或在 GitHub 选择 **Create a merge commit**；不使用 fast-forward、squash 或 rebase。
