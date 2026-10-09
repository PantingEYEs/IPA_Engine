# 发布仓库维护约定

此 GitHub 仓库对应完整本地项目的 `remote/` 子目录，只发布说明、更新清单与编译结果。源码、训练权重、原始词典、构建缓存、工具和完整项目历史留在本地。克隆此仓库可以检查发布产物；源码构建及 CLI 需要完整开发项目。

## 客户端发布合同

IPA Board 固定读取 `codex/ipa-engine-v0.0.1-dev` 分支的根目录 [board-update.json](board-update.json)。分支名不随引擎版本改变，`VERSION` 与 GitHub Releases 均不是客户端发现入口。AAR 用于宿主构建时集成；客户端更新需单独提供匹配的 `classes.dex`、`libipa_graph_android.so` 及裸 `lexicon.ipad`（IPADWG01 v2），不能只上传 AAR。

每次产物变更建立新的不可变批次，例如 `pre-release/v0.0.2-dev-client.1/`。不要覆盖或删除历史目录。引擎与词典版本独立；未变更文件可以继续引用已经发布的旧目录。引擎更新必须成套发布 DEX/JNI，并保持现有 GraphEngine 接口、候选合同及已安装词典兼容；新词典必须兼容仍在使用的旧引擎。

按两个独立提交发布：

1. 先提交并推送完整批次。验证所有格式、路径、大小、SHA-256 和来源许可，取得实际已推送的公开提交 40 位 SHA。
2. 再生成并单独提交、推送根 `board-update.json`，把前一步的公开 SHA 填为 `artifactCommit`。清单引用的全部文件都必须存在于该提交。不能填完整本地项目提交，也不能提前引用尚未推送的文件。

`client.json` 记录批次字段；客户端只消费根入口。入口的必需字段、字节上限、版本及下载行为见根 [README](README.md) 的“IPA Board 独立资源更新”。发布失败时原入口继续引用旧资源，待产物发布成功后再更新。

## Git 历史及分支

本地完整项目的开发分支和 GitHub 开发分支虽然都叫 `codex/ipa-engine-v0.0.1-dev`，对应不同历史。只推送本地公开导出分支，完整项目不设置发布仓库的 upstream：

~~~sh
git subtree split --prefix=remote --rejoin --branch=codex/remote-v0.0.1-dev
python3 scripts/verify_public_export.py
git push public refs/heads/codex/remote-v0.0.1-dev:refs/heads/codex/ipa-engine-v0.0.1-dev
~~~

这些命令在完整本地项目根执行，且先取得并整合远程改动。`verify_public_export.py` 确认工作区干净、导出树等于 `HEAD:remote`、远程开发提交是导出分支祖先、全部导出历史仅含公开编译文件与说明。项目已有正确 rejoin 映射，日常增量 `split --rejoin` 会继承公开历史，并继续记录完整项目与公开提交的对应关系。

`--onto` 属于尚未建立映射时连接外来历史的一次性迁移参数，本项目正常发布不用它。与已有 rejoin 叠加可能重复登记缓存并失败。遇到这种错误或映射不明，先停止推送并核对历史，不盲目重用 `--onto`、删除缓存或强推；需要迁移时在独立候选分支中核验，而非修改日常命令。

遇到分叉先按 `git subtree merge --prefix=remote` 将远程改动放回子目录，修复冲突，再导出；不要使用 force、`--all`、`--mirror`，也不要直接推送完整项目开发分支。完整的本地维护流程保存在开发项目 `docs/远程发布流程.md`。本仓库不发布上述工具源码。

开发更新不直接推送到 `main`。将来合并到远程已有 main 时，在发布仓库 checkout 中保留合并提交：

~~~sh
git switch main
git merge --no-ff codex/ipa-engine-v0.0.1-dev
~~~

GitHub PR 使用 **Create a merge commit**，不使用 squash 或 rebase。首次建立 main 和每次合并均单独进行；开发发布不会自动创建或更新它。
