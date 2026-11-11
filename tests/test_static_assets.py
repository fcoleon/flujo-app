import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from xml.etree import ElementTree


class StaticAssetsTest(unittest.TestCase):
    def test_favicon_reference_and_content(self):
        client=app.app.test_client()
        html=client.get('/').get_data(as_text=True)
        match=re.search(r'href="(/static/favicon\.svg\?v=[0-9a-f]{16})"',html)
        self.assertIsNotNone(match)
        response=client.get(match.group(1))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'image/svg+xml')
        self.assertEqual(ElementTree.fromstring(response.data).tag,'{http://www.w3.org/2000/svg}svg')
        response.close()

    def test_versions_follow_source_content_and_revalidate_cache(self):
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
                versions = dict(re.findall(r'/static/(style\.css|app\.js|favicon\.svg)\?v=([0-9a-f]{16})', html))
                self.assertEqual(set(versions), {'style.css', 'app.js', 'favicon.svg'})
                deep = client.get('/t/12345678-1234-1234-1234-123456789abc')
                self.assertIn(f'/static/style.css?v={versions["style.css"]}', deep.get_data(as_text=True))
                self.assertIn('no-cache', deep.headers['Cache-Control'])

                url='/static/style.css?v='+versions['style.css']
                css=client.get(url)
                self.assertIn('no-cache',css.headers['Cache-Control'])
                cached=client.get(url,headers={'If-None-Match':css.headers['ETag']})
                self.assertEqual(cached.status_code,304)
                css.close();cached.close()
                (root/'style.css').write_text('body { color: blue; }',encoding='utf-8')
                updated=client.get('/').get_data(as_text=True)
                next_versions=dict(re.findall(r'/static/(style\.css|app\.js|favicon\.svg)\?v=([0-9a-f]{16})',updated))
                self.assertNotEqual(versions['style.css'],next_versions['style.css'])
                self.assertEqual(versions['app.js'],next_versions['app.js'])
                current=client.get(url)
                self.assertEqual(current.get_data(as_text=True),'body { color: blue; }')
                self.assertIn('no-cache',current.headers['Cache-Control'])
                current.close()
                legacy=client.get('/assets/style.'+versions['style.css']+'.css')
                self.assertEqual(legacy.get_data(as_text=True),'body { color: blue; }')
                self.assertIn('no-cache',legacy.headers['Cache-Control']);legacy.close()
                self.assertEqual(client.get('/static/index.html').status_code,404)


if __name__ == '__main__':
    unittest.main()
