"""Deterministic symbol-level extraction, keeping native runtime code unchanged."""
import ast, hashlib, json, pathlib, sys, shutil, re
root = pathlib.Path(sys.argv[1]); target = pathlib.Path(sys.argv[2]) if len(sys.argv)>2 else pathlib.Path(__file__).resolve().parents[1] / '.artifacts/extraction/native'
mods = {'.'.join(p.relative_to(root).with_suffix('').parts).removesuffix('.__init__'):p for p in root.rglob('*.py')}
trees = {n:ast.parse(p.read_text()) for n,p in mods.items()}; selected={}; wanted={}; queue=[]
def demand(mod,name):
 if mod not in mods:return
 names=wanted.setdefault(mod,set())
 if name not in names:names.add(name);queue.append(mod)
def names(node):
 if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):return {node.name}
 if isinstance(node,(ast.Import,ast.ImportFrom)):return {a.asname or a.name.split('.')[0] for a in node.names}
 return {x.id for x in ast.walk(node) if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Store)}
def import_deps(mod,node):
 for x in ast.walk(node):
  if isinstance(x,ast.ImportFrom):
   name=x.module or ''
   if x.level:name='.'.join(mod.split('.')[:-x.level]+name.split('.')).strip('.')
   elif name.startswith('amplifier_web.'):name=name[len('amplifier_web.'):]
   else:continue
   for alias in x.names:
    if name in mods:demand(name,alias.name)
    elif '.'.join(filter(None,[name,alias.name])) in mods:
     sub='.'.join(filter(None,[name,alias.name])); local=alias.asname or alias.name
     attrs={a.attr for a in ast.walk(trees[mod]) if isinstance(a,ast.Attribute) and isinstance(a.value,ast.Name) and a.value.id==local}
     for attr in attrs:demand(sub,attr)
  if isinstance(x,ast.Import):
   for alias in x.names:
    if alias.name.startswith('amplifier_web.'):demand(alias.name[len('amplifier_web.'):],'*')
demand('runtime_worker','*')
while queue:
 mod=queue.pop(0); keeps=selected.setdefault(mod,set()); tree=trees[mod]
 for index,node in enumerate(tree.body):
  if index in keeps:continue
  if '*' not in wanted[mod] and not names(node)&wanted[mod] and not (isinstance(node,ast.ImportFrom) and node.module=='__future__'):continue
  keeps.add(index)
  refs={x.id for x in ast.walk(node) if isinstance(x,ast.Name) and isinstance(x.ctx,ast.Load)}
  for name in refs:demand(mod,name)
  import_deps(mod,node)
manifest={}
for mod,indices in sorted(selected.items()):
 source=mods[mod].read_text(); lines=source.splitlines(keepends=True); out=[]
 for index,node in enumerate(trees[mod].body):
  if index not in indices:continue
  if index == 0 and isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):continue
  start=min([node.lineno]+[d.lineno for d in getattr(node,'decorator_list',[])])-1
  out.append(''.join(lines[start:node.end_lineno]))
 text='"""Extracted native application runtime; provenance in extraction.json."""\n'+ '\n\n'.join(out)
 text=re.sub(r'\bamplifier_web\b', 'amplifier_acp.native', text)
 path=target / (mod.replace('.','/')+'.py');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
 manifest[mod]={'source':str(mods[mod].relative_to(root)),'sha256':hashlib.sha256(source.encode()).hexdigest(),'symbols': sorted(wanted[mod]), 'lines':len(text.splitlines())}
for directory in [target,*[p for p in target.rglob('*') if p.is_dir()]]:
 (directory/'__init__.py').touch()
for folder in ['runtime_deps','bundle_data']:
 if (root/folder).is_dir():shutil.copytree(root/folder,target/folder,dirs_exist_ok=True)
# Installed behavior resources retain their public bundle/module interfaces.
if not (target/'bundle_data').exists():
 for folder in ['behaviors','modules','context']:
  if (root.parent/folder).is_dir():shutil.copytree(root.parent/folder,target/'bundle_data'/folder,dirs_exist_ok=True)
(target.parent/'extraction.json').write_text(json.dumps({'sourceRevision':'828d2bff97a77fbdbd0f06429369f7ff1f4abf42','modules':manifest},indent=2)+'\n')
print(len(manifest),'modules',sum(r['lines'] for r in manifest.values()),'lines')
