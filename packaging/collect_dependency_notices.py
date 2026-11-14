#!/usr/bin/env python3
import json,re,shutil
from pathlib import Path
root=Path(__file__).resolve().parents[1]
import argparse
parser=argparse.ArgumentParser(description='Collect locked Cargo/frontend license notices; run cargo vendor and npm ci first.')
parser.add_argument('--vendor',type=Path,required=True)
parser.add_argument('--output',type=Path,default=root/'release-artifacts/dependency-notices')
args=parser.parse_args()
vendor=args.vendor;out=args.output;out.mkdir(parents=True,exist_ok=True)
version=json.loads((root/'src-tauri/tauri.conf.json').read_text())['version']
rows=[]; missing=[]
for d in sorted(vendor.iterdir()):
 if not d.is_dir():continue
 import tomllib
 m=tomllib.loads((d/'Cargo.toml').read_text())['package']
 dest=out/'rust'/f"{m['name']}-{m['version']}";dest.mkdir(parents=True,exist_ok=True)
 files=[p for p in d.rglob('*') if p.is_file() and re.search(r'(^|[-_.])(license|copying|copyright|notice)([-_.]|$)',p.name,re.I) and p.stat().st_size<500000 and p.suffix.lower() not in {'.rs','.c','.cpp','.h','.hpp','.py','.js','.ts'}]
 for p in files:
  q=dest/p.relative_to(d);q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
 (dest/'ATTRIBUTION.json').write_text(json.dumps({k:m[k] for k in ['name','version','authors','license','license-file','repository','homepage','description'] if k in m},indent=2)+'\n')
 if not files:
  missing.append(m['name'])
  for p in d.iterdir():
   if p.is_file() and p.name.lower().startswith('readme'):shutil.copy2(p,dest/p.name)
 rows.append(f"| {m['name']} | {m['version']} | {m.get('license',m.get('license-file','see source'))} |")
for name in ['svelte','@tauri-apps/api']:
 d=root/'node_modules'/name;m=json.loads((d/'package.json').read_text());dest=out/'frontend'/name;dest.mkdir(parents=True,exist_ok=True)
 for p in d.iterdir():
  if p.is_file() and re.search('license|copying|copyright|notice',p.name,re.I):shutil.copy2(p,dest/p.name)
 rows.append(f"| {name} | {m['version']} | {m.get('license','see source')} |")
shutil.copy2(root/'public/fonts/OFL.txt',out/'Manrope-OFL.txt')
(out/'THIRD_PARTY.md').write_text('''# Muse Video Bridge dependency notices\n\nMuse Video Bridge source is MIT licensed. Third-party components retain their own\nlicenses and copyrights. This directory carries the Rust/frontend/font license\ntexts, including the compiled Rust crates and additional locked build dependencies.\n\nComplete locked Rust crate source (including MPL-2.0 serialport and option-ext)\nis supplied in the same GitHub Release as\n`Muse-Video-Bridge-0.3.0-dependency-sources.tar.gz`. The crate source archive includes\noriginal license texts, Cargo manifests and checksum metadata. Muse source, lockfiles\nand build instructions are at https://github.com/ysf-kul/musevideobridge/tree/v0.3.0.\nRebuild the app with modified libraries using those sources and the normal Cargo/Tauri\ncommands. No restriction is added on modifying library code or debugging such changes.\n\nLinux GTK/WebKitGTK and Windows WebView2 are installed separately. Linux FFmpeg is\nexternal. The Windows portable package has separate FFmpeg notices and matching source\nfor its bundled conversion tools; those tools run as replaceable separate processes.\nThe desktop application does not link FFmpeg. Firmware dependencies have separate\nnotices under firmware/vendor and the pinned PlatformIO libraries.\n\n| Component | Version | Declared license |\n| --- | --- | --- |\n'''.replace('0.3.0',version)+ '\n'.join(rows)+'\n')
print('Notices:',len(rows),'crates without root/recursive named license files:',missing)
