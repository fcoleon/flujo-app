import re
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from PIL import Image


class StaticAssetsTest(unittest.TestCase):
    def test_slack_logo_is_transparent_and_served(self):
        response=app.app.test_client().get('/static/slack-logo.png')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'image/png')
        self.assertIn('no-cache',response.headers['Cache-Control'])
        with Image.open(io.BytesIO(response.data)) as image:
            self.assertEqual(image.size,(200,200))
            self.assertEqual(image.getchannel('A').getextrema(),(0,255))
        response.close()
        self.assertIn('<img class="slack-icon" src="/static/slack-logo.png" alt="" aria-hidden="true">',(app.STATIC_DIR/'app.js').read_text(encoding='utf-8'))

    def test_favicon_reference_and_content(self):
        client=app.app.test_client()
        html=client.get('/').get_data(as_text=True)
        match=re.search(r'href="(/static/favicon\.png\?v=[0-9a-f]{16})"',html)
        self.assertIsNotNone(match)
        response=client.get(match.group(1))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'image/png')
        with Image.open(io.BytesIO(response.data)) as image:
            self.assertEqual(image.size,(200,200))
            self.assertEqual(image.getchannel('A').getextrema(),(0,255))
        response.close()
        self.assertEqual(client.get('/static/favicon.svg').status_code,200)
        script=(app.STATIC_DIR/'app.js').read_text(encoding='utf-8')
        self.assertNotIn('class="brandmark">✳',script)
        self.assertIn("url('/static/favicon.png')",(app.STATIC_DIR/'style.css').read_text(encoding='utf-8'))

    def test_versions_follow_source_content_and_revalidate_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'index.html').write_text((app.TEMPLATE_DIR/'index.html').read_text(encoding='utf-8'), encoding='utf-8')
            (root/'style.css').write_text('body { color: red; }', encoding='utf-8')
            (root/'app.js').write_text('console.log(1);', encoding='utf-8')
            (root/'automations.js').write_text('console.log(2);', encoding='utf-8')
            scripts=('navigation.js','slack.js','project-filters.js','attachments.js','task-detail.js')
            for name in scripts: (root/name).write_text('original '+name,encoding='utf-8')
            (root/'favicon.png').write_bytes(b'png-test')
            with patch.object(app, 'STATIC_DIR', root), patch.object(app, 'TEMPLATE_DIR', root):
                client = app.app.test_client()
                page = client.get('/')
                self.assertIn('no-cache', page.headers['Cache-Control'])
                html = page.get_data(as_text=True)
                versions = dict(re.findall(r'/static/([\w.-]+)\?v=([0-9a-f]{16})', html))
                self.assertEqual(set(versions), {'style.css', 'app.js', 'automations.js', 'favicon.png', *scripts})
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
                next_versions=dict(re.findall(r'/static/([\w.-]+)\?v=([0-9a-f]{16})',updated))
                self.assertNotEqual(versions['style.css'],next_versions['style.css'])
                self.assertEqual(versions['app.js'],next_versions['app.js'])
                script=client.get('/static/automations.js?v='+versions['automations.js'])
                self.assertEqual(script.get_data(as_text=True),'console.log(2);')
                self.assertIn('no-cache',script.headers['Cache-Control'])
                cached_script=client.get('/static/automations.js?v='+versions['automations.js'],headers={'If-None-Match':script.headers['ETag']})
                self.assertEqual(cached_script.status_code,304)
                script.close();cached_script.close()
                (root/'automations.js').write_text('console.log(3);',encoding='utf-8')
                self.assertNotIn('/static/automations.js?v='+versions['automations.js'],client.get('/').get_data(as_text=True))
                for name in scripts:
                    with self.subTest(name=name):
                        script_url='/static/'+name+'?v='+versions[name]
                        response=client.get(script_url)
                        self.assertEqual(response.get_data(as_text=True),'original '+name)
                        self.assertIn('no-cache',response.headers['Cache-Control'])
                        cached=client.get(script_url,headers={'If-None-Match':response.headers['ETag']})
                        self.assertEqual(cached.status_code,304)
                        response.close();cached.close()
                        (root/name).write_text('changed '+name,encoding='utf-8')
                        self.assertNotIn(script_url,client.get('/').get_data(as_text=True))
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
