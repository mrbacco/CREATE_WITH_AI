import urllib.request, urllib.error
print('models', urllib.request.urlopen('http://127.0.0.1:5050/models').status)
try:
    r = urllib.request.urlopen('http://127.0.0.1:5050/analyze_file')
    print('analyze', r.status, r.read().decode())
except urllib.error.HTTPError as e:
    print('analyze err', e.code, e.read().decode())
