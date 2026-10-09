# 编译产物预发布目录

当前引擎版本：**v0.0.2-dev**。发布目录只包含编译结果、批次说明、来源许可与校验值，不含源码、训练资料、模型、测试 APK 或构建工具。每次发布后最新目录只保留当前 AAR 与客户端批次；旧版本保存在 Git 历史提交中，可按固定提交下载或回滚。

| 目录 | 消费方式 |
| --- | --- |
| [v0.0.2-dev-client.1](v0.0.2-dev-client.1/README.md) | IPA Board 独立更新批次：配套 `classes.dex` 与 ARM64 JNI，以及裸 IPADWG01 v2 词典。客户端由根 [board-update.json](../board-update.json) 发现并校验，直接消费各资源。 |
| [v0.0.2-dev](v0.0.2-dev/release.json) | 构建时 Gradle 依赖：`ipa-engine-v0.0.2-dev-android-arm64-v8a.aar`，自带完整 24 位词典。 |

两个新包使用同一已构建 v0.0.2-dev 引擎与完整 24 位词典，目标 API 26+、arm64-v8a，入口 `com.ipaengine.graph.GraphEngine`。DEX 从相应 AAR 的 `classes.jar` 转换，JNI 必须与它配套。此次词典字节及 SHA-256 不变，客户端的独立 `dictionaryVersion` 继续为 v0.0.1-dev-lexicon。

IPA Board 不解包 AAR、不编译资料库、不扫描 VERSION 或 GitHub Releases。客户端唯一入口位于仓库根；清单路径以远程仓库根为基准，二进制下载固定在 `artifactCommit`，不能只改 AAR 版本或文件名完成更新。

维护者在完整本地项目使用 `scripts/publish_release.py`，一次命令完成产物生成、旧目录清理、入口更新与公开导出推送。脚本先发布完整资源，再用实际公开 SHA 生成并发布入口；`artifactCommit` 必须是包含全部资源的已发布公开提交。批次的 `client.json` 和 `SHA256SUMS` 不能代替根入口。维护规则及命令见 [CONTRIBUTING](../CONTRIBUTING.md)，接口、候选字段及宿主责任见根 [README](../README.md)。

[此前发布的完整目录](https://github.com/PantingEYEs/IPA_Engine/tree/707823a2aac3e848ddee0a7b66e232aa94de88e3/pre-release) 固定在历史提交；从最新目录删除不会改变该提交中的文件。

v0.0.2-dev 默认支持顺序省略及数字音高值，已完成桌面正确性/JNI 检查与 Android 交叉编译。将其转换并拆分为客户端资源属于发布修复，未新增手机性能验证；旧版实机数字不代表新版运行结果。
