"""Caché opcional de valores temporales, compartida entre workers."""
import json
import logging
import time

log=logging.getLogger(__name__)


class Cache:
    def __init__(self, settings):
        self.kind=settings['type']
        self.settings=settings
        if self.kind=='redis':
            import redis
            self.redis=redis.Redis.from_url(settings['url'],socket_connect_timeout=0.3,socket_timeout=0.3,decode_responses=True,retry_on_timeout=False)

    def _call(self, operation, key, value=None, ttl=None):
        if self.kind=='redis':
            if operation=='get': return self.redis.get(key)
            return self.redis.set(key,value,ex=ttl)
        if self.kind=='memcached':
            from pymemcache.client.base import Client
            client=Client((self.settings['host'],self.settings['port']),connect_timeout=0.3,timeout=0.3)
            try:
                if operation=='get':
                    result=client.get(key)
                    return result.decode('utf-8') if result is not None else None
                return client.set(key,value,expire=ttl,noreply=False)
            finally: client.close()

    def get_or_create(self, key, factory, ttl):
        if not self.kind: return factory()
        try:
            raw=self._call('get',key)
            if raw:
                entry=json.loads(raw)
                if isinstance(entry,list) and len(entry)==2 and isinstance(entry[0],(int,float)) and entry[0]>time.time() and isinstance(entry[1],str):
                    return entry[1]
        except Exception:
            log.warning('No se pudo leer la caché; se genera el valor directamente')
        expires=time.time()+ttl
        value=factory()
        try:
            self._call('set',key,json.dumps([expires,value]),ttl)
        except Exception:
            log.warning('No se pudo escribir en la caché')
        return value
