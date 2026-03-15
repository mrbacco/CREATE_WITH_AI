Set-Content -Path test_upload.txt -Value 'hello world from test file'
 = Invoke-RestMethod -Uri 'http://127.0.0.1:5050/upload' -Method Post -Form @{file=Get-Item 'test_upload.txt'}
 | ConvertTo-Json
 = Invoke-RestMethod -Uri 'http://127.0.0.1:5050/documents'
 | ConvertTo-Json
if (.documents.Count -gt 0) {
   = .documents[0].file_id
   = Invoke-RestMethod -Uri ('http://127.0.0.1:5050/read_file?file_id=' + [uri]::EscapeDataString())
   | ConvertTo-Json
}
