#!/usr/bin/env python3
"""Prepare an explicitly chosen isolated IPA Board copy for Extensions verification."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess

SOURCE_COMMIT = 'a2e3bc84cc9471831e0dd049c570edd8f4d96d1f'
TOKENIZER_SHA256 = '4d28a2a61017a7b222164065d832e51103fbb3a4451c4e4938a2eeb8e83e44e8'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--checkout', required=True, type=Path, help='An isolated copy, never the primary IPA Board checkout')
parser.add_argument('--mode', required=True, choices=('capture', 'compare'))
parser.add_argument('--page-size', required=True, type=int, choices=(4096, 16384))
parser.add_argument('--process-architecture', default='x86_64', choices=('x86_64', 'arm64-v8a'))
parser.add_argument('--score-absolute-tolerance', type=float, default=0.0001)
args = parser.parse_args()
work = args.checkout.resolve()
assert str(work).replace('\\', '/').casefold() not in ('d:/ipa_board', '/mnt/d/ipa_board'), 'Refusing to modify the primary IPA Board workspace'
assert (work / '.git').exists(), 'An existing isolated Git checkout is required'
assert subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], text=True).strip() == SOURCE_COMMIT
assert 0 <= args.score_absolute_tolerance <= 0.001
root = Path(__file__).resolve().parent
fixtures = root / 'fixtures'
shutil.copyfile(fixtures / 'OnnxExtensionsBuildCompatibilityTest.kt', work / 'app/src/androidTest/java/com/example/ipa_board/OnnxExtensionsBuildCompatibilityTest.kt')
semantic = work / 'app/src/androidTest/java/com/example/ipa_board/SemanticContextIntegrationTest.kt'
if 'disableUnsupportedIpaOnlyForThisFixture' not in semantic.read_text():
    patch = fixtures / 'semantic-x86-fixture.patch'
    subprocess.run(['git', '-C', str(work), 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', '-C', str(work), 'apply', str(patch)], check=True)
aar = work / 'app/libs/onnxruntime-extensions-android-0.13.0.aar'
aar_sha = hashlib.file_digest(aar.open('rb'), 'sha256').hexdigest()
if args.mode == 'compare':
    artifact_manifest = json.loads((root.parent / 'artifacts/manifest.json').read_text())
    expected = [entry for entry in artifact_manifest['artifacts'] if entry['path'] == 'onnxruntime-extensions-android-0.13.0-ipa-e5-16kb.aar']
    assert len(expected) == 1 and aar_sha == expected[0]['sha256'], 'Isolated AAR must match the reviewed build manifest'
else:
    assert aar_sha == 'cb98c6fbeac1a9707228c4b76cfbb395b4dc4d310900aefaa672561d17fd6d9d', 'Capture requires the original AAR'
lock_path = work / 'tools/engine-assets.lock.json'
lock = json.loads(lock_path.read_text())
entries = [entry for entry in lock['files'] if entry['path'] == 'app/libs/onnxruntime-extensions-android-0.13.0.aar']
assert len(entries) == 1
entries[0]['sha256'] = aar_sha
entries[0]['bytes'] = aar.stat().st_size
lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + '\n')
assets = work / 'app/src/androidTest/assets'
assets.mkdir(parents=True, exist_ok=True)
(assets / 'onnx-extension-validation.json').write_text(json.dumps({
    'mode': args.mode, 'expectedPageSize': args.page_size,
    'expectedProcessArchitecture': args.process_architecture,
    'scoreAbsoluteTolerance': args.score_absolute_tolerance,
}, indent=2) + '\n')
if args.mode == 'compare':
    for name in ('onnx-tokenizer-baseline.json', 'onnx-e5-baseline.json'):
        data = json.loads((root / name).read_text())
        assert data['formatVersion'] == 1 and data['tokenizerSha256'] == TOKENIZER_SHA256
        shutil.copyfile(root / name, assets / name)
failure_assets = assets / 'engines/semantic'
failure_assets.mkdir(parents=True, exist_ok=True)
for name in ('tokenizer.onnx', 'centering.json'):
    shutil.copyfile(work / 'app/src/main/assets/engines/semantic' / name, failure_assets / name)
assert hashlib.sha256((failure_assets / 'tokenizer.onnx').read_bytes()).hexdigest() == TOKENIZER_SHA256
bad_model = b'Synthetic invalid ONNX model, used only by the isolated runtime failure test.\n'
(failure_assets / 'model.part0').write_bytes(bad_model)
model_sha = hashlib.sha256(bad_model).hexdigest()
(failure_assets / 'manifest.json').write_text(json.dumps({
    'version': 'onnx-test-invalid-model-v1', 'modelSha256': model_sha, 'modelSize': len(bad_model),
    'parts': [{'name': 'model.part0', 'size': len(bad_model), 'sha256': model_sha}],
    'tokenizerSha256': TOKENIZER_SHA256,
}, indent=2) + '\n')
manifest = work / 'tools/regression-suites.json'
suites = json.loads(manifest.read_text())
fixture_name = 'com.example.ipa_board.OnnxExtensionsBuildCompatibilityTest'
semantic_name = 'com.example.ipa_board.SemanticContextIntegrationTest'
for owner in ('semantic', 'full'):
    classes = suites['suites'][owner]['classes']
    if fixture_name not in classes:
        classes.append(fixture_name)
suites['suites']['onnx-extensions'] = {'classes': [fixture_name, semantic_name]}
suites['suites']['onnx-extension-runtime'] = {'classes': [fixture_name]}
suites['suites']['onnx-semantic-integration'] = {'classes': [semantic_name]}
manifest.write_text(json.dumps(suites, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'sourceCommit':SOURCE_COMMIT,'mode':args.mode,'pageSize':args.page_size,'processArchitecture':args.process_architecture,'runtimeTests':8,'semanticIntegrationTests':4}, indent=2))