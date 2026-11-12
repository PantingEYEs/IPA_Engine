#!/usr/bin/env python3
# Copyright 2026 PantingEYEs. All rights reserved.
"""Parse and validate Android ELF layout, dependencies and public ABI offline."""
from pathlib import Path
import argparse, hashlib, json, struct
ROOT=Path(__file__).resolve().parents[1]
ALLOWED_DEPENDENCIES={'libc.so','libm.so','libdl.so','liblog.so'}
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def inspect_elf(data, machine, expected_soname, required_exports, exact_exports=False):
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
