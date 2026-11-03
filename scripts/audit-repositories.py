#!/usr/bin/env python3
"""Read-only Git hygiene scan. Reports paths/types, never credential contents.
Pattern/known-current-secret matching is not a complete secret or legal audit.
"""
import argparse,json,re,subprocess
from pathlib import Path
NAMES=['project-agora-FE','project-agora-BE','project-agora-DB','project-agora-Wall','project-orchestra','hypervisor-for-agora']
PATTERNS={'private_key':re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----(?:\r?\n|\\n)[A-Za-z0-9+/]{40,}'),'google_client_secret':re.compile(rb'GOCSPX-[A-Za-z0-9_-]{20,}'),'github_token':re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),'aws_access_key':re.compile(rb'AKIA[A-Z0-9]{16}')}
def git(repo,*args):return subprocess.check_output(['git',*args],cwd=repo)
def audit(root,history):
 secrets=set()
 for name in NAMES:
  repo=root/name
  for path in [repo/'.env',*repo.glob('*/.env')]:
   if not path.is_file():continue
   for line in path.read_text().splitlines():
    if '=' not in line or line.lstrip().startswith('#'):continue
    key,value=line.split('=',1);value=value.strip().strip('\"\'')
    if re.search(r'SECRET|PASSWORD|API_TOKEN|(?:ENCRYPTION|SIGNING|PRIVATE)_KEY',key) and len(value)>=16 and not re.search(r'change.me|example|placeholder|test.only',value,re.I):secrets.add(value.encode())
 results=[]
 for name in NAMES:
  repo=root/name;tracked=[p for p in git(repo,'ls-files','-z').decode().split('\0') if p];findings=[]
  def scan(data,path,revision):
   kinds=[kind for kind,pattern in PATTERNS.items() if pattern.search(data)]
   if any(value in data for value in secrets):kinds.append('known_current_credential')
   if kinds:findings.append({'path':path,'revision':revision,'types':kinds})
  candidates=git(repo,'ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0')
  for path in sorted(set(p for p in candidates if p)):
   file=repo/path
   if file.is_file() and file.stat().st_size<5_000_000:scan(file.read_bytes(),path,'working-tree')
  if history:
   objects=git(repo,'rev-list','--objects','--all').decode().splitlines()
   proc=subprocess.Popen(['git','cat-file','--batch'],cwd=repo,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
   for entry in objects:
    oid,_,path=entry.partition(' ')
    proc.stdin.write((oid+'\n').encode());proc.stdin.flush();header=proc.stdout.readline().split()
    if len(header)!=3:continue
    size=int(header[2]);data=proc.stdout.read(size);proc.stdout.read(1)
    if header[1]==b'blob' and size<5_000_000:scan(data,path,oid[:12])
   proc.stdin.close();proc.wait()
  ignored_tests=['.env','.env.local','.envrc','client_secret_local.json','service-account-local.json','application_default_credentials.json','test-results/example.txt','example.key']
  missed=[p for p in ignored_tests if subprocess.run(['git','check-ignore','--no-index','-q',p],cwd=repo).returncode]
  source_tests=['LICENSE','.env.example','docs/audit.md','src/example.js','systemd/example.socket']
  if name=='project-agora-FE':source_tests+=['build/exportDist.js']
  if name=='project-agora-BE':source_tests+=['Makefile','spring/gradle/wrapper/gradle-wrapper.jar','spring/src/main/resources/static/test.html']
  hidden=[p for p in source_tests if not subprocess.run(['git','check-ignore','--no-index','-q',p],cwd=repo).returncode]
  results.append({'repository':name,'tracked_files':len(tracked),'missed_ignore_cases':missed,'source_paths_wrongly_ignored':hidden,'tracked_but_ignored':git(repo,'ls-files','-ci','--exclude-standard').decode().splitlines(),'credential_findings':findings})
 return {'scope':'Local working trees and locally available Git refs; excludes remote-only refs, submodule histories and unknown secret formats','history_scanned':history,'repositories':results}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]);p.add_argument('--history',action='store_true');a=p.parse_args();print(json.dumps(audit(a.root,a.history),ensure_ascii=False,indent=2))
