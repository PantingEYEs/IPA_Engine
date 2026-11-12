#!/usr/bin/env python3
"""Task-only APK audit. Fail closed on unaligned packaged ELF files or ZIP alignment."""
from pathlib import Path
import argparse, hashlib, json, re, struct, subprocess, sys, zipfile

parser=argparse.ArgumentParser()
parser.add_argument('--work',type=Path,required=True,help='Isolated IPA Board checkout')
parser.add_argument('--sdk',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
work=args.work.resolve(); sdk=args.sdk.resolve(); logs=args.output.resolve()
logs.mkdir(parents=True,exist_ok=True)
def version(path):
    return tuple(int(value) for value in re.findall(r"\d+",path.parent.name))
tools=sorted(sdk.glob("build-tools/*/zipalign"),key=version)
if not tools:
    raise SystemExit("zipalign is missing")
zipalign=tools[-1]
apks=[work/"app/build/outputs/apk/debug/app-debug.apk",
      work/"app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"]
report={"pageSize":16384,"zipalign":str(zipalign.relative_to(sdk)),"apks":[],"errors":[]}

def inspect_elf(content,name):
    if content[:6]!=b"\x7fELF\x02\x01":
        raise ValueError("Expected a little-endian ELF64 shared object: "+name)
    if len(content)<64:
        raise ValueError("Truncated ELF: "+name)
    offset=struct.unpack_from("<Q",content,32)[0]
    size,count=struct.unpack_from("<HH",content,54)
    if size<56 or not count or offset+size*count>len(content):
        raise ValueError("Invalid program-header table: "+name)
    headers=[]
    for index in range(count):
        fields=struct.unpack_from("<IIQQQQQQ",content,offset+index*size)
        kind,flags,fileoff,vaddr,physical,filesz,memsz,align=fields
        headers.append({"type":kind,"flags":flags,"offset":fileoff,"vaddr":vaddr,
                        "filesz":filesz,"memsz":memsz,"align":align})
    loads=[item for item in headers if item["type"]==1]
    if not loads:
        raise ValueError("ELF has no PT_LOAD: "+name)
    issues=[]
    for item in loads:
        if item["align"]<16384 or item["align"]&(item["align"]-1):
            issues.append("PT_LOAD alignment below 16 KB or not a power of two")
        if item["offset"]%16384!=item["vaddr"]%16384:
            issues.append("PT_LOAD offset/vaddr are not congruent modulo 16 KB")
        if item["filesz"]>item["memsz"] or item["offset"]+item["filesz"]>len(content):
            issues.append("PT_LOAD size exceeds valid ELF bounds")
    relros=[item for item in headers if item["type"]==0x6474e552]
    for relro in relros:
        end=relro["vaddr"]+relro["memsz"]
        for load in loads:
            loadend=load["vaddr"]+load["memsz"]
            if load["flags"]&2 and load["vaddr"]<=relro["vaddr"] and end<=loadend:
                if end%16384!=0 and end!=loadend:
                    issues.append("GNU_RELRO ends inside an unaligned writable page")
    return {"entry":name,"bytes":len(content),"sha256":hashlib.sha256(content).hexdigest(),
            "machine":struct.unpack_from("<H",content,18)[0],"loads":loads,
            "relro":relros,"errors":issues}

for apk in apks:
    entry={"path":apk.relative_to(work).as_posix(),"nativeFiles":[]}
    report["apks"].append(entry)
    if not apk.is_file():
        report["errors"].append("Missing APK: "+str(apk))
        continue
    with apk.open("rb") as source:
        entry["sha256"]=hashlib.file_digest(source,"sha256").hexdigest()
    ziplog=logs/(apk.name+".zipalign16k.txt")
    with ziplog.open("w") as output:
        result=subprocess.run([str(zipalign),"-c","-P","16","-v","4",str(apk)],
                              stdout=output,stderr=subprocess.STDOUT)
    entry["zipalignExitCode"]=result.returncode
    entry["zipalignLog"]=ziplog.name
    if result.returncode:
        report["errors"].append("ZIP alignment failed: "+apk.name)
    with zipfile.ZipFile(apk) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.endswith(".so"):
                continue
            try:
                native=inspect_elf(archive.read(member),member.filename)
                native["compressed"]=member.compress_type!=zipfile.ZIP_STORED
                entry["nativeFiles"].append(native)
                report["errors"].extend(apk.name+":"+member.filename+": "+error for error in native["errors"])
            except (ValueError,struct.error) as error:
                report["errors"].append(apk.name+":"+str(error))
    print(apk.name+": inspected "+str(len(entry["nativeFiles"]))+" ELF files; zipalign exit="+str(result.returncode))
report["success"]=not report["errors"]
(logs/"apk-16kb-audit.json").write_text(json.dumps(report,indent=2)+"\n")
if report["errors"]:
    raise SystemExit("\n".join(report["errors"]))
print("Every packaged .so passed ELF LOAD/RELRO and both APKs passed zipalign -c -P 16 4")
