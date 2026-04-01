from ml.supabase_client import get_client
from urllib.parse import urlparse
import re
url='https://www.airbnb.com/rooms/12345678'
parsed=urlparse(url)
m=re.search(r'/rooms/(\d+)', parsed.path)
room_id=m.group(1) if m else None
print('room_id=', room_id)
client=get_client()
rows=client.table('saved_listings').select('id,name,input_attributes').execute().data or []
print('total saved_listings=', len(rows))
matches=[]
for row in rows:
    attrs=row.get('input_attributes') or {}
    for key in ['listingUrl','listing_url','input_listing_url']:
        val=attrs.get(key)
        if isinstance(val, str):
            if val.strip().rstrip('/') == url.strip().rstrip('/') or (room_id and '/rooms/'+room_id in val):
                matches.append((row['id'], row.get('name'), key, val))
print('found matches=', len(matches))
for m in matches[:20]:
    print(m)
