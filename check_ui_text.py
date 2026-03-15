import urllib.request
html = urllib.request.urlopen('http://127.0.0.1:5050/').read().decode('utf-8')
print('Upload & Analyze' in html)
