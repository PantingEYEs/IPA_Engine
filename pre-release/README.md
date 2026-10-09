# 编译依赖库预发布产物

当前版本：**v0.0.2-dev**。`v0.0.2-dev/` 只包含编译后的 Android AAR、发布元数据、来源许可记录与 SHA-256 校验值，不含源代码、训练数据、模型、测试 APK 或构建工具。

将 `v0.0.2-dev/ipa-engine-v0.0.2-dev-android-arm64-v8a.aar` 复制到输入法工程的 `app/libs/` 并添加本地 Gradle 依赖。AAR 自带完整 24 位词典，目标 Android API 26+、`arm64-v8a`。调用入口为 `com.ipaengine.graph.GraphEngine`。

完整调用方式、候选字段和宿主责任直接写在发布目录根 [README](../README.md) 中。发布文件信息见版本目录的 `release.json`，校验值见 `SHA256SUMS`；数据来源及许可记录见 `THIRD_PARTY_NOTICES.json`。

预发布版本目录不覆盖已有不同内容。本地开发项目的 `scripts/package_pre_release.py` 从已有构建及验证报告包装结果，新版明确标记桌面验证范围，不重新运行模型或手机测试。

v0.0.1-dev 保留作为已完成实机验证的严格模式历史包；v0.0.2-dev 默认支持顺序省略与数字音高值，新版只完成桌面正确性/JNI检查和Android交叉编译，未复测手机性能。
