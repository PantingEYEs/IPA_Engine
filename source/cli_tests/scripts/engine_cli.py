"""Inspect and test the same Rust graph core used by the Android dependency."""
import argparse
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import unicodedata

ROOT=Path(__file__).resolve().parents[2]
REGIONS=('en-US','en-GB','zh-CN','zh-TW','ja-JP','ru-RU','ar-001','ko-KR')


def ensure_cli(path):
    sys.path.insert(0, str(ROOT))
    from engine_android.scripts.build_rust_core import build
    path=Path(path)
    sources=[ROOT/'engine_android/ipa_graph/Cargo.toml',
             *list((ROOT/'engine_android/ipa_graph/src').rglob('*.rs'))]
    if not path.exists() or path.stat().st_mtime_ns<max(p.stat().st_mtime_ns for p in sources):
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.part')
        shutil.copyfile(build() / 'ipa_graph', temporary)
        temporary.chmod(0o755)
        temporary.replace(path)
    return path.resolve()


def flags(case):
    result=[]
    if case.get('no_default_packs'):result.append('--no-default-packs')
    for path in case.get('extra_dictionaries') or []:
        result.extend(['--extra-dictionary',str(path)])
    for specification in case.get('pack_modes') or []:
        result.extend(['--pack-mode',specification])
    for region in case.get('regions') or []:
        if region not in REGIONS:raise ValueError('unknown region '+region)
        result.extend(['--region',region])
    if case.get('prefix'):result.append('--prefix')
    if case.get('strict'):result.append('--strict')
    result.extend(['--omission-budget',str(case.get('omission_budget',20000))])
    result.extend(['--limit',str(case.get('limit',30)),'--prefix-budget',str(case.get('prefix_budget',2048))])
    return result


def invoke(cli,dictionary,case):
    raw=unicodedata.normalize('NFC',case['raw'])
    process=subprocess.run([str(cli),'query',str(dictionary),raw,*flags(case)],text=True,capture_output=True)
    if not process.stdout.strip():raise RuntimeError(process.stderr.strip() or 'engine produced no result')
    result=json.loads(process.stdout)
    result['input_raw']=case['raw'];result['normalized_raw']=raw
    return result


def display(result):
    print(f"输入: {result['raw']!r}  状态: {result['status']}  {result['elapsed_ms']:.4f} ms")
    print(f"精确命中: {result['exact_found']}  IPA键: {result['matched_keys']}  映射: {result['matched_records']}  截断: {result['truncated']}")
    for c in result['candidates']:
        provenance='派生' if c['derived'] else '直接'
        print(f"{c['rank']+1:3}. {c['text']}\t{c['language']} / {c['region']}\tZipf≈{c['frequency_zipf']:.2f}\t{c['kind']} / {provenance}\t漏符号: {c.get('omitted_symbols',0)}\tIPA: {c['matched_ipa']}")
    if not result['candidates']:print('（无候选）')


def check_case(case,result):
    failures=[];texts={c['text'] for c in result['candidates']}
    if result['status']!=case.get('expected_status','ok'):failures.append('status')
    if case.get('expected_any') and not texts.intersection(case['expected_any']):failures.append('expected_any')
    if case.get('expected_all') and not set(case['expected_all']).issubset(texts):failures.append('expected_all')
    if case.get('expected_none') and texts.intersection(case['expected_none']):failures.append('expected_none')
    if case.get('expected_empty') and texts:failures.append('expected_empty')
    if case.get('expected_nonempty') and not texts:failures.append('expected_nonempty')
    if 'expected_truncated' in case and result['truncated']!=case['expected_truncated']:failures.append('truncated')
    if 'expected_exact_found' in case and result['exact_found']!=case['expected_exact_found']:failures.append('exact_found')
    return failures


