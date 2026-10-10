"""Compare full compiled-dictionary outputs and host timing with the C++ reference."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.engine_cli import Session, ensure_cli, read_cases
from scripts.build_rust_core import source_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dictionary', type=Path, default=ROOT / 'data/compiled/lexicon.ipad')
    parser.add_argument('--cases', type=Path, default=ROOT / 'data/compiled/source-cases.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/reports/rust-migration.json')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--iterations', type=int, default=1000)
    args = parser.parse_args()
    if args.rounds < 1 or args.iterations < 1:
        parser.error('rounds and iterations must be positive')
    rust = ensure_cli(ROOT / '.tools/graph/ipa_graph')
    runtime_digest = source_digest()
    compiler = shutil.which('clang++') or shutil.which('g++')
    if not compiler:
        parser.error('C++17 compiler required for the migration reference')
    with tempfile.TemporaryDirectory(prefix='ipa-rust-compare-') as scratch:
        cpp = Path(scratch) / 'ipa_graph_cpp'
        subprocess.run([compiler, '-std=c++17', '-O3', '-I', str(ROOT / 'graph/include'),
                        str(ROOT / 'graph/src/ipa_graph.cpp'), str(ROOT / 'graph/src/ipa_graph_cli.cpp'),
                        '-o', str(cpp)], check=True)
        rows = read_cases(args.cases)
        checks = []
        benchmarks = {}
        for strict in (True, False):
            total = mismatches = reference_missing = capped = 0
            examples = []
            for region in ('en-US', 'en-GB', 'zh-CN', 'zh-TW', 'ja-JP'):
                selected = [row for row in rows if row.get('regions') == [region]]
                sessions = [Session(cli, args.dictionary, {'regions': [region], 'strict': strict, 'limit': 180})
                            for cli in (cpp, rust)]
                try:
                    for row in selected:
                        a, b = [session.query(row['raw']) for session in sessions]
                        total += 1
                        reference_missing += not {c['text'] for c in b['candidates']}.intersection(row['expected_any'])
                        capped += a['truncated'] or b['truncated']
                        for result in (a, b):
                            result.pop('elapsed_ms', None)
                        if a != b:
                            mismatches += 1
                            if len(examples) < 8:
                                examples.append({'raw': row['raw'], 'region': region,
                                                 'cpp_status': a['status'], 'rust_status': b['status'],
                                                 'cpp_truncated': a['truncated'], 'rust_truncated': b['truncated']})
                finally:
                    for session in sessions:
                        session.close()
            checks.append({'mode': 'strict' if strict else 'omissions', 'cases': total,
                           'mismatches': mismatches, 'missing_reference': reference_missing,
                           'budget_capped_pairs': capped, 'examples': examples})
            print(json.dumps(checks[-1], ensure_ascii=False), flush=True)
            # Identical inputs and options, one resident instance per measured round.
            inputs = '\n'.join(row['raw'] for row in rows[:500]) + '\n'
            measurements = {'cpp': [], 'rust': []}
            for round_index in range(args.rounds):
                order = [('cpp', cpp), ('rust', rust)]
                if round_index % 2:
                    order.reverse()
                for name, cli in order:
                    command = [str(cli), 'bench', str(args.dictionary), '-', '--iterations', str(args.iterations), '--warmup', '500']
                    if strict:
                        command += ['--strict']
                    result = subprocess.run(command, input=inputs, check=True, text=True, capture_output=True)
                    measurements[name].append(json.loads(result.stdout))
                print(f"{'strict' if strict else 'omissions'} timing round {round_index + 1}/{args.rounds}", flush=True)
            for name, values in measurements.items():
                benchmarks[('strict' if strict else 'omissions') + '_' + name] = values
        report = {'status': 'passed' if all(x['mismatches'] == 0 and x['missing_reference'] == 0 for x in checks) else 'review_required',
                  'engine_version': 'v0.0.3-dev', 'dictionary_bytes': args.dictionary.stat().st_size,
                  'runtime_source_sha256': runtime_digest,
                  'checks': checks, 'benchmarks': benchmarks,
                  'note': 'Development-host comparison, not phone performance. Time-capped results can differ with scheduling. '
                          'Full-word source observations verify compiled relation retention, not user intent or source correctness.'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': report['status'], 'report': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
