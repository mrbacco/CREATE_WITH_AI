import http.client, urllib.parse, json, pathlib
fn='test_upload.txt'
pathlib.Path(fn).write_text('hello world from test file\nthis is analysis test')
boundary='----WebKitFormBoundary7MA4YWxkTrZu0gW'
lines=[
    '--'+boundary,
    'Content-Disposition: form-data; name="file"; filename="'+fn+'"',
    'Content-Type: text/plain',
    '',
    pathlib.Path(fn).read_text(),
    '--'+boundary+'--',
    ''
]
body='\r\n'.join(lines)
conn=http.client.HTTPConnection('127.0.0.1',5050)
conn.request('POST','/analyze_file',body,{'Content-Type':'multipart/form-data; boundary='+boundary})
r=conn.getresponse();print('analyze-post',r.status,r.read().decode())
conn.request('GET','/documents')
r=conn.getresponse();docs=json.loads(r.read().decode())
print('docs count',len(docs.get('documents',[])))
if docs.get('documents'):
    fid=docs['documents'][0]['file_id']
    conn.request('GET','/analyze_file?'+urllib.parse.urlencode({'file_id':fid}))
    r=conn.getresponse();print('analyze-get',r.status,r.read().decode())
