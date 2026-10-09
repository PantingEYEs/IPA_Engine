# 发布仓库维护约定

此 GitHub 仓库只对应完整本地项目的 `remote/` 子目录，发布说明、更新清单与编译结果。源码、训练权重、原始词典、构建缓存、工具和完整项目历史留在本地。克隆此仓库可以检查发布产物；源码构建需要完整开发项目。

## 一条命令发布

在完整本地项目根目录运行，填写实际 AAR、版本及提交描述：

~~~sh
python3 scripts/publish_release.py \
  --aar data/android/ipa-graph-full.aar \
  --version v0.0.3-dev \
  --message 'Release v0.0.3-dev'
~~~

调用前自行提交源码等非 `remote/` 改动；脚本只暂存发布目录。它生成 AAR 目录、默认 `{version}-client.1` 批次、索引与校验记录，清理旧产物目录，更新 `board-update.json`，校验并推送 `remote/` 的公开导出历史。同版本另发批次用 `--batch`；代理默认取 macOS 系统配置，也可用 `--git-proxy` 指定。上述工具源码仅在完整开发项目中提供。

脚本内部自动完成两次提交和推送：先发布完整资源，取得真实的公开 SHA，再用该 SHA 生成入口的 `artifactCommit` 并发布入口。调用者无需手动打包、导出、填 SHA 或分阶段推送。若产物发布失败，原入口仍引用旧资源。

## 客户端合同与历史

IPA Board 固定读取 `codex/ipa-engine-v0.0.1-dev` 分支的根 [board-update.json](board-update.json)。分支名不随引擎版本改变；客户端按清单下载配套 `classes.dex`、`libipa_graph_android.so` 及裸 `lexicon.ipad`（IPADWG01 v2）。AAR 用于宿主构建时集成，不能单靠上传 AAR 完成客户端更新。最新产物见 [预发布索引](pre-release/README.md)，字段与下载合同见根 [README](README.md) 的“IPA Board 独立资源更新”。

最新目录只保留当前 AAR 和客户端批次，旧目录的删除记录纳入 Git。已发布资源在历史提交中保持不可变，旧入口仍可从它固定的 `artifactCommit` 下载；回滚从相应 Git 提交恢复。引擎与词典版本独立，词典字节未变时保持原词典版本。引擎更新须配套发布 DEX/JNI，并保持 GraphEngine 接口、候选合同及已安装词典兼容；新词典须兼容仍在使用的旧引擎。

## Git 分支与推送范围

本地完整项目分支和远程客户端分支都叫 `codex/ipa-engine-v0.0.1-dev`，但对应不同历史。发布脚本只推送本地 `codex/remote-v0.0.1-dev` 到 `public/codex/ipa-engine-v0.0.1-dev`，导出树及全部历史都必须仅含公开文件。禁止推送完整项目分支、force、`--all` 或 `--mirror`。历史映射、远程分叉或校验异常时先排查，不盲目迁移或覆盖历史。完整维护说明在本地 `docs/远程发布流程.md`。

开发发布不创建或更新 `main`。将来向远程 `main` 合并时，在公开仓库使用 `git merge --no-ff codex/ipa-engine-v0.0.1-dev`，或在 GitHub 选择 **Create a merge commit**；不使用 fast-forward、squash 或 rebase。
