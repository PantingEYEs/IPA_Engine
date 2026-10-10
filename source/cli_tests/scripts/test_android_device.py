"""Compare graph index widths using the native CLI on a real ADB device.

No desktop fallback. This does not test Java assets, JNI, or the IME service.
"""
from __future__ import annotations
import argparse
import collections
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
REMOTE = '/data/local/tmp/ipa-graph-test'
REGIONS = ('en-US', 'en-GB', 'zh-CN', 'zh-TW', 'ja-JP')


class DeviceTestError(RuntimeError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as incoming:
        for block in iter(lambda: incoming.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_report(path, report):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


class Adb:
    def __init__(self, executable=None, serial=None, timeout=60):
        found = None
        if executable is not None:
            found = shutil.which(str(executable))
            if not found and Path(executable).is_file():
                found = str(Path(executable).resolve())
        else:
            # Configured and conventional SDK installations precede PATH.
            sdk_roots = [Path(value) for key in ('ANDROID_SDK_ROOT', 'ANDROID_HOME')
                         if (value := os.environ.get(key))]
            sdk_roots += [Path.home() / 'Library/Android/sdk', Path.home() / 'Android/Sdk']
            if os.environ.get('LOCALAPPDATA'):
                sdk_roots.append(Path(os.environ['LOCALAPPDATA']) / 'Android/Sdk')
            filename = 'adb.exe' if os.name == 'nt' else 'adb'
            found = next((str(path.resolve()) for root in sdk_roots
                          if (path := root / 'platform-tools' / filename).is_file()), None)
            found = found or shutil.which('adb')
        if not found:
            raise DeviceTestError('ADB executable not found in the SDK installations or PATH; provide --adb PATH')
        self.executable, self.serial, self.timeout = found, serial, timeout

    def run(self, arguments, input_text=None, check=True, selected=True, timeout=None):
        command = [self.executable]
        if selected and self.serial:
            command += ['-s', self.serial]
        command += list(arguments)
        started = time.perf_counter()
        try:
            result = subprocess.run(command, input=input_text, encoding='utf-8', errors='replace',
                                    capture_output=True, timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired as error:
            raise DeviceTestError(f'ADB command exceeded {timeout or self.timeout}s: {shlex.join(command)}') from error
        except OSError as error:
            raise DeviceTestError(f'Cannot execute ADB: {error}') from error
        result.wall_ms = (time.perf_counter() - started) * 1000
        if check and result.returncode:
            raise DeviceTestError(f'ADB command failed ({result.returncode}): {shlex.join(command)}\n'
                                  + (result.stderr or result.stdout).strip()[-4000:])
        return result

    def shell(self, command, **kwargs):
        return self.run(['shell', '-T', command], **kwargs)

    def select_device(self):
        result = self.run(['devices', '-l'], selected=False, timeout=min(self.timeout, 15))
        devices = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] != 'List':
                devices[fields[0]] = fields[1]
        if self.serial:
            if self.serial not in devices:
                raise DeviceTestError(f'Requested device {self.serial} is not connected; devices: {devices}')
            state = devices[self.serial]
            if state != 'device':
                action = ' Unlock the phone and accept its USB debugging RSA prompt.' if state == 'unauthorized' else ''
                raise DeviceTestError(f'Device {self.serial} is {state}, not ready.{action}')
        else:
            ready = [serial for serial, state in devices.items() if state == 'device']
            if len(ready) != 1:
                raise DeviceTestError(f'Exactly one authorized device is required, or pass --serial; devices: {devices}')
            self.serial = ready[0]

    def properties(self):
        names = {'model': 'ro.product.model', 'manufacturer': 'ro.product.manufacturer',
                 'device': 'ro.product.device', 'sdk': 'ro.build.version.sdk',
                 'android_release': 'ro.build.version.release', 'abi': 'ro.product.cpu.abi',
                 'abi_list': 'ro.product.cpu.abilist', 'build_fingerprint': 'ro.build.fingerprint'}
        values = self.shell('; '.join('getprop ' + shlex.quote(value) for value in names.values())).stdout.splitlines()
        if len(values) != len(names):
            raise DeviceTestError('Device getprop returned an unexpected number of fields')
        result = dict(zip(names, (value.strip() for value in values)))
        result['sdk'] = int(result['sdk'])
        result['abi_list'] = [abi for abi in result['abi_list'].split(',') if abi]
        result['serial'] = self.serial
        if 'arm64-v8a' not in result['abi_list'] and result['abi'] != 'arm64-v8a':
            raise DeviceTestError(f'Provided CLI requires arm64-v8a; device ABIs: {result["abi_list"]}')
        return result

    def push_checked(self, local, name, executable=False):
        local = Path(local).resolve()
        remote = REMOTE + '/' + name
        self.run(['push', str(local), remote])
        self.shell('chmod ' + ('755' if executable else '644') + ' ' + shlex.quote(remote))
        command = 'sha256sum ' + shlex.quote(remote)
        result = self.shell(command, check=False)
        if result.returncode:
            result = self.shell('toybox ' + command)
        fields = result.stdout.split()
        expected = sha256(local)
        if not fields or fields[0].lower() != expected:
            raise DeviceTestError(f'Device SHA256 mismatch after pushing {local.name}: {result.stdout.strip()}')
        return {'path': str(local), 'remote_path': remote, 'bytes': local.stat().st_size,
                'sha256': expected, 'device_sha256_verified': True}

    def stop_test_child(self):
        # The PID must still refer to this test directory's CLI, never an app.
        pid_path, cli_path = shlex.quote(REMOTE + '/bench.pid'), shlex.quote(REMOTE + '/ipa_graph')
        command = (f'if [ -r {pid_path} ]; then pid=$(cat {pid_path}); '
                   'case "$pid" in ""|*[!0-9]*) ;; *) '
                   f'if grep -Fq {cli_path} "/proc/$pid/cmdline" 2>/dev/null; '
                   'then kill "$pid" 2>/dev/null; fi ;; esac; fi')
        try:
            self.shell(command, check=False, timeout=min(self.timeout, 10))
        except DeviceTestError:
            pass


def profile(case):
    regions = tuple(case.get('regions') or ())
    if any(region not in REGIONS for region in regions):
        raise DeviceTestError('Unknown case region: ' + str(regions))
    limit, budget, deadline = case.get('limit', 180), case.get('prefix_budget', 2048), case.get('deadline_ms', 60)
    if not isinstance(limit, int) or not 0 <= limit <= 180:
        raise DeviceTestError('Case limit must be an integer in 0..180')
    if not isinstance(budget, int) or not 1 <= budget <= 65536:
        raise DeviceTestError('Case prefix_budget must be an integer in 1..65536')
    if not isinstance(deadline, int) or not 1 <= deadline <= 60:
        raise DeviceTestError('Case deadline_ms must be an integer in 1..60')
    return regions, bool(case.get('prefix')), limit, budget, deadline


def profile_flags(value):
    regions, prefix, limit, budget, deadline = value
    flags = []
    for region in regions:
        flags += ['--region', region]
    if prefix:
        flags.append('--prefix')
    return flags + ['--limit', str(limit), '--prefix-budget', str(budget), '--deadline-ms', str(deadline)]


def read_cases(path, category):
    cases = []
    for line_number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case, dict) or not isinstance(case.get('raw'), str):
            raise DeviceTestError(f'{path}:{line_number}: case must contain a string raw')
        if '\n' in case['raw'] or '\r' in case['raw']:
            raise DeviceTestError(f'{path}:{line_number}: REPL input must be a single line')
        case = dict(case)
        case.setdefault('id', f'{category}-{line_number}')
        case['_category'] = category
        profile(case)
        cases.append(case)
    return cases


def parse_json_lines(result, expected=None):
    try:
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise DeviceTestError(f'Native CLI produced invalid JSON: {result.stdout[:1000]}') from error
    if expected is not None and len(rows) != expected:
        raise DeviceTestError(f'Native CLI returned {len(rows)} records; expected {expected}; stderr: {result.stderr[-2000:]}')
    return rows


def check_case(case, result):
    failures = []
    candidates = result.get('candidates', [])
    texts = {candidate.get('text') for candidate in candidates}
    if result.get('status') != case.get('expected_status', 'ok'):
        failures.append('status')
    if case.get('expected_any') and not texts.intersection(case['expected_any']):
        failures.append('expected_any')
    if case.get('expected_all') and not set(case['expected_all']).issubset(texts):
        failures.append('expected_all')
    if case.get('expected_empty') and candidates:
        failures.append('expected_empty')
    if case.get('expected_nonempty') and not candidates:
        failures.append('expected_nonempty')
    if 'expected_truncated' in case and result.get('truncated') != case['expected_truncated']:
        failures.append('truncated')
    if result.get('returned') != len(candidates) or len(candidates) > profile(case)[2]:
        failures.append('candidate_limit_or_count')
    enabled = set(case.get('regions') or REGIONS)
    for rank, candidate in enumerate(candidates):
        if not isinstance(candidate.get('text'), str) or not candidate['text'].strip():
            failures.append('invalid_candidate_text')
        if candidate.get('region') not in enabled:
            failures.append('disabled_region_returned')
        if candidate.get('rank') != rank:
            failures.append('candidate_rank')
        affinity = candidate.get('context_affinity')
        if not isinstance(affinity, (int, float)) or not math.isfinite(affinity) or not 0 <= affinity <= 1:
            failures.append('context_affinity')
    return sorted(set(failures))


def correctness(adb, dictionaries, cases):
    groups = collections.OrderedDict()
    for index, case in enumerate(cases):
        groups.setdefault(profile(case), []).append((index, case))
    outputs = {24: [None] * len(cases), 32: [None] * len(cases)}
    for group_number, (configuration, group) in enumerate(groups.items()):
        order = (24, 32) if group_number % 2 == 0 else (32, 24)
        input_text = ''.join(unicodedata.normalize('NFC', case['raw']) + '\n' for unused, case in group)
        for width in order:
            command = shlex.join([REMOTE + '/ipa_graph', 'repl', dictionaries[width], *profile_flags(configuration)])
            process = adb.shell(command, input_text=input_text)
            for (index, case), row in zip(group, parse_json_lines(process, len(group))):
                if row.get('raw') != unicodedata.normalize('NFC', case['raw']):
                    raise DeviceTestError(f'Native REPL input/output order mismatch at case {case["id"]}')
                outputs[width][index] = row
        print(f'Correctness group {group_number + 1}/{len(groups)}: {len(group)} cases; '
              f'regions={configuration[0]} prefix={configuration[1]} limit={configuration[2]}', flush=True)
    results, mismatches, failures = [], [], {24: 0, 32: 0}
    for index, case in enumerate(cases):
        first, second = outputs[24][index], outputs[32][index]
        same = ({key: value for key, value in first.items() if key != 'elapsed_ms'}
                == {key: value for key, value in second.items() if key != 'elapsed_ms'})
        checked = {width: check_case(case, outputs[width][index]) for width in (24, 32)}
        for width in (24, 32):
            failures[width] += bool(checked[width])
        if not same:
            mismatches.append(case['id'])
        results.append({'id': case['id'], 'input': {k: v for k, v in case.items() if k != '_category'},
                        'category': case['_category'], 'same_full_output': same,
                        'checks24': checked[24], 'checks32': checked[32], 'result24': first, 'result32': second})
    return {'passed': not mismatches and not any(failures.values()), 'cases': len(cases),
            'mapping_cases': sum(case['_category'] == 'mapping' for case in cases),
            'manual_cases': sum(case['_category'] == 'manual' for case in cases), 'same_all': not mismatches,
            'output_mismatch_ids': mismatches, 'expected_case_failures_by_width': failures,
            'failed_case_ids': [row['id'] for row in results
                                if not row['same_full_output'] or row['checks24'] or row['checks32']],
            'expected_failure_ids_by_width': {
                width: [row['id'] for row in results if row['checks' + str(width)]] for width in (24, 32)},
            'results': results}


def bench_shell(dictionary, input_path, iterations, warmup, sample_rss):
    command = shlex.join([REMOTE + '/ipa_graph', 'bench', dictionary, '-', '--iterations', str(iterations),
                          '--warmup', str(warmup), '--limit', '180', '--deadline-ms', '60'])
    result_path, error_path, pid_path = (shlex.quote(REMOTE + '/' + name)
                                        for name in ('bench-result.json', 'bench-error.txt', 'bench.pid'))
    script = (f'{command} < {shlex.quote(input_path)} > {result_path} 2> {error_path} &\n'
              f'pid=$!; printf "%s\\n" "$pid" > {pid_path}\npeak=0; samples=0\n')
    if sample_rss:
        script += r'''while kill -0 "$pid" 2>/dev/null; do
  state=$(sed -n 's/^State:[[:space:]]*\([^[:space:]]*\).*/\1/p' "/proc/$pid/status" 2>/dev/null)
  [ "$state" = "Z" ] && break
  rss=$(sed -n 's/^VmRSS:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "/proc/$pid/status" 2>/dev/null)
  case "$rss" in ''|*[!0-9]*) ;; *)
    samples=$((samples + 1)); [ "$rss" -gt "$peak" ] && peak=$rss ;;
  esac
  sleep 0.05
done
'''
    script += (f'wait "$pid"; native_status=$?\ncat {result_path}\ncat {error_path} >&2\n'
               'printf \'{"_device_sample":true,"peak_rss_sampled_kib":%s,"rss_samples":%s,'
               '"native_exit_code":%s}\\n\' "$peak" "$samples" "$native_status"\n'
               f'rm -f {pid_path}\nexit "$native_status"')
    return script


def benchmarks(adb, dictionaries, input_path, case_count, iterations, rounds, warmup, sample_rss):
    records = []
    for round_number in range(rounds):
        order = (24, 32) if round_number % 2 == 0 else (32, 24)
        runs = []
        for width in order:
            try:
                process = adb.shell(bench_shell(dictionaries[width], input_path, iterations, warmup, sample_rss), check=False)
            except DeviceTestError:
                adb.stop_test_child()
                raise
            values = parse_json_lines(process)
            metrics = [value for value in values if not value.get('_device_sample')]
            samples = [value for value in values if value.get('_device_sample')]
            if len(metrics) != 1 or len(samples) != 1:
                raise DeviceTestError(f'Benchmark did not produce metrics and RSS marker: {process.stderr[-2000:]}')
            result, memory = metrics[0], samples[0]
            if result.get('index_bits') != width or result.get('iterations') != iterations or result.get('cases') != case_count:
                raise DeviceTestError('Benchmark width/iteration/input count differs from requested run')
            status = 'ok' if not process.returncode and not result.get('timeouts') and not result.get('errors') else 'failed'
            result.update({'status': status, 'adb_wall_ms': process.wall_ms, 'adb_exit_code': process.returncode,
                           'native_exit_code': memory['native_exit_code'],
                           'peak_rss_sampled_kib': memory['peak_rss_sampled_kib'] if memory['rss_samples'] else None,
                           'rss_samples': memory['rss_samples'], 'rss_interval_ms': 50 if sample_rss else None,
                           'stderr': process.stderr.strip()})
            runs.append(result)
            print(f'Round {round_number + 1}/{rounds}, {width}-bit: load={result["load_ms"]:.3f} ms '
                  f'p50={result["p50_ms"]:.5f} p95={result["p95_ms"]:.5f} '
                  f'p99={result["p99_ms"]:.5f} max={result["max_ms"]:.5f} ms status={status}', flush=True)
        records.append({'round': round_number + 1, 'order': list(order), 'runs': runs})
    summary = {}
    for width in (24, 32):
        runs = [run for record in records for run in record['runs'] if run['index_bits'] == width]
        summary[width] = {'rounds': len(runs), 'all_ok': all(run['status'] == 'ok' for run in runs),
                          'total_iterations': sum(run['iterations'] for run in runs),
                          'total_timeouts': sum(run['timeouts'] for run in runs), 'total_errors': sum(run['errors'] for run in runs)}
        for field in ('load_ms', 'p50_ms', 'p95_ms', 'p99_ms', 'max_ms'):
            summary[width]['median_of_round_' + field] = statistics.median(run[field] for run in runs)
        memory = [run['peak_rss_sampled_kib'] for run in runs if run['peak_rss_sampled_kib'] is not None]
        summary[width]['peak_rss_sampled_kib'] = max(memory) if memory else None
    return {'passed': all(value['all_ok'] for value in summary.values()),
            'profile': 'all regions, exact lookup, maximum 180 candidates, deadline 60 ms',
            'cases': case_count, 'iterations_per_run': iterations, 'warmup_per_run': warmup,
            'rounds': records, 'summary': summary}


def validate_cli(path):
    with Path(path).open('rb') as incoming:
        header = incoming.read(20)
    if (len(header) < 20 or header[:4] != b'\x7fELF' or header[4:6] != b'\x02\x01'
            or struct.unpack_from('<H', header, 18)[0] != 183):
        raise DeviceTestError('--cli must be an Android ELF64 AArch64 executable; no desktop fallback')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', help='default: configured/conventional Android SDK installations, then PATH')
    parser.add_argument('--serial', help='required if multiple authorized devices are connected')
    parser.add_argument('--dictionary24', type=Path, default=ROOT / 'data/dictionary/compiled/lexicon.ipad')
    parser.add_argument('--dictionary32', type=Path, default=ROOT / 'data/dictionary/compiled/lexicon-32.ipad')
    parser.add_argument('--cli', type=Path, default=ROOT / 'data/engine_android/builds/24/ipa_graph-arm64-v8a')
    parser.add_argument('--cases', type=Path, default=ROOT / 'cli_tests/cases/graph_cases.jsonl')
    parser.add_argument('--manual-cases', type=Path, default=ROOT / 'cli_tests/cases/graph_cases.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/cli_tests/reports/graph-s21-comparison.json')
    parser.add_argument('--details', type=Path, default=ROOT / 'data/cli_tests/reports/android-native-cases.json',
                        help='full per-case output JSON; main report stores only path, SHA256, size and failures')
    parser.add_argument('--iterations', type=int, default=100000)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--warmup', type=int, default=1000)
    parser.add_argument('--timeout', type=int, default=60, help='per ADB invocation timeout, 1..60 seconds')
    parser.add_argument('--no-rss', action='store_true', help='disable /proc RSS sampling during benchmarks')
    args = parser.parse_args()
    if not 1 <= args.iterations <= 1000000 or not 1 <= args.rounds <= 100 or not 0 <= args.warmup <= 1000000:
        parser.error('iterations 1..1000000, rounds 1..100, warmup 0..1000000 required')
    if not 1 <= args.timeout <= 60:
        parser.error('timeout must be 1..60 seconds')
    report = {'format': 'ipa-graph-android-native-comparison-v1', 'status': 'running',
              'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'requested_serial': args.serial,
              'notes': ['Actual native execution on the selected Android device; no desktop fallback.',
                        'Does not test the Android Java asset wrapper, JNI bridge, or IME service integration.',
                        'Expected-target inclusion checks mappings, not source IPA correctness or top-1 linguistic accuracy.',
                        'Each benchmark uses a fresh native process, warmup, and alternating width order; filesystem cache is not cleared.',
                        'RSS is sampled rather than exact VmHWM, includes mmap resident pages, and sampling may slightly perturb timings.',
                        'This is not long-term battery, thermal stability, or keyboard lifecycle qualification.']}
    adb = None
    try:
        if args.details.resolve() == args.output.resolve():
            raise DeviceTestError('--details and --output must refer to different files')
        for path in (args.dictionary24, args.dictionary32, args.cli, args.cases, args.manual_cases):
            if not path.is_file():
                raise DeviceTestError(f'Required local file does not exist: {path}')
        validate_cli(args.cli)
        mapping_cases, manual_cases = read_cases(args.cases, 'mapping'), read_cases(args.manual_cases, 'manual')
        if not mapping_cases:
            raise DeviceTestError('At least one mapping case is required')
        adb = Adb(args.adb, args.serial, args.timeout)
        adb.select_device()
        report['device'] = adb.properties()
        report['adb_version'] = adb.run(['version'], selected=False).stdout.strip()
        adb.shell('mkdir -p ' + shlex.quote(REMOTE))
        report['artifacts'] = {'cli': adb.push_checked(args.cli, 'ipa_graph', executable=True),
                               '24': adb.push_checked(args.dictionary24, 'lexicon-24.ipad'),
                               '32': adb.push_checked(args.dictionary32, 'lexicon-32.ipad')}
        dictionaries = {width: report['artifacts'][str(width)]['remote_path'] for width in (24, 32)}
        infos = {}
        for width in (24, 32):
            info = parse_json_lines(adb.shell(shlex.join([REMOTE + '/ipa_graph', 'info', dictionaries[width]])), 1)[0]
            if info.get('index_bits') != width or info.get('dictionary_bytes') != report['artifacts'][str(width)]['bytes']:
                raise DeviceTestError(f'Native info reports unexpected {width}-bit format or size')
            infos[width] = info
        for field in ('nodes', 'arcs', 'keys', 'words', 'alphabet'):
            if infos[24].get(field) != infos[32].get(field):
                raise DeviceTestError('24/32-bit dictionaries have different lexical graphs: ' + field)
        report['info'] = infos
        report['correctness'] = correctness(adb, dictionaries, mapping_cases + manual_cases)
        case_results = report['correctness'].pop('results')
        write_report(args.details, case_results)
        report['correctness']['details'] = {
            'path': str(args.details.resolve()), 'sha256': sha256(args.details),
            'bytes': args.details.stat().st_size}
        del case_results
        write_report(args.output, report)
        bench_inputs = ''.join(unicodedata.normalize('NFC', case['raw']) + '\n' for case in mapping_cases)
        with tempfile.TemporaryDirectory(prefix='ipa-android-test-') as temporary:
            path = Path(temporary) / 'bench-inputs.txt'
            path.write_text(bench_inputs, encoding='utf-8')
            dataset = adb.push_checked(path, 'bench-inputs.txt')
            dataset['path'], dataset['cases'] = str(args.cases.resolve()), len(mapping_cases)
            dataset['encoding'] = 'NFC UTF-8 IPA lines, passed to native bench stdin'
            report['benchmark_dataset'] = dataset
            report['benchmarks'] = benchmarks(adb, dictionaries, dataset['remote_path'], len(mapping_cases),
                                               args.iterations, args.rounds, args.warmup, not args.no_rss)
        passed = report['correctness']['passed'] and report['benchmarks']['passed']
        report['status'] = 'passed' if passed else 'failed'
        report['completed_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_report(args.output, report)
        print(json.dumps({'status': report['status'], 'device': report['device'],
                          'correctness_cases': report['correctness']['cases'], 'same_all': report['correctness']['same_all'],
                          'benchmarks': report['benchmarks']['summary'], 'report': str(args.output.resolve())},
                         ensure_ascii=False, indent=2))
        return 0 if passed else 1
    except (DeviceTestError, OSError, ValueError) as error:
        report['status'], report['error'] = 'error', str(error)
        report['completed_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if adb is not None:
            adb.stop_test_child()
        write_report(args.output, report)
        print(f'Android device test failed: {error}\nReport: {args.output.resolve()}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
