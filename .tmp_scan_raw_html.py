import re
from pathlib import Path
paths=[Path('worker/logs/worker.log')]+sorted(Path('worker/logs').glob('worker.log.[1-5]'))
pat=re.compile(r'Playwright PDP raw html listing=(\d+) final_url=([^ ]+) html=(.*)$')
pat2=re.compile(r'\[price_extract_raw_html\] url=([^ ]+) checkin=([^ ]+) checkout=([^ ]+) html=(.*)$')
for p in paths:
    try:
        lines=p.read_text(encoding='utf-8',errors='ignore').splitlines()
    except Exception:
        continue
    for i,l in enumerate(lines,1):
        m=pat.search(l)
        if m:
            listing, url, html=m.groups()
            print(f'PDP {p}:{i} listing={listing} url={url} html_len={len(html)}')
    for i,l in enumerate(lines,1):
        m=pat2.search(l)
        if m:
            url,ci,co,html=m.groups()
            print(f'EXTRACT {p}:{i} ci={ci} co={co} url={url} html_len={len(html)}')
