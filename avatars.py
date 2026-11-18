"""Almacenamiento privado de fotos de perfil en Backblaze B2."""
import io
import secrets
import time
import warnings

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

StorageError = (BotoCoreError, ClientError)


def client(settings):
    return boto3.client('s3', endpoint_url=settings['endpoint'], region_name=settings['region'],
                        aws_access_key_id=settings['key_id'], aws_secret_access_key=settings['application_key'],
                        config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=15, retries={'max_attempts': 2}))


def process(stream):
    raw=stream.read(2*1024*1024+1)
    if len(raw)>2*1024*1024: raise ValueError('La foto debe pesar como máximo 2 MiB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ('JPEG','PNG') or image.width*image.height>20_000_000:
                    raise ValueError('Usa una imagen JPEG o PNG de hasta 20 megapíxeles')
                image.load()
                image=ImageOps.exif_transpose(image)
                image.thumbnail((512,512))
                image=image.convert('RGB')
                output=io.BytesIO()
                image.save(output,format='JPEG',quality=85)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('Imagen inválida o demasiado grande') from None


def cleanup(c, settings):
    if not all(settings.values()): return
    s3=client(settings)
    # shortcut: cleanup runs on later avatar changes, add a scheduled worker if timely deletion is required.
    for row in c.execute('SELECT key FROM avatar_cleanup WHERE created_at<?',(int(time.time())-3600,)).fetchall():
        key=row['key']
        try:
            paginator=s3.get_paginator('list_object_versions')
            for page in paginator.paginate(Bucket=settings['bucket'],Prefix=key):
                for item in page.get('Versions',[])+page.get('DeleteMarkers',[]):
                    if item['Key']==key:
                        s3.delete_object(Bucket=settings['bucket'],Key=key,VersionId=item['VersionId'])
            c.execute('DELETE FROM avatar_cleanup WHERE key=?',(key,))
            c.commit()
        except StorageError:
            c.rollback()


def save(c, user_id, session, image, settings):
    key='avatars/'+secrets.token_hex(32)+'.jpg'
    c.execute('INSERT INTO avatar_cleanup VALUES(?,?)',(key,int(time.time())))
    c.commit()
    client(settings).put_object(Bucket=settings['bucket'],Key=key,Body=image,ContentType='image/jpeg',CacheControl='private, no-store')
    c.execute('BEGIN IMMEDIATE')
    row=c.execute('''SELECT u.blocked,u.must_change_password,u.avatar_key FROM users u
                     JOIN sessions s ON s.user_id=u.id WHERE u.id=? AND s.token=? AND s.expires_at>?''',
                  (user_id,session,int(time.time()))).fetchone()
    if not row or row['blocked'] or row['must_change_password']:
        c.rollback()
        raise ValueError('La cuenta ya no tiene acceso')
    if row['avatar_key']:
        c.execute('INSERT OR IGNORE INTO avatar_cleanup VALUES(?,?)',(row['avatar_key'],int(time.time())))
    c.execute('UPDATE users SET avatar_key=? WHERE id=?',(key,user_id))
    c.execute('DELETE FROM avatar_cleanup WHERE key=?',(key,))
    c.commit()
    cleanup(c,settings)


def remove(c, user_id, settings):
    c.execute('BEGIN IMMEDIATE')
    row=c.execute('SELECT blocked,must_change_password,avatar_key FROM users WHERE id=?',(user_id,)).fetchone()
    if not row or row['blocked'] or row['must_change_password']:
        c.rollback()
        raise ValueError('La cuenta ya no tiene acceso')
    if row['avatar_key']:
        c.execute('INSERT OR IGNORE INTO avatar_cleanup VALUES(?,?)',(row['avatar_key'],int(time.time())))
        c.execute('UPDATE users SET avatar_key=NULL WHERE id=?',(user_id,))
    c.commit()
    cleanup(c,settings)


def signed_url(key, settings):
    return client(settings).generate_presigned_url('get_object',Params={'Bucket':settings['bucket'],'Key':key},ExpiresIn=3900)
