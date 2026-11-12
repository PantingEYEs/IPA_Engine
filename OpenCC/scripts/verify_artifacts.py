#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Validate produced OpenCC binaries and immutable artifact identities, offline."""
from pathlib import Path
import argparse, hashlib, json, struct
ROOT=Path(__file__).resolve().parents[1]
EXPECTED_ABIS={'arm64-v8a':183,'x86_64':62}
REQUIRED_EXPORTS={'opencc_open','opencc_close','opencc_convert_utf8','opencc_convert_utf8_to_buffer','opencc_convert_utf8_free','opencc_error'}
ALLOWED_DEPENDENCIES={'libc.so','libm.so','libdl.so','liblog.so'}
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def inspect_elf(data, machine, expected_soname="libopencc.so", required_exports=REQUIRED_EXPORTS, exact_exports=False):
 if data[:6]!=b'\x7fELF\x02\x01':raise ValueError('Requires little-endian ELF64')
 h=struct.unpack_from('<16sHHIQQQIHHHHHH',data)
 if h[1]!=3 or h[2]!=machine:raise ValueError('Unexpected ELF type or ABI')
 programs=[dict(zip(['type','flags','offset','vaddr','paddr','filesz','memsz','align'],struct.unpack_from('<IIQQQQQQ',data,h[5]+i*h[9]))) for i in range(h[10])]
 loads=[p for p in programs if p['type']==1]
 if not loads:raise ValueError('Missing LOAD segments')
 for p in loads:
  if p['align']<16384 or p['align']&(p['align']-1) or p['offset']%p['align']!=p['vaddr']%p['align']:
   raise ValueError('Invalid 16KB LOAD layout')
  if p['offset']+p['filesz']>len(data) or p['filesz']>p['memsz']:raise ValueError('Invalid LOAD bounds')
 relros=[p for p in programs if p['type']==0x6474e552]
 if not relros:raise ValueError('Missing GNU RELRO segment')
 for p in relros:
  end=p['vaddr']+p['memsz']
  if p['memsz']==0 or not any(q['flags']&2 and q['vaddr']<=p['vaddr'] and end<=q['vaddr']+q['memsz'] for q in loads):
   raise ValueError('Invalid RELRO coverage')
  page_end=(end+16383)//16384*16384
  if any(q['flags']&2 and max(q['vaddr'],end)<min(q['vaddr']+q['memsz'],page_end) for q in loads):
   raise ValueError('RELRO boundary shares a writable 16KB page')
 def offset(address):
  for p in loads:
   if p['vaddr']<=address<p['vaddr']+p['filesz']:return p['offset']+address-p['vaddr']
  raise ValueError('Address is not file-backed')
 tags=[]
 for p in programs:
  if p['type']==2:
   for i in range(0,p['filesz'],16):
    t,v=struct.unpack_from('<qQ',data,p['offset']+i)
    if t==0:break
    tags.append((t,v))
 strtab=offset(next(v for t,v in tags if t==5))
 def cstr(start):return data[start:data.index(b'\0',start)].decode()
 needed=[cstr(strtab+v) for t,v in tags if t==1]
 soname=[cstr(strtab+v) for t,v in tags if t==14]
 if soname!=[expected_soname]:raise ValueError('Expected plain Android SONAME '+expected_soname)
 if not set(needed)<=ALLOWED_DEPENDENCIES:raise ValueError('Unexpected dynamic dependency: '+str(needed))
 if any(t in (15,29) for t,v in tags):raise ValueError('Unexpected RPATH/RUNPATH')
 sections=[struct.unpack_from('<IIQQQQIIQQ',data,h[6]+i*h[11]) for i in range(h[12])]
 exports=set()
 for s in sections:
  if s[1]!=11:continue
  strings=sections[s[6]]
  for i in range(0,s[5],s[9]):
   name,info,other,shndx,value,size=struct.unpack_from('<IBBHQQ',data,s[4]+i)
   if shndx!=0 and info>>4 in (1,2) and other&3 in (0,3):
    exports.add(cstr(strings[4]+name))
 if not required_exports<=exports:raise ValueError('Missing C API exports: '+str(required_exports-exports))
 if exact_exports and exports!=required_exports:raise ValueError('Unexpected public exports: '+str(exports-required_exports))
 return {'machine':machine,'loadSegments':loads,'relro':[p for p in programs if p['type']==0x6474e552],'needed':needed,'soname':soname[0],'requiredCExports':sorted(required_exports),'exportCount':len(exports)}
def inspect():
 result=[]
 for abi,machine in EXPECTED_ABIS.items():
  so=ROOT/'artifacts'/abi/'libopencc.so'
  result.append({'path':str(so.relative_to(ROOT)).replace('\\','/'),'sizeBytes':so.stat().st_size,'sha256':digest(so),'elf':inspect_elf(so.read_bytes(),machine)})
  static=ROOT/'artifacts'/abi/'static'
  for name in ['libopencc.a','libmarisa.a']:
   p=static/'lib'/name
   if not p.read_bytes().startswith(b'!<arch>\n'):raise ValueError('Not an archive: '+str(p))
  for required in ['include/opencc/opencc.h','include/opencc/Config.hpp','include/marisa.h','lib/cmake/opencc/OpenCCConfig.cmake']:
   if not (static/required).is_file():raise ValueError('Incomplete static prefix: '+required)
  for p in sorted(static.rglob('*'),key=lambda item:item.relative_to(static).parts):
   if p.is_file():result.append({'path':str(p.relative_to(ROOT)).replace('\\','/'),'sizeBytes':p.stat().st_size,'sha256':digest(p)})
 return result
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--write-manifest',action='store_true');args=parser.parse_args()
 records=inspect()
 manifest={'schemaVersion':1,'sourceCommit':'025f371dc76b598d77384fbdab90c937471844d8','sourceLockSha256':digest(ROOT/'sources.lock.json'),'integrationSha256':digest(ROOT/'integration/CMakeLists.txt'),'buildScriptSha256':digest(ROOT/'scripts/build.sh'),'minimumElfLoadAlignment':16384,'androidRuntimeExecuted':False,'files':records}
 path=ROOT/'artifacts/manifest.json'
 if args.write_manifest:path.write_text(json.dumps(manifest,indent=2)+'\n')
 elif manifest!=json.loads(path.read_text()):raise ValueError('Artifact manifest or build input identity mismatch')
 print(json.dumps({'validated':True,'sharedLibraries':2,'manifestEntries':len(records),'androidRuntimeExecuted':False}))
if __name__=='__main__':main()