$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
$storageNames = @('mysql', 'redis', 'etcd', 'minio', 'milvus') | ForEach-Object { "telecom-ops-rag-$_-1" }
Push-Location $projectRoot
try {
    # These three old services use the legacy collection/model binding and collide
    # with the reviewed host entry on localhost/IPv6. Preserve the five storages.
    & docker stop telecom-ops-rag-api-1 telecom-ops-rag-ingest-worker-1 telecom-ops-rag-enterprise-mock-1
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the conflicting legacy entry.' }
    & docker start @storageNames
    if ($LASTEXITCODE -ne 0) { throw 'Original local storage start failed.' }
    $deadline = (Get-Date).AddSeconds(180)
    do {
        $storageStatus = & docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' @storageNames
        if ($LASTEXITCODE -ne 0) { throw 'Original local storage inspection failed.' }
        if (@($storageStatus | Where-Object { $_ -notin @('healthy', 'running') }).Count -eq 0) { break }
        if ((Get-Date) -gt $deadline) { throw 'Original local storage readiness timed out.' }
        Start-Sleep -Seconds 1
    } while ($true)
    & $pythonPath -c "from scripts.original_local_runtime import build_profile;from scripts.prepare_original_citation_release import wait_readable_collections;from pymilvus import MilvusClient;s=build_profile();c=MilvusClient(uri=s.milvus_uri,timeout=120);wait_readable_collections(c,[s.doc_collection,s.faq_collection]);c.close()"
    if ($LASTEXITCODE -ne 0) { throw 'Original local collections are not readable.' }
    & $pythonPath -m scripts.original_local_runtime start
    if ($LASTEXITCODE -ne 0) { throw 'Original local entry start failed; inspect owned runtime state.' }
} finally { Pop-Location }
