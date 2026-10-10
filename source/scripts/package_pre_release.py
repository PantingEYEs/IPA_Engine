"""Package verified Android results, without source or test APKs, for UI developers."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
AAR_CONTENTS = {
    'AndroidManifest.xml', 'classes.jar', 'proguard.txt',
    'jni/arm64-v8a/libipa_graph_android.so',
    'assets/ipa/lexicon.ipad', 'assets/ipa/lexicon.manifest.json',
}


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1048576), b''):
            value.update(block)
    return value.hexdigest()


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aar', type=Path, default=ROOT / 'data/android/24/ipa-graph-full.aar')
    parser.add_argument('--version', default=(ROOT / 'remote/VERSION').read_text().strip())
    parser.add_argument('--validation-report', type=Path,
                        help='explicit desktop validation report for a new engine build; never reuses old device measurements')
    args = parser.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+-dev', args.version):
        parser.error('development version must have the form v0.0.1-dev')
    source = args.aar.resolve()
    native_report = json.loads((ROOT / 'data/reports/graph-s21-comparison.json').read_text())
    artifact_hash = sha256(source)
    if args.validation_report:
        if args.version == 'v0.0.1-dev':
            parser.error('v0.0.1-dev must retain its original device validation')
        report_path = args.validation_report.resolve()
        report = json.loads(report_path.read_text())
        cases = report.get('desktop_cases', {})
        total = cases.get('total') if isinstance(cases, dict) else None
        if (report.get('status') != 'passed'
                or report.get('aar_sha256') != artifact_hash
                or not isinstance(total, int) or isinstance(total, bool) or total <= 0
                or cases.get('passed') != total or cases.get('failed') != 0
                or report.get('android_device_execution', False) is not False):
            parser.error('new AAR must match a passed desktop report with all cases passed and no device-execution claim')
        validation = {
            'scope': 'desktop',
            'android_device_execution': False,
            'desktop_cases': cases,
            'report_sha256': sha256(report_path),
            'note': 'Desktop checks of the upgraded core; the report binds this AAR and dictionary. Android device execution and performance have not been validated for this build.',
        }
    else:
        if args.version != 'v0.0.1-dev':
            parser.error('new versions require --validation-report; original device measurements certify v0.0.1-dev only')
        wrapper_report = json.loads((ROOT / 'data/reports/android-wrapper-probe.json').read_text())
        if (native_report['status'] != 'passed' or wrapper_report['status'] != 'passed'
                or not wrapper_report['android_device_execution']
                or wrapper_report['build']['aar_sha256'] != artifact_hash):
            parser.error('AAR must match the completed 24-bit device validation report; new builds require --validation-report')
        wrapper = wrapper_report['device']
        validation = {
            'device': wrapper['model'], 'sdk': wrapper['sdk_int'],
            'native_mapping_cases': native_report['correctness']['cases'],
            'native_queries': native_report['benchmarks']['summary']['24']['total_iterations'],
            'native_p95_ms': native_report['benchmarks']['summary']['24']['median_of_round_p95_ms'],
            'wrapper_queries': wrapper['wrapper_benchmark']['iterations'],
            'wrapper_p95_ms': wrapper['wrapper_benchmark']['p95_ms'],
            'paced_soak_seconds': wrapper['wrapper_soak']['requested_seconds'],
            'paced_soak_queries': wrapper['wrapper_soak']['queries'],
            'note': 'Previous completed measurements, not rerun by this packager; no IME UI integration or long-term power qualification.',
        }
    with zipfile.ZipFile(source) as archive:
        if set(archive.namelist()) != AAR_CONTENTS:
            parser.error('unexpected AAR contents: only compiled library and dictionary assets are allowed')
        manifest = json.loads(archive.read('assets/ipa/lexicon.manifest.json'))
        with archive.open('assets/ipa/lexicon.ipad') as dictionary:
            header = dictionary.read(108)
            if header[:8] != b'IPADWG01' or struct.unpack_from('<I', header, 8)[0] != 2:
                parser.error('release uses the selected 24-bit dictionary format only')
            digest = hashlib.sha256(header)
            count = len(header)
            for block in iter(lambda: dictionary.read(1048576), b''):
                count += len(block)
                digest.update(block)
        if (not manifest.get('complete') or manifest.get('status') != 'complete'
                or count != manifest['bytes'] or digest.hexdigest() != manifest['sha256']
                or native_report['artifacts']['24']['sha256'] != manifest['sha256']):
            parser.error('complete dictionary assets must match the verified source manifest')
        if args.validation_report and report.get('dictionary_sha256') != manifest['sha256']:
            parser.error('desktop validation report must bind the embedded dictionary SHA-256')
    parent = ROOT / 'remote/pre-release'
    parent.mkdir(exist_ok=True)
    destination = parent / args.version
    filename = f'ipa-engine-{args.version}-android-arm64-v8a.aar'
    with tempfile.TemporaryDirectory(prefix='.package-', dir=parent) as temporary:
        stage = Path(temporary)
        shutil.copyfile(source, stage / filename)
        save_json(stage / 'THIRD_PARTY_NOTICES.json', {
            'version': args.version,
            'scope': 'Source-specific dictionary and frequency notices retained from the compiled manifest; no blanket data license is assigned.',
            'sources': manifest['sources'],
        })
        save_json(stage / 'release.json', {
            'version': args.version,
            'artifact': filename,
            'artifact_bytes': source.stat().st_size,
            'artifact_sha256': artifact_hash,
            'platform': 'Android', 'abi': 'arm64-v8a', 'min_sdk': 26,
            'java_language_level': 8,
            'entry_point': 'com.ipaengine.graph.GraphEngine',
            'includes_sources': False,
            'includes_test_apks': False,
            'includes_dictionary': True,
            'dictionary': {
                'format': 'IPADWG01', 'format_version': 2, 'index_bits': 24,
                'bytes': count, 'sha256': manifest['sha256'],
                'ipa_keys': manifest['ipa_keys'], 'unique_texts': manifest['unique_texts'],
                'pronunciation_postings': manifest['pronunciation_postings'],
                'regions': ['en-US', 'en-GB', 'zh-CN', 'zh-TW', 'ja-JP'],
            },
            'api_documentation': '../../README.md',
            'source_notices': 'THIRD_PARTY_NOTICES.json',
            'validation': validation,
        })
        (stage / 'SHA256SUMS').write_text(''.join(
            f'{sha256(path)}  {path.name}\n' for path in sorted(stage.iterdir())), encoding='utf-8')
        if destination.exists():
            # Published version directories are immutable. Identical packaging is idempotent.
            if (set(p.name for p in destination.iterdir()) != set(p.name for p in stage.iterdir())
                    or any(sha256(destination / p.name) != sha256(p) for p in stage.iterdir())):
                parser.error('version already exists with different contents; choose a new version')
        else:
            stage.chmod(0o755)
            stage.rename(destination)
    print(json.dumps({'version': args.version, 'directory': str(destination),
                      'artifact': filename, 'bytes': source.stat().st_size,
                      'sha256': artifact_hash, 'includes_sources': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
