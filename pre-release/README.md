# 编译产物预发布目录

当前引擎版本：**v0.0.2-dev**。发布目录只包含编译结果、批次说明、来源许可与校验值，不含源码、训练资料、模型、测试 APK 或构建工具。全部历史目录不可变，修改资源使用新批次，已有包不覆盖或删除。

| 目录 | 消费方式 |
| --- | --- |
| [v0.0.2-dev-client.1](v0.0.2-dev-client.1/README.md) | IPA Board 独立更新批次：配套 `classes.dex` 与 ARM64 JNI，以及裸 IPADWG01 v2 词典。客户端由根 [board-update.json](../board-update.json) 发现并校验，直接消费各资源。 |
| [v0.0.2-dev](v0.0.2-dev/release.json) | 构建时 Gradle 依赖：`ipa-engine-v0.0.2-dev-android-arm64-v8a.aar`，自带完整 24 位词典。 |
| [v0.0.1-dev-client.1](v0.0.1-dev-client.1/README.md) | 历史客户端批次，继续保留。 |
| [v0.0.1-dev](v0.0.1-dev/release.json) | 历史严格模式 AAR，已有实机报告。 |

两个新包使用同一已构建 v0.0.2-dev 引擎与完整 24 位词典，目标 API 26+、arm64-v8a，入口 `com.ipaengine.graph.GraphEngine`。DEX 从相应 AAR 的 `classes.jar` 转换，JNI 必须与它配套。此次词典字节及 SHA-256 不变，客户端的独立 `dictionaryVersion` 继续为 v0.0.1-dev-lexicon。

IPA Board 不解包 AAR、不编译资料库、不扫描 VERSION 或 GitHub Releases。客户端唯一入口位于仓库根；清单路径以远程仓库根为基准，二进制下载固定在 `artifactCommit`，不能只改 AAR 版本或文件名完成更新。

发布遵守“完整产物先推送、入口另一次提交后推送”的两阶段顺序；`artifactCommit` 必须是包含全部资源的已发布公开提交。批次的 `client.json` 是资源元数据模板，`SHA256SUMS` 是校验记录，均不能代替根入口。维护规则见 [CONTRIBUTING](../CONTRIBUTING.md)，接口、候选字段及宿主责任见根 [README](../README.md)。

v0.0.2-dev 默认支持顺序省略及数字音高值，已完成桌面正确性/JNI 检查与 Android 交叉编译。将其转换并拆分为客户端资源属于发布修复，未新增手机性能验证；旧版实机数字不代表新版运行结果。
