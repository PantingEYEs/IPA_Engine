#!/usr/bin/env python3
"""Record completed CMake link/source evidence; run after final packaging."""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
from collections import defaultdict
from functools import lru_cache
from pathlib import Path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--build', type=Path, required=True)
    p.add_argument('--ndk', type=Path, required=True)
    a = p.parse_args()
    root, build, ndk = a.root.resolve(), a.build.resolve(), a.ndk.resolve()
    out = root / 'licenses/evidence'
    out.mkdir(parents=True, exist_ok=True)
    substitutions = [(str(ndk), '${NDK_ROOT}'), (str(build), '${BUILD_ROOT}'), (str(root), '${SOURCE_ROOT}')]

    def portable(s):
        for old, new in substitutions:
            s = s.replace(old, new)
        return s

    @lru_cache(maxsize=None)
    def resolve_file(path):
        resolved = path.resolve()
        return resolved if resolved.is_file() else None

    def record_text(name, text):
        target = out / name
        target.write_text(portable(text), encoding='utf-8')
        return {'file': 'licenses/evidence/' + name, 'sha256': sha256(target),
                'transformation': 'absolute build, source and NDK roots replaced with placeholders'}

    lock = json.loads((root / 'sources.lock.json').read_text())
    report = {'schemaVersion': 1, 'status': 'verified_for_recorded_native_artifacts',
              'scope': 'Build-input and notice inventory, not a formal SPDX/CycloneDX SBOM or legal conclusion',
              'method': 'Inspect actual completed CMake object files, dependency files, link commands, linker maps, ELF dynamic sections and delivered library hashes; no relink performed',
              'upstreamCommit': lock['upstream']['commit'], 'sourcesLockSha256': sha256(root / 'sources.lock.json'),
              'dependencies': {}, 'licenseComparisons': [], 'abis': []}
    for name in ('spm', 'protobuf', 'gsl'):
        directory = root / '.work/deps' / name
        commit = subprocess.check_output(['git', '-C', str(directory), 'rev-parse', 'HEAD'], text=True).strip()
        if commit != lock['dependencies'][name]['commit']:
            raise ValueError(f'{name} commit differs from source lock')
        report['dependencies'][name] = {'commit': commit, 'tag': lock['dependencies'][name]['tag'],
                                          'patches': lock['dependencies'][name]['patches']}
    notices = json.loads((root / 'licenses/sources.json').read_text())
    for entry in notices['files']:
        actual = sha256(root / 'licenses' / entry['file'])
        if actual != entry['sha256']:
            raise ValueError('Notice hash mismatch: ' + entry['file'])
        source = entry.get('localSource')
        if source:
            source_path = Path(source.replace('${SOURCE_ROOT}', str(root)).replace('${NDK_ROOT}', str(ndk)))
            if sha256(source_path) != actual:
                raise ValueError('Copied notice differs from build source: ' + entry['file'])
        report['licenseComparisons'].append({'file': 'licenses/' + entry['file'], 'sha256': actual,
                                            'matchesBuildSource': True if source else None})

    readelf = ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf'
    allowed_roots = (root, ndk, build)
    for abi in lock['abis']:
        base = build / abi
        entries = json.loads((base / 'compile_commands.json').read_text())
        compiled, headers = [], set()
        for entry in entries:
            command = shlex.split(entry['command'])
            obj = Path(entry['directory']) / command[command.index('-o') + 1]
            if not obj.is_file():
                continue  # CMake also describes disabled/unbuilt training/protoc targets.
            src = Path(entry['file']).resolve()
            compiled.append({'source': portable(str(src)), 'sourceSha256': sha256(src),
                             'object': portable(str(obj.resolve()))})
            depfile = Path(str(obj) + '.d')
            if not depfile.is_file():
                raise FileNotFoundError('Missing compiler dependency file: ' + str(depfile))
            deps = depfile.read_text().replace('\\\n', ' ').split()[1:]
            for token in deps:
                if token == '\\':
                    continue
                dep = Path(token)
                if not dep.is_absolute():
                    dep = Path(entry['directory']) / dep
                dep = resolve_file(dep)
                if dep is not None and dep != src:
                    if not any(dep.is_relative_to(r) for r in allowed_roots):
                        raise ValueError('Unexpected dependency root: ' + str(dep))
                    headers.add(dep)
        selected_headers = [{'file': portable(str(x)), 'sha256': sha256(x)} for x in sorted(headers)
                            if x.is_relative_to(root)]
        compiled.sort(key=lambda x: (x['source'], x['object']))
        compiled_file = out / f'{abi}-compiled-inputs.json'
        compiled_file.write_text(json.dumps({'compiledSources': compiled, 'projectAndDependencyHeaders': selected_headers,
                                             'ndkOrGeneratedHeaderCount': len(headers) - len(selected_headers)}, indent=2) + '\n')
        abi_report = {'abi': abi, 'compiledSourceCount': len(compiled),
                      'projectAndDependencyHeaderCount': len(selected_headers),
                      'inputsEvidence': 'licenses/evidence/' + compiled_file.name,
                      'inputsEvidenceSha256': sha256(compiled_file), 'libraries': []}
        header_names = [portable(str(x)) for x in headers]
        forbidden = ('/third_party/esaxx/', '/third_party/protobuf-lite/', '/third_party/utf8_range/')
        unexpected = [n for n in header_names if any(x in n for x in forbidden)]
        if unexpected:
            raise ValueError('Unexpected disabled dependency headers: ' + str(unexpected))
        abi_report['disabledDependencyHeadersAbsent'] = list(forbidden)
        for target, filename, link_rel in (
            ('extensions_shared', 'libortextensions.so', 'upstream/CMakeFiles/extensions_shared.dir/link.txt'),
            ('onnxruntime_extensions4j_jni', 'libonnxruntime_extensions4j_jni.so', 'CMakeFiles/onnxruntime_extensions4j_jni.dir/link.txt')):
            link = base / link_rel
            map_path = base / (target + '.map')
            map_text = map_path.read_text()
            groups = defaultdict(set)
            for archive, member in set(re.findall(r'(\S+\.a)\(([^)]+)\)', map_text)):
                archive_path = Path(archive)
                if not archive_path.is_absolute():
                    archive_path = link.parent.parent.parent / archive_path
                archive_path = resolve_file(archive_path)
                if archive_path is None:
                    raise FileNotFoundError('Link-map archive not found: ' + str(archive_path))
                groups[portable(str(archive_path))].add(member)
            if not any('libsentencepiece.a' in key for key in groups):
                raise ValueError('SentencePiece archive missing in ' + str(map_path))
            library = {'file': filename, 'linkCommand': record_text(f'{abi}-{target}-link.txt', link.read_text()),
                       'originalLinkCommandSha256': sha256(link), 'originalLinkMapSha256': sha256(map_path),
                       'contributingArchives': [{'archive': key, 'members': sorted(values), 'memberCount': len(values)} for key, values in sorted(groups.items())]}
            artifact = root / 'artifacts' / abi / filename
            if not artifact.exists():
                raise FileNotFoundError('Final packaged native artifact not found: ' + str(artifact))
            library['artifact'] = str(artifact.relative_to(root))
            library['artifactSha256'] = sha256(artifact)
            dynamic = subprocess.check_output([str(readelf), '-d', str(artifact)], text=True)
            library['neededLibraries'] = re.findall(r'Shared library: \[([^\]]+)\]', dynamic)
            library['elfDynamicEvidence'] = record_text(f'{abi}-{target}-dynamic.txt', dynamic)
            abi_report['libraries'].append(library)
        report['abis'].append(abi_report)
    (out / 'dependency-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'abis': len(report['abis']), 'compiledSources': [x['compiledSourceCount'] for x in report['abis']],
                      'noticeCount': len(report['licenseComparisons'])}))


if __name__ == '__main__':
    main()


