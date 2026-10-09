# IPA Board development update artifacts

These are the unchanged Java/JNI engine and compressed binary dictionary from the v0.0.1-dev ARM64 AAR. classes.jar was converted to classes.dex with Android SDK D8 36.1.0, min API 26. Source package commit: 53eed9518f932934a28aceb4e03a453d545f80b8.

IPA Board can update engine code/native files and the developer-compiled dictionary independently. The client does not compile lexical sources. To publish a dictionary-only update, use a new immutable directory and update dictionaryVersion, dictionaryPath, dictionaryBytes, dictionarySha256 and dictionaryKeys in board-update.json; keep the existing engine fields unchanged. The new dictionary must open with the old installed engine.

client.json declares the schema, entry point, ABI, version strings, paths, sizes and SHA-256 values. SHA256SUMS covers the executable and dictionary artifacts. Preserve source-specific notices in lexicon.manifest.json and THIRD_PARTY_NOTICES.json. No new blanket license is assigned.
