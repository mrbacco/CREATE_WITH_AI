import http.client, urllib.parse, json, pathlib, mimetypes
fn = 'test_upload.txt'
pathlib.Path(fn).write_text('hello world from test file')
boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
lines = []
lines.append('--' + boundary)
lines.append('Content-Disposition: form-data; name="file"; filename="%s"' % fn)
lines.append('Content-Type: %s' % (mimetypes.guess_type(fn)[0] or 'application/octet-stream'))
lines.append('')
lines.append(pathlib.Path(fn).read_text())
lines.append('--' + boundary + '--')
body = '\r\n'.join(lines)
headers = {'Content-Type': 'multipart/form-data; boundary=' + boundary, 'Content-Length': str(len(body))}
conn = http.client.HTTPConnection('127.0.0.1', 5050)
conn.request('POST', '/upload', body, headers)
r = conn.getresponse(); print('upload', r.status, r.read().decode())
conn.request('GET', '/documents')
r = conn.getresponse(); txt = r.read().decode(); print('documents', r.status, txt)
docs = json.loads(txt).get('documents', [])
print('count', len(docs))
if docs:
    fid = docs[0]['file_id']
    conn.request('GET', '/read_file?' + urllib.parse.urlencode({'file_id': fid}))
    r = conn.getresponse(); print('read', r.status, r.read().decode())
