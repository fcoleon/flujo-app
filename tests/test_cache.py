import json
import unittest
from unittest.mock import MagicMock, patch

from cache import Cache


class CacheTest(unittest.TestCase):
    def test_disabled_never_accesses_backend(self):
        cache=Cache({'type':''})
        with patch.object(cache,'_call') as call:
            self.assertEqual(cache.get_or_create('key',lambda:'url',240),'url')
            call.assert_not_called()

    def test_hit_expiry_and_backend_failure(self):
        cache=Cache({'type':'memcached','host':'localhost','port':11211})
        factory=MagicMock(return_value='nueva')
        with patch('cache.time.time',return_value=100),patch.object(cache,'_call',return_value=json.dumps([101,'vigente'])) as call:
            self.assertEqual(cache.get_or_create('key',factory,240),'vigente')
            factory.assert_not_called()
            call.assert_called_once_with('get','key')
        with patch('cache.time.time',return_value=101),patch.object(cache,'_call',side_effect=[json.dumps([101,'antigua']),True]) as call:
            self.assertEqual(cache.get_or_create('key',factory,240),'nueva')
            self.assertEqual(json.loads(call.call_args.args[2]),[341,'nueva'])
            self.assertEqual(call.call_args.args[3],240)
        with patch.object(cache,'_call',side_effect=OSError('secreto')):
            with self.assertLogs('cache',level='WARNING') as logs:
                self.assertEqual(cache.get_or_create('key',factory,240),'nueva')
            self.assertNotIn('secreto',str(logs.output))

    def test_redis_adapter(self):
        backend=MagicMock()
        with patch('redis.Redis.from_url',return_value=backend):
            cache=Cache({'type':'redis','url':'redis://localhost:6379/0'})
        backend.get.return_value='value'
        self.assertEqual(cache._call('get','key'),'value')
        cache._call('set','key','value',240)
        backend.set.assert_called_once_with('key','value',ex=240)

    def test_memcached_adapter(self):
        backend=MagicMock()
        backend.get.return_value=b'value'
        cache=Cache({'type':'memcached','host':'localhost','port':11211})
        with patch('pymemcache.client.base.Client',return_value=backend):
            self.assertEqual(cache._call('get','key'),'value')
            cache._call('set','key','value',240)
        backend.set.assert_called_once_with('key','value',expire=240,noreply=False)
        self.assertEqual(backend.close.call_count,2)
