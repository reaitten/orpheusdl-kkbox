import json
import re
from time import time, sleep
from random import randrange
from Cryptodome.Cipher import ARC4
from Cryptodome.Hash import MD5
import requests
from tqdm import tqdm
from utils.utils import create_requests_session

class KkboxAPI:
    def __init__(
            self,
            exception,
            kc1_key,
            secret_key,
            kkid = None,
            http_proxy_url = None,
            https_proxy_url = None
        ):
        self.exception = exception

        key_pattern = re.compile("[0-9a-f]{32}")
        if not key_pattern.fullmatch(kc1_key):
            raise self.exception("kc1_key is invalid, change it in settings")
        if not key_pattern.fullmatch(secret_key):
            raise self.exception("secret_key is invalid, change it in settings")

        self.kc1_key = kc1_key.encode('ascii')
        self.secret_key = secret_key.encode('ascii')

        # self.s = create_requests_session()
        self.s = requests.Session()
        self.s.headers.update({
            'user-agent': 'okhttp/3.14.9'
        })
        self.s.proxies.update({
            "http": http_proxy_url,
            "https": https_proxy_url,
        })

        self.kkid = kkid or '%032X' % randrange(16**32)

        self.params = {
            'enc': 'u',
            'ver': '06120082',
            'os': 'android',
            'osver': '13',
            'lang': 'en',
            'ui_lang': 'en',
            'dist': '0021',
            'dist2': '0021',
            'resolution': '411x841',
            'of': 'j',
            'oenc': 'kc1',
        }

    def kc1_decrypt(self, data):
        cipher = ARC4.new(self.kc1_key)
        return cipher.decrypt(data).decode('utf-8')

    def api_call(self, host, path, params={}, payload=None):
        if host == 'ticket':
            payload = json.dumps(payload)

        timestamp = int(time())

        md5 = MD5.new()
        md5.update(self.params['ver'].encode('ascii'))
        md5.update(str(timestamp).encode('ascii'))
        md5.update(self.secret_key)

        params.update(self.params)
        params.update({'secret': md5.hexdigest()})
        params.update({'timestamp': timestamp})

        url = f'https://api-{host}.kkbox.com.tw/{path}'
        # print(f"{url=}, {params=}")
        if not payload:
            r = self.s.get(url, params=params)
        else:
            r = self.s.post(url, params=params, data=payload)

        resp = json.loads(self.kc1_decrypt(r.content)) if r.content else None
        return resp

    def login(self, email, password):
        md5 = MD5.new()
        md5.update(password.encode('utf-8'))
        pswd = md5.hexdigest()

        resp = self.api_call('login', 'login.php', payload={
            'uid': email,
            'passwd': pswd,
            'kkid': self.kkid,
            'registration_id': '',
        })

        if resp['status'] not in (2, 3):
            if resp['status'] == -1:
                raise self.exception('Email not found')
            elif resp['status'] == -2:
                raise self.exception('Incorrect password')
            elif resp['status'] == -4:
                raise self.exception('IP address is in unsupported region, use a VPN')
            elif resp['status'] == 1:
                raise self.exception('Account expired')
            raise self.exception(f'Login failed, status code {resp["status"]}')

        self.apply_session(resp)

    def renew_session(self):
        resp = self.api_call('login', 'check.php')
        if resp['status'] not in (2, 3):
            raise self.exception('Session renewal failed')
        self.apply_session(resp)

    def apply_session(self, resp):
        self.sid = resp['sid']
        self.params['sid'] = self.sid

        self.lic_content_key = resp['lic_content_key'].encode('ascii')

        self.available_qualities = ['128k', '192k', '320k']
        if resp['high_quality']:
            self.available_qualities.append('hifi')
            self.available_qualities.append('hires')

    def get_songs(self, ids):
        resp = self.api_call('ds', 'v2/song', payload={
            'ids': ','.join(ids),
            'fields': 'artist_role,song_idx,album_photo_info,song_is_explicit,song_more_url,album_more_url,artist_more_url,genre_name,is_lyrics,audio_quality'
        })
        if resp['status']['type'] != 'OK':
            raise self.exception('Track not found')
        return resp['data']['songs']

    def get_song_lyrics(self, id):
        return self.api_call('ds', f'v1/song/{id}/lyrics')

    def get_album(self, id):
        resp = self.api_call('ds', f'v1/album/{id}')
        if resp['status']['type'] != 'OK':
            raise self.exception('Album not found')
        return resp['data']

    def get_album_more(self, raw_id):
        return self.api_call('ds', 'album_more.php', params={
            'album': raw_id
        })

    def get_artist(self, id):
        resp = self.api_call('ds', f'v3/artist/{id}')
        if resp['status']['type'] != 'OK':
            raise self.exception('Artist not found')
        return resp['data']
    
    def get_artist_albums(self, raw_id, limit, offset):
        resp = self.api_call('ds', f'v2/artist/{raw_id}/album', params={
            'limit': limit,
            'offset': offset,
        })
        if resp['status']['type'] != 'OK':
            raise self.exception('Artist not found')
        return resp['data']['album']

    def get_playlists(self, ids):
        resp = self.api_call('ds', f'v1/playlists', params={
            'playlist_ids': ','.join(ids)
        })
        if resp['status']['type'] != 'OK':
            raise self.exception('Playlist not found')
        return resp['data']['playlists']

    def search(self, query, types, limit):
        return self.api_call('ds', 'search_music.php', params={
            'sf': ','.join(types),
            'limit': limit,
            'query': query,
            'search_ranking': 'sc-A',
        })

    def get_ticket(self, song_id, play_mode = None):
        resp = self.api_call('ticket', 'v1/ticket', payload={
            'sid': self.sid,
            'song_id': song_id,
            'ver': '06120082',
            'os': 'android',
            'osver': '13',
            'kkid': self.kkid,
            'dist': '0021',
            'dist2': '0021',
            'timestamp': int(time()),
            'play_mode': play_mode,
        })

        if resp['status'] != 1:
            if resp['status'] == -1:
                self.renew_session()
                return self.get_ticket(song_id, play_mode)
            elif resp['status'] == -4:
                self.auth_device()
                return self.get_ticket(song_id, play_mode)
            elif resp['status'] == 2:
                # tbh i'm not sure if this is some rate-limiting thing
                # or if it's a bug on their slow-as-hell servers
                sleep(0.5)
                return self.get_ticket(song_id, play_mode)
            raise self.exception("Couldn't get track URLs")

        return resp['uris']

    def auth_device(self):
        resp = self.api_call('ds', 'active_sid.php', payload={
            'ui_lang': 'en',
            'of': 'j',
            'os': 'android',
            'enc': 'u',
            'sid': self.sid,
            'ver': '06120082',
            'kkid': self.kkid,
            'lang': 'en',
            'oenc': 'kc1',
            'osver': '13',
        })
        if resp['status'] != 1:
            raise self.exception("Couldn't auth device")

    def kkdrm_dl(self, url, path):
        # skip first 1024 bytes of track file
        resp = self.s.get(url, stream=True, headers={'range': 'bytes=1024-'})
        resp.raise_for_status()

        size = int(resp.headers['content-length'])
        bar = tqdm(total=size, unit='B', unit_scale=True)

        # drop 512 bytes of keystream
        rc4 = ARC4.new(self.lic_content_key, drop=512)

        with open(path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=4096):
                f.write(rc4.decrypt(chunk))
                bar.update(len(chunk))

        bar.close()