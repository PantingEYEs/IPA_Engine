#!/usr/bin/env python3
"""Download and SHA-256 verify the official Linux CMake/NDK archives."""
import hashlib,json,pathlib,subprocess,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
def main():
    lock=json.loads((ROOT/'sources.lock.json').read_text())['toolchain']
    tools=ROOT/'.cache/tools';tools.mkdir(parents=True,exist_ok=True)
    for kind,entry in [('cmake','cmake-3.31.6-linux-x86_64/bin/cmake'),('androidNdk','android-ndk-r29/source.properties')]:
        url=lock[kind+'Url'];archive=tools/url.rsplit('/',1)[1]
        if not archive.exists():
            temporary=archive.with_suffix(archive.suffix+'.part')
            urllib.request.urlretrieve(url,temporary)
            temporary.replace(archive)
        with archive.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=lock[kind+'Sha256']:raise SystemExit(f'Bad archive digest: {archive}')
        if not (tools/entry).is_file():
            # These are authenticated upstream archives, never arbitrary user uploads.
            if archive.suffix=='.zip':subprocess.run(['unzip','-q',str(archive),'-d',str(tools)],check=True)
            else:subprocess.run(['tar','-xzf',str(archive),'-C',str(tools)],check=True)
        print(kind+': '+str(tools/entry))
if __name__=='__main__':main()
