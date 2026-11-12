# IPA Board 接入说明

验证基线：IPA Board `a2e3bc84cc9471831e0dd049c570edd8f4d96d1f`。本分支只交付引擎构建包，没有修改或提交 IPA Board 的产品代码。

1. 将新 AAR 放入应用库目录，并移除旧 `onnxruntime-extensions-android-0.13.0.aar`；Gradle 只保留一份 Extensions 依赖，避免重复 Java 类及 JNI 库。
2. 保留 ONNX Runtime Android 1.23.2、原 E5 模型分块、`tokenizer.onnx` 与 `centering.json`。不修改分词编号映射、模型输入名、池化或排序策略。
3. 更新 `tools/engine-assets.lock.json` 中对应 AAR 的来源与摘要。Engine Management 的来源信息应标明官方 0.13.0 源码、IPA Engine `ranking` 构建、SPM-only 与 16KB 构建信息；不要把它称为完整算子包或新的 E5 模型。
4. `SemanticEngine` 仍通过 `OrtxPackage.getLibraryPath()` 注册 custom-op library，无需换 Java 包名、JNI 文件名或为分词添加新 API。AAR 包含两个 ABI 的两份库，应整包替换。
5. 继续默认关闭语义排序。保留普通候选先显示、异步过期结果拒绝、关闭后解绑、失败沿用普通候选，以及只在 RAM 缓存向量的现有行为。
6. 按 IPA Board 的保护入口跑分词／E5／语义集成专项，再按产品需要更新独立预览版。本次只使用隔离副本完成兼容测试，不对手机安装包做替换。

本包没有模型词库更新，因此没有必要删除现有用户数据或强制重新部署相同 E5 模型。后续更换模型时，应继续采用校验摘要、临时文件和原子替换的现有部署路径。

`onnxruntime-1.23.2-headers/` 是从锁定官方 AAR 提取的原始 API 头；它们供 Extensions 编译，不能替代 Android 运行库。`ipa-board-resources.lock.json` 记录对照资源，不代表这些资源在当前分支分发。

当前没有需要向用户开放的新底层开关。算子裁剪、线程数、JNI 装载和内存页对齐属于实现配置；现有语义排序开关继续承担用户选择入口。