def read_cases(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def open_lexicon(dictionary):
    sys.path.insert(0,str(ROOT))
    from cli_tests.scripts.compiled_lexicon import CompiledLexicon
    return CompiledLexicon(dictionary)


def reverse_native(cli,dictionary,word,case=None):
    process=subprocess.run([str(cli),'reverse',str(dictionary),unicodedata.normalize('NFC',word),
                            *flags(case or {})],text=True,capture_output=True)
    if process.returncode or not process.stdout.strip():
        raise RuntimeError(process.stderr.strip() or 'engine reverse lookup failed')
    return json.loads(process.stdout)


def stream_request(process,text):
    if '\n' in text or '\r' in text:raise ValueError('stream requests must be single-line')
    process.stdin.write(text+'\n');process.stdin.flush()
    line=process.stdout.readline()
    if not line:raise RuntimeError('engine closed')
    return json.loads(line)


def display_packs(result,as_json=False):
    if as_json:
        print(json.dumps(result,ensure_ascii=False))
        return
    for pack in result['packs']:
        print(f"{pack['id']}\t{pack['mode']}\tloaded={pack['loaded']}\t"
              f"graph={pack['graph_bytes']} B\treverse={pack['reverse_bytes']} B\t"
              f"search_index={pack['search_index_bytes']} B")


def display_reverse(word,readings,as_json=False,truncated=False):
    # Missing words/disabled readings are deliberately silent, also in JSON mode.
    if not readings:return
    word=unicodedata.normalize('NFC',word)
    if as_json:
        print(json.dumps(dict(word=word,pronunciations=readings,truncated=truncated),ensure_ascii=False))
    else:
        print(f'词汇: {word}')
        for i,r in enumerate(readings,1):
            provenance='派生' if r['derived'] else '直接'
            print(f"{i:3}. {r['ipa']}\t{r['region']} / {provenance}\tZipf≈{r['frequency_zipf']:.2f}")
        if truncated:print('（反查结果已截断）')


def random_test(cli,dictionary,rows,as_json=False):
    if not rows:raise ValueError('test case file contains no cases')
    case=random.choice(rows)
    result=invoke(cli,dictionary,case)
    failures=check_case(case,result)
    if as_json:
        print(json.dumps(dict(type='test',id=case.get('id'),input=case,result=result,
                              passed=not failures,failures=failures),ensure_ascii=False))
    else:
        print('随机用例: '+str(case.get('id')))
        print('用例配置/预期: '+json.dumps(case,ensure_ascii=False))
        display(result)
        print('检查: '+('PASS' if not failures else 'FAIL '+', '.join(failures)))


class Session:
    def __init__(self,cli,dictionary,case=None):
        options=flags(case or {'limit':180})
        self.process=subprocess.Popen([str(cli),'repl',str(dictionary),*options],stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    def query(self,raw):
        # Evaluation may intentionally use a frozen historical native binary.
        # Its original plain-IPA line protocol remains supported by new cores.
        return stream_request(self.process,unicodedata.normalize('NFC',raw))
    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)
        self.process.stdout.close();self.process.stderr.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dictionary',type=Path,default=ROOT/'data/dictionary/compiled/lexicon.ipad')
    parser.add_argument('--cli',type=Path,default=ROOT/'data/cli_tests/builds/ipa_graph')
    sub=parser.add_subparsers(dest='command',required=True)
    def resource_args(p):
        p.add_argument('--extra-dictionary',dest='extra_dictionaries',action='append',type=Path,
                       help='attach an independent language-pack container; repeat for multiple files')
        p.add_argument('--no-default-packs',action='store_true',help='open only the directory until packs are enabled')
        p.add_argument('--pack-mode',dest='pack_modes',action='append',metavar='ID:off|reverse|input',
                       help='select a language pack mode; repeat for multiple packs')
    def query_args(p):
        resource_args(p)
        p.add_argument('--region',dest='regions',action='append',choices=REGIONS)
        p.add_argument('--prefix',action='store_true');p.add_argument('--prefix-budget',type=int,default=2048)
        p.add_argument('--strict',action='store_true',help='disable ordered omission matching')
        p.add_argument('--omission-budget',type=int,default=20000)
        p.add_argument('--limit',type=int,default=30);p.add_argument('--json',action='store_true')
    q=sub.add_parser('query');q.add_argument('raw');query_args(q)
    repl=sub.add_parser('repl');query_args(repl)
    repl.add_argument('--cases',type=Path,default=ROOT/'cli_tests/cases/graph_cases.jsonl',
                      help='JSONL cases used by the interactive test command')
    reverse=sub.add_parser('reverse',help='look up recorded IPA for an exact output word')
    reverse.add_argument('word')
    reverse.add_argument('--region',dest='regions',action='append',choices=REGIONS)
    reverse.add_argument('--json',action='store_true')
    resource_args(reverse)
    packs=sub.add_parser('packs',help='list available language packs and current resource state')
    packs.add_argument('--json',action='store_true');resource_args(packs)
    test=sub.add_parser('test',help='show one random test case and its actual output')
    test.add_argument('--cases',type=Path,default=ROOT/'cli_tests/cases/graph_cases.jsonl')
    test.add_argument('--json',action='store_true')
    cases=sub.add_parser('cases');cases.add_argument('path',type=Path);cases.add_argument('--output',type=Path)
    cases.add_argument('--verbose',action='store_true')
    compare=sub.add_parser('compare');compare.add_argument('other',type=Path)
    compare.add_argument('--cases',type=Path,default=ROOT/'cli_tests/cases/graph_cases.jsonl')
    compare.add_argument('--output',type=Path,default=ROOT/'data/cli_tests/reports/graph-cli-comparison.json')
    bench=sub.add_parser('bench');bench.add_argument('raw');bench.add_argument('--iterations',type=int,default=10000)
    query_args(bench)
    info=sub.add_parser('info');resource_args(info)
    args=parser.parse_args()
    cli=ensure_cli(args.cli)
    if args.command=='reverse':
        result=reverse_native(cli,args.dictionary,args.word,vars(args))
        display_reverse(args.word,result['readings'],args.json,result['truncated'])
        return
    if args.command=='packs':
        process=subprocess.run([str(cli),'packs',str(args.dictionary),*flags(vars(args))],
                               text=True,capture_output=True,check=True)
        display_packs(json.loads(process.stdout),args.json)
        return
    if args.command=='test':
        random_test(cli,args.dictionary,read_cases(args.cases),args.json)
        return
    if args.command in ('query','repl'):
        base=dict(regions=args.regions or [],prefix=args.prefix,limit=args.limit,prefix_budget=args.prefix_budget,
                  strict=args.strict,omission_budget=args.omission_budget,
                  no_default_packs=args.no_default_packs,pack_modes=args.pack_modes,
                  extra_dictionaries=args.extra_dictionaries)
        if args.command=='query':
            r=invoke(cli,args.dictionary,dict(base,raw=args.raw))
            print(json.dumps(r,ensure_ascii=False,indent=2)) if args.json else display(r)
        else:
            process=subprocess.Popen([str(cli),'repl',str(args.dictionary),*flags(base)],stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,stderr=None,text=True,bufsize=1)
            test_rows=None
            try:
                if sys.stdin.isatty():
                    print('输入 IPA 查看候选，或输入词汇反查 IPA；test 随机用例；Ctrl-D 退出。')
                    print('reverse 词汇 强制反查；ipa 输入 强制正向查询（含无匹配诊断）。')
                    print('packs 查看语言包；pack ru off|reverse|input 更改模式。')
                while True:
                    try:raw=input('IPA> ' if sys.stdin.isatty() else '')
                    except EOFError:break
                    if raw=='packs':
                        display_packs(stream_request(process,':packs'),args.json)
                        continue
                    if raw.startswith('pack '):
                        parts=raw.split()
                        if len(parts)!=3 or parts[2] not in ('off','reverse','input'):
                            print('用法: pack 语言包ID off|reverse|input',file=sys.stderr)
                            continue
                        response=stream_request(process,':pack\t'+parts[1]+'\t'+parts[2])
                        if 'error' in response:print(response['error'],file=sys.stderr)
                        else:display_packs(response,args.json)
                        continue
                    if raw=='test':
                        if test_rows is None:test_rows=read_cases(args.cases)
                        random_test(cli,args.dictionary,test_rows,args.json)
                        continue
                    if raw.startswith('reverse '):
                        word=unicodedata.normalize('NFC',raw[len('reverse '):])
                        response=stream_request(process,':reverse\t'+word)
                        if 'error' in response:print(response['error'],file=sys.stderr)
                        else:display_reverse(word,response['readings'],args.json,response['truncated'])
                        continue
                    force_ipa=raw.startswith('ipa ')
                    if force_ipa:raw=raw[len('ipa '):]
                    r=stream_request(process,':query\t'+unicodedata.normalize('NFC',raw))
                    if force_ipa or r['exact_found']:
                        print(json.dumps(r,ensure_ascii=False)) if args.json else display(r)
                    else:
                        response=stream_request(process,':reverse\t'+unicodedata.normalize('NFC',raw))
                        if response.get('readings'):
                            display_reverse(raw,response['readings'],args.json,response['truncated'])
                        elif r['candidates'] or r['status'] in ('error','timeout'):
                            print(json.dumps(r,ensure_ascii=False)) if args.json else display(r)
            finally:
                process.stdin.close();process.wait(timeout=10);process.stdout.close()
    elif args.command=='cases':
        rows=read_cases(args.path);results=[];sessions={}
        try:
            for c in rows:
                if '\n' not in c['raw'] and '\r' not in c['raw']:
                    key=tuple(flags(c))
                    if key not in sessions:sessions[key]=Session(cli,args.dictionary,c)
                    r=sessions[key].query(c['raw'])
                else:r=invoke(cli,args.dictionary,c)
                failures=check_case(c,r)
                result=dict(id=c.get('id'),passed=not failures,failures=failures,input=c,result=r)
                results.append(result)
                if args.verbose or failures:
                    print(('PASS' if not failures else 'FAIL')+' '+str(c.get('id')))
                    display(r)
        finally:
            for s in sessions.values():s.close()
        report=dict(passed=all(r['passed'] for r in results),total=len(rows),failures=sum(not r['passed'] for r in results),
                    dictionary=str(args.dictionary),results=results,
                    note='Expected-target inclusion validates mapping retrieval; homophone order does not imply top1 linguistic accuracy.')
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ('passed','total','failures')},ensure_ascii=False))
        if not report['passed']:raise SystemExit(1)
    elif args.command=='compare':
        rows=read_cases(args.cases);results=[];sessions={}
        if not rows:parser.error('comparison requires at least one case')
        try:
            for c in rows:
                pair=[]
                for p in (args.dictionary,args.other):
                    if '\n' in c['raw'] or '\r' in c['raw']:r=invoke(cli,p,c)
                    else:
                        key=(p,tuple(flags(c)))
                        if key not in sessions:sessions[key]=Session(cli,p,c)
                        r=sessions[key].query(c['raw'])
                    pair.append(r)
                first,second=pair
                fields=('candidates','matched_records','matched_keys','status','exact_found','truncated')
                same=all(first[k]==second[k] for k in fields)
                results.append(dict(id=c['id'],same=same,first_ms=first['elapsed_ms'],second_ms=second['elapsed_ms']))
        finally:
            for s in sessions.values():s.close()
        def stats(values):
            v=sorted(values);n=len(v)
            return dict(p50_ms=v[n//2],p95_ms=v[min(n-1,int(n*.95))],p99_ms=v[min(n-1,int(n*.99))])
        report=dict(same_all=all(r['same'] for r in results),cases=len(results),
                    first=dict(path=str(args.dictionary),bytes=args.dictionary.stat().st_size,**stats([r['first_ms'] for r in results])),
                    second=dict(path=str(args.other),bytes=args.other.stat().st_size,**stats([r['second_ms'] for r in results])),
                    results=results,
                    note='Varied source inputs on development host, interleaved queries; not SamsungS21Ultra qualification.')
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ('same_all','cases','first','second')},ensure_ascii=False,indent=2))
        if not report['same_all']:raise SystemExit(1)
    elif args.command=='bench':
        command=[str(cli),'bench',str(args.dictionary),unicodedata.normalize('NFC',args.raw),
                 '--iterations',str(args.iterations),*flags(vars(args))]
        subprocess.run(command,check=True)
    else:subprocess.run([str(cli),'info',str(args.dictionary),*flags(vars(args))],check=True)


if __name__=='__main__':main()
