import re, sys, json, pathlib, yaml
d = pathlib.Path(sys.argv[1])
rows = []
for f in sorted(d.glob("*.md")):
    txt = f.read_text(errors="replace")
    m = re.search(r"```yaml\n(.*?)\n```", txt, re.S)
    if not m:
        rows.append((f.name, "NOYAML", "", ""))
        continue
    try:
        spec = yaml.safe_load(m.group(1))
    except Exception as e:
        rows.append((f.name, "BADYAML", str(e)[:60], ""))
        continue
    paths = spec.get("paths") or {}
    for p, ops in paths.items():
        for meth, op in ops.items():
            tags = ",".join(op.get("tags") or [])
            rows.append((f.name, p, meth, tags))
for r in rows:
    print("\t".join(r))
