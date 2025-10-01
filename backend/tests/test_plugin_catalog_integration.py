import os
import shutil
import tempfile
import time
from pathlib import Path
from fastapi.testclient import TestClient
from stash_ai_server.main import app
from stash_ai_server.db.session import SessionLocal

client = TestClient(app)

# These tests expect network access to GitHub to download the registry repo and plugin contents.
# If running offline, skip or mock httpx responses.

BUILTIN_SOURCE = 'official'

def test_end_to_end_plugin_catalog_lifecycle():
    db = SessionLocal()
    try:
        # Ensure source exists (create pointing at the published catalog repo raw URL)
        src_url = 'https://raw.githubusercontent.com/skier233/AIOverhaul_Plugin_Catalog_Official/main'
        resp = client.post('/api/v1/plugins/sources', json={'name': BUILTIN_SOURCE, 'url': src_url, 'enabled': True})
        assert resp.status_code in (200, 201)

        # Refresh the source (pull plugins_index.json from the repo)
        resp = client.post(f'/api/v1/plugins/sources/{BUILTIN_SOURCE}/refresh')
        print('REFRESH:', resp.status_code, resp.text)
        assert resp.status_code == 200
        data = resp.json()
        print('REFRESH JSON:', data)
        assert 'fetched' in data

        # List catalog entries
        resp = client.get(f'/api/v1/plugins/catalog/{BUILTIN_SOURCE}')
        assert resp.status_code == 200
        catalog = resp.json()
        print('CATALOG RESP:', len(catalog))
        assert isinstance(catalog, list)
        if not catalog:
            # Nothing to test further
            return
        entry = catalog[0]
        plugin_name = entry['plugin_name']
        print('CATALOG FIRST ENTRY:', entry)

        # Install first plugin
        resp = client.post('/api/v1/plugins/install', json={'source': BUILTIN_SOURCE, 'plugin': plugin_name, 'overwrite': True})
        print('INSTALL RESP STATUS:', resp.status_code)
        assert resp.status_code == 200
        j = resp.json()
        print('INSTALL RESP JSON:', j)
        # Show filesystem state under backend/stash_ai_server/plugins
        backend_root = Path(__file__).resolve().parents[1]
        plugins_dir = backend_root / 'stash_ai_server' / 'plugins'
        print('PLUGINS DIR:', plugins_dir)
        try:
            contents = [p.name for p in plugins_dir.iterdir()]
        except Exception as _:
            contents = None
        print('PLUGINS DIR CONTENTS:', contents)
        plugin_path = plugins_dir / plugin_name
        print('PLUGIN PATH EXISTS:', plugin_path.exists())
        if plugin_path.exists():
            all_files = [str(p.relative_to(plugin_path)) for p in plugin_path.rglob('*') if p.is_file()]
        else:
            all_files = []
        print('PLUGIN FILES:', all_files)
        assert j['status'] in ('installed',)

        # Update same plugin (idempotent)
        resp = client.post('/api/v1/plugins/update', json={'source': BUILTIN_SOURCE, 'plugin': plugin_name})
        print('UPDATE RESP STATUS:', resp.status_code)
        assert resp.status_code == 200
        j = resp.json()
        print('UPDATE RESP JSON:', j)
        assert j['status'] in ('updated',)

        # Remove plugin
        resp = client.post('/api/v1/plugins/remove', json={'plugin': plugin_name})
        print('REMOVE RESP STATUS:', resp.status_code)
        assert resp.status_code == 200
        j = resp.json()
        print('REMOVE RESP JSON:', j)
        # show plugin folder now
        print('PLUGIN PATH EXISTS AFTER REMOVE:', plugin_path.exists())
        assert j['status'] == 'removed'
    finally:
        try:
            db.close()
        except Exception:
            pass
