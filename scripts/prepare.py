#!/usr/bin/env python3
"""Fetch exact source commits, verify tracked changes and apply documented patches."""
import argparse, hashlib, json, pathlib, subprocess
ROOT = pathlib.Path(__file__).resolve().parents[1]

def run(*args, cwd=None):
    if args[0]=='git': args=('git','-c','core.filemode=false',*args[1:])
    return subprocess.check_output(args, cwd=cwd, stderr=subprocess.STDOUT)

def checkout(name, entry, target, patches):
    if not (target / '.git').is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git','-c','credential.helper=','clone','--depth','1','--branch',entry['tag'],entry['repository'],str(target)],check=True)
    actual=run('git','rev-parse','HEAD',cwd=target).decode().strip()
    if actual != entry['commit']:
        raise SystemExit(f'{name}: wrong commit {actual}')
    flags=run('git','ls-files','-v',cwd=target).decode().splitlines()
    if any(line and (line[0].islower() or line[0]=='S') for line in flags):
        raise SystemExit(f'{name}: hidden index flags are forbidden')
    dirty=run('git','diff','--binary','--no-ext-diff','HEAD',cwd=target).replace(b'\r\n',b'\n')
    # Compare the complete tracked diff against concatenated approved patches.
    # git diff combines multi-file patches in pathname order; normalize via an
    # alternate temporary index to produce that canonical expected diff.
    import os, tempfile
    with tempfile.TemporaryDirectory(prefix='ortx-index-') as temp:
        env=dict(os.environ,GIT_INDEX_FILE=str(pathlib.Path(temp)/'index'))
        subprocess.run(['git','read-tree','HEAD'],cwd=target,env=env,check=True)
        for patch in patches:
            subprocess.run(['git','apply','--cached','--ignore-space-change','--ignore-whitespace',str(patch)],cwd=target,env=env,check=True)
        expected=subprocess.check_output(['git','diff','--cached','--binary','--no-ext-diff','HEAD'],cwd=target,env=env).replace(b'\r\n',b'\n')
    if not dirty and expected:
        for patch in patches:
            subprocess.run(['git','apply','--ignore-space-change','--ignore-whitespace',str(patch)],cwd=target,check=True)
        dirty=run('git','diff','--binary','--no-ext-diff','HEAD',cwd=target).replace(b'\r\n',b'\n')
    if dirty != expected:
        raise SystemExit(f'{name}: unapproved tracked changes; refusing to reset source')
    untracked=run('git','ls-files','--others',cwd=target).decode().strip()
    if untracked:
        raise SystemExit(f'{name}: unexpected untracked source files: {untracked}')
    print(f'{name}: verified {actual}')

def main():
    lock=json.loads((ROOT/'sources.lock.json').read_text())
    source=ROOT/'.work/upstream'
    patch=ROOT/lock['upstream']['patch']
    if hashlib.sha256(patch.read_bytes()).hexdigest()!=lock['upstream']['patchSha256']:
        raise SystemExit('Local patch hash mismatch')
    checkout('extensions',lock['upstream'],source,[patch])
    for name,dep in lock['dependencies'].items():
        checkout(name,dep,ROOT/'.work/deps'/name,[source/p for p in dep['patches']])
    for name,digest in lock['ortHeaders'].items():
        if hashlib.sha256((ROOT/'integration/onnxruntime-1.23.2-headers'/name).read_bytes()).hexdigest()!=digest:
            raise SystemExit(f'ORT header mismatch: {name}')
if __name__=='__main__':main()
