import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class StaticAssetsTest(unittest.TestCase):
    def test_production_keeps_published_version_until_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'static'; source.mkdir(); published=root/'assets'; published.mkdir()
            for name in ('style.css','app.js','favicon.svg'): (source/name).write_text(name)
            with patch.object(app,'STATIC_DIR',source):
                versions={name:app.content_version(name) for name in ('style.css','app.js','favicon.svg')}
                published_name=f'style.{versions["style.css"]}.css'
                (published/published_name).write_text('style.css')
                with patch.object(app,'PRODUCTION_ASSET_VERSIONS',versions),patch.object(app,'PUBLIC_ASSET_DIR',published):
                    (source/'style.css').write_text('nuevo estilo')
                    client=app.app.test_client()
                    self.assertIn('/assets/'+published_name,client.get('/').get_data(as_text=True))
                    response=client.get('/assets/'+published_name)
                    self.assertEqual(response.get_data(as_text=True),'style.css')
                    response.close()

    def test_versions_follow_content_and_cache_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'index.html').write_text((app.TEMPLATE_DIR/'index.html').read_text(encoding='utf-8'), encoding='utf-8')
            (root/'style.css').write_text('body { color: red; }', encoding='utf-8')
            (root/'app.js').write_text('console.log(1);', encoding='utf-8')
            (root/'favicon.svg').write_text('<svg/>', encoding='utf-8')
            with patch.object(app, 'STATIC_DIR', root), patch.object(app, 'TEMPLATE_DIR', root):
                client = app.app.test_client()
                page = client.get('/')
                self.assertIn('no-cache', page.headers['Cache-Control'])
                html = page.get_data(as_text=True)
                versions = {name+'.'+ext:version for name,version,ext in re.findall(r'/assets/(style|app|favicon)\.([0-9a-f]{16})\.(css|js|svg)', html)}
                self.assertEqual(set(versions), {'style.css', 'app.js', 'favicon.svg'})
                deep = client.get('/t/12345678-1234-1234-1234-123456789abc')
                self.assertIn(f'/assets/style.{versions["style.css"]}.css', deep.get_data(as_text=True))
                self.assertIn('no-cache', deep.headers['Cache-Control'])

                url='/assets/style.'+versions['style.css']+'.css'
                css = client.get(url)
                self.assertIn('immutable', css.headers['Cache-Control'])
                self.assertIn('max-age=31536000', css.headers['Cache-Control'])
                cached = client.get(url, headers={'If-None-Match': css.headers['ETag']})
                self.assertEqual(cached.status_code, 304)
                css.close(); cached.close()
                (root/'style.css').write_text('body { color: blue; }', encoding='utf-8')
                updated = client.get('/').get_data(as_text=True)
                next_versions = {name+'.'+ext:version for name,version,ext in re.findall(r'/assets/(style|app|favicon)\.([0-9a-f]{16})\.(css|js|svg)', updated)}
                self.assertNotEqual(versions['style.css'], next_versions['style.css'])
                self.assertEqual(versions['app.js'], next_versions['app.js'])
                outdated = client.get(url)
                unversioned = client.get('/app.js')
                self.assertEqual(outdated.status_code,404)
                self.assertIn('no-cache', unversioned.headers['Cache-Control'])
                outdated.close(); unversioned.close()


if __name__ == '__main__':
    unittest.main()
