#!/usr/bin/env python3
from typing import TYPE_CHECKING, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen, urlretrieve
from argparse import ArgumentParser
from sys import stderr
import plistlib
import sqlite3
import json
import gzip
import os
import re
import subprocess
import tempfile
import hashlib
from PIL import Image, PngImagePlugin, ImageFile

KNOWN_BAD_ARTWORK_MD5 = {
    'bd660e3972f60fc1934c5faf4b61e6da',  # PNG signature 1 of generic dummy artwork
    '9c086c11db6681d4d7e417243f17bc61',  # JPEG signature 1 of generic dummy artwork
    '8efd998d4613d0b554d1b38d23618ba7',  # PNG signature 2 of generic dummy artwork (GamesRoom)
    '234357c49bc4fcfc01fb8ac96c4da2b8',  # JPEG signature 2 of generic dummy artwork (GamesRoom)
}

KNOWN_BAD_ARTWORK_DHASH = {
    'a5542a2b6a695300',  # Visual dHash fingerprint of generic dummy placeholder artwork
}

def calc_dhash(img_path: Path) -> str:
    """ Computes difference hash (dHash) for visual similarity comparison. """
    try:
        with Image.open(img_path) as img:
            resized = img.convert('L').resize((9, 8), Image.Resampling.LANCZOS)
            if hasattr(resized, 'get_flattened_data'):
                pixels = list(resized.get_flattened_data())
            else:
                pixels = list(resized.getdata())
            diff = [pixels[r*9 + c] > pixels[r*9 + c + 1] for r in range(8) for c in range(8)]
            dec = 0
            hex_str = []
            for i, v in enumerate(diff):
                if v: dec += 2**(i % 8)
                if (i % 8) == 7:
                    hex_str.append(hex(dec)[2:].zfill(2))
                    dec = 0
            return ''.join(hex_str)
    except Exception:
        return ''

def is_bad_artwork(jpg_path: Path) -> bool:
    """ Checks if image matches known dummy MD5s or visual dHash fingerprints. """
    if not jpg_path.exists():
        return False
    try:
        with open(jpg_path, 'rb') as f:
            if hashlib.md5(f.read()).hexdigest() in KNOWN_BAD_ARTWORK_MD5:
                return True
        
        # Perceptual Visual Check (dHash)
        dh = calc_dhash(jpg_path)
        if dh:
            for bad_dh in KNOWN_BAD_ARTWORK_DHASH:
                dist = bin(int(dh, 16) ^ int(bad_dh, 16)).count('1')
                if dist <= 4:
                    return True
    except Exception:
        pass
    return False

def clean_plist_str(val) -> str:
    if isinstance(val, (str, int, float)):
        return ' '.join(str(val).split())
    return ''


# Increase limit for large metadata chunks
PngImagePlugin.MAX_TEXT_CHUNK = 100 * 1024 * 1024 # 100MB
ImageFile.LOAD_TRUNCATED_IMAGES = True

import warnings
with warnings.catch_warnings():  # hide macOS LibreSSL warning
    warnings.filterwarnings('ignore')
    from remotezip import RemoteZip  # pip install remotezip

if TYPE_CHECKING:
    from zipfile import ZipInfo


import platform
USE_ZIP_FILESIZE = False
NESTED_SEP = '##'

# Detect OS and set pngdefry binary name
if platform.system() == 'Windows':
    PNGDEFRY_BIN = Path(__file__).parent / 'pngdefry' / 'pngdefry.exe'
else:
    PNGDEFRY_BIN = Path(__file__).parent / 'pngdefry' / 'pngdefry'

re_info_plist = re.compile(r'((?:Payload/)?([^/]+\.app))/Info.plist', re.IGNORECASE)
# re_links = re.compile(r'''<a\s[^>]*href=["']([^>]+\.ipa)["'][^>]*>''')
re_archive_url = re.compile(
    r'https?://archive.org/(?:metadata|details|download)/([^/]+)(?:/.*)?')
CACHE_DIR = Path(__file__).parent / 'data'
CACHE_DIR.mkdir(exist_ok=True)

# Load Special Exceptions (Mappings of Bundle ID -> image_pk)
EXCEPTIONS_FILE = CACHE_DIR / 'exceptions.json'
EXCEPTIONS = {}
if EXCEPTIONS_FILE.exists():
    with open(EXCEPTIONS_FILE, 'r') as f:
        EXCEPTIONS = json.load(f)

def main():
    CacheDB().init()
    parser = ArgumentParser()
    cli = parser.add_subparsers(metavar='command', dest='cmd', required=True)

    cmd = cli.add_parser('add', help='Add urls to cache')
    cmd.add_argument('urls', metavar='URL', nargs='+',
                     help='Search URLs for .ipa links. Use "continue" to resume interrupted progress.')

    cmd = cli.add_parser('update', help='Update all urls')
    cmd.add_argument('urls', metavar='URL', nargs='*', help='URLs or index')

    cmd = cli.add_parser('run', help='Download and process pending urls')
    cmd.add_argument('-force', '-f', action='store_true',
                     help='Reindex local data / populate DB.'
                     'Make sure to export fsize before!')
    cmd.add_argument('-retry', '-r', action='store_true',
                     help='Automatically retry entries that fail.')
    cmd.add_argument('pk', metavar='PK', type=int,
                     nargs='*', help='Primary key')

    cmd = cli.add_parser('export', help='Export data')
    cmd.add_argument('export_type', choices=['json', 'fsize'],
                     help='Export to json or temporary-filesize file')

    cmd = cli.add_parser('err', help='Handle problematic entries')
    cmd.add_argument('err_type', choices=['reset', 'fix', 'clear'], 
                     help='reset: Set all done=3 to 0. fix: Reset and retry until no progress is made. clear: DELETE all entries with done=3 or done=4 from database.')

    cmd = cli.add_parser('get', help='Lookup value')
    cmd.add_argument('get_type', choices=['url', 'img', 'ipa'],
                     help='Get data field or download image.')
    cmd.add_argument('pk', metavar='PK', type=int,
                     nargs='+', help='Primary key')

    cmd = cli.add_parser('set', help='(Re)set value')
    cmd.add_argument('set_type', choices=['err'], help='Data field/column')
    cmd.add_argument('pk', metavar='PK', type=int,
                     nargs='+', help='Primary key')

    cli.add_parser('fix-imgs', help='Check and fix missing images')
    
    cmd = cli.add_parser('clear-queue', help='DELETE pending entries from the database')
    cmd.add_argument('queue_type', choices=['run', 'add'], metavar='type', 
                     help='run: Processing queue (done=0), add: Scraping queue')

    cmd = cli.add_parser('special', help='Consolidate all images for a bundle ID to the first one')
    cmd.add_argument('bundle_id', help='The Bundle ID to consolidate')

    args = parser.parse_args()

    if args.cmd == 'add':
        if args.urls == ['continue']:
            queue = CacheDB().getScrapeQueue()
            if not queue:
                print('Nothing to resume.')
            else:
                print(f'Resuming {len(queue)} collections...')
                for url in queue:
                    addNewUrl(url, resume=True)
        else:
            # Add all URLs to queue first so they can be resumed if interrupted
            db = CacheDB()
            for url in args.urls:
                db.addToScrapeQueue(url)
            
            for url in args.urls:
                addNewUrl(url, resume=False)
        print('done.')

    elif args.cmd == 'update':
        queue = args.urls or CacheDB().getUpdateUrlIds(sinceNow='-7 days')
        if queue:
            for i, url in enumerate(queue):
                updateUrl(url, i + 1, len(queue))
            print('done.')
        else:
            print('Nothing to do.')

    elif args.cmd == 'run':
        DB = CacheDB()
        if args.pk:
            for pk in args.pk:
                url = DB.getUrl(pk)
                print(pk, ': process', url)
                success, _img_pk = loadIpa(pk, url, overwrite=True)
                if success:
                    DB.setDone(pk)
                else:
                    DB.setError(pk, done=3)
        else:
            if args.force:
                print('Resetting done state ...')
                DB.setAllUndone(whereDone=1)
            while True:
                old_done_count = DB.count(done=1)
                processPending()
                new_done_count = DB.count(done=1)

                if args.retry and new_done_count > old_done_count:
                    err_count = DB.count(done=3)
                    if err_count > 0:
                        print(f'\nFixed {new_done_count - old_done_count} entries. {err_count} errors remain. Retrying...')
                        DB.setAllUndone(whereDone=3)
                        continue
                break
        
        # After run, always check for missing images
        fix_missing_images(DB)

    elif args.cmd == 'err':
        DB = CacheDB()
        if args.err_type == 'reset':
            print('Resetting error state ...')
            DB.setAllUndone(whereDone=3)
        elif args.err_type == 'clear':
            count = DB.deleteAllErrors()
            print(f'Successfully deleted {count} error entries from the database.')
        elif args.err_type == 'fix':
            while True:
                err_count = DB.count(done=3)
                if err_count == 0:
                    print('No errors to fix.')
                    break
                
                print(f'Resetting {err_count} errors and retrying...')
                DB.setAllUndone(whereDone=3)
                
                old_done_count = DB.count(done=1)
                processPending()
                new_done_count = DB.count(done=1)
                
                if new_done_count <= old_done_count:
                    print(f'No more progress. {DB.count(done=3)} errors remain.')
                    break
                print(f'Fixed {new_done_count - old_done_count} entries. Retrying remaining errors...')

    elif args.cmd == 'export':
        if args.export_type == 'json':
            export_json()
        elif args.export_type == 'fsize':
            export_filesize()

    elif args.cmd == 'get':
        DB = CacheDB()
        if args.get_type == 'url':
            for pk in args.pk:
                print(pk, ':', DB.getUrl(pk))
        elif args.get_type == 'img':
            for pk in args.pk:
                url = DB.getUrl(pk)
                print(pk, ': load image', url)
                loadIpa(pk, url, overwrite=True, image_only=True)
        elif args.get_type == 'ipa':
            dir = Path('ipa_download')
            dir.mkdir(exist_ok=True)
            for pk in args.pk:
                url = DB.getUrl(pk)
                print(pk, ': load ipa', url)
                urlretrieve(url, dir / f'{pk}.ipa', printProgress)
                print(end='\r')

    elif args.cmd == 'set':
        DB = CacheDB()
        if args.set_type == 'err':
            for pk in args.pk:
                print(pk, ': set done=4')
                DB.setPermanentError(pk)

    elif args.cmd == 'fix-imgs':
        fix_missing_images(CacheDB())

    elif args.cmd == 'clear-queue':
        count = CacheDB().clearQueue(type=args.queue_type)
        print(f'Successfully cleared {count} {args.queue_type} entries from the database.')

    elif args.cmd == 'special':
        DB = CacheDB()
        bid = args.bundle_id
        print(f"Consolidating images for: {bid}")
        
        # Find the first entry that has an image
        res = DB._db.execute("""
            SELECT image_pk FROM idx 
            WHERE bundle_id=? AND done=1 AND image_pk IS NOT NULL 
            ORDER BY pk ASC LIMIT 1
        """, [bid]).fetchone()
        
        if not res:
            print(f"Error: No processed entries (done=1) found for {bid} with an image.")
            return
            
        master_pk = res[0]
        if not diskPath(master_pk, '.jpg').exists():
            print(f"Error: Master image {master_pk}.jpg not found on disk.")
            return
            
        print(f"Master Image PK identified: {master_pk}")
        
        # Update JSON
        EXCEPTIONS[bid] = master_pk
        with open(EXCEPTIONS_FILE, 'w') as f:
            json.dump(EXCEPTIONS, f, indent=4)
        print(f"Updated exceptions.json")
        
        # Update Database
        cur = DB._db.execute("UPDATE idx SET image_pk=? WHERE bundle_id=?", [master_pk, bid])
        count = cur.rowcount
        print(f"Updated {count} entries in database.")
        DB._db.commit()
        
        # Cleanup orphaned files
        cur = DB._db.execute("SELECT pk FROM idx WHERE bundle_id=?", [bid])
        pks = [row[0] for row in cur.fetchall()]
        deleted_count = 0
        for pk in pks:
            if pk == master_pk: continue
            for ext in ['.jpg', '.png']:
                p = diskPath(pk, ext)
                if p.exists():
                    p.unlink()
                    deleted_count += 1
        print(f"Deleted {deleted_count} orphaned icon files.")
        print("done.")


def fix_missing_images(DB: 'CacheDB'):
    missing = []
    print("Checking for missing images...")
    # Only iterate over the unique master images (~58k)
    # We skip entries where done=4 because those are known to be broken/unfixable
    entries = list(DB.getUniqueImagePks())
    total = len(entries)
    
    # Group by shard for faster filesystem checking
    shards = {}
    for pk, img_pk in entries:
        s = img_pk // 1000
        if s not in shards:
            shards[s] = []
        shards[s].append(img_pk)
        
    checked = 0
    for s, pks in sorted(shards.items()):
        shard_dir = CACHE_DIR / str(s)
        if not shard_dir.exists():
            missing.extend(pks)
        else:
            # Batch list the directory for performance
            existing = {f.name for f in shard_dir.iterdir() if f.suffix == '.jpg'}
            for img_pk in pks:
                jpg_file = shard_dir / f"{img_pk}.jpg"
                if f"{img_pk}.jpg" not in existing:
                    missing.append(img_pk)
                elif jpg_file.exists() and is_bad_artwork(jpg_file):
                    print(f"\n[BAD ARTWORK] [{img_pk}] Found generic bad artwork (MD5/dHash). Re-extracting icon...")
                    try: jpg_file.unlink()
                    except Exception: pass
                    missing.append(img_pk)
        checked += len(pks)
        if checked % 100 == 0 or checked == total:
            print(f"\rChecked {checked}/{total} unique images...", end="")

    print(f"\rChecked {total}/{total} unique images. Done.")
    
    if not missing:
        print("No missing images found.")
    else:
        print(f"Found {len(missing)} missing unique images. Fixing...")
        for pk in missing:
            url = DB.getUrl(pk)
            print(f"[{pk}] Fix unique image: {url}")
            
            # Get current state to handle retry logic
            res = DB._db.execute("SELECT done FROM idx WHERE pk=?", [pk]).fetchone()
            state = res[0] if res else 1
            
            success, img_pk = loadIpa(pk, url, overwrite=True, image_only=True)
            
            if success and img_pk != pk:
                print(f"  [FIX] [{pk}] deduplicated to {img_pk}. Updating database...")
                DB._db.execute("UPDATE idx SET image_pk=? WHERE image_pk=?", [img_pk, pk])
                DB._db.commit()
            
            # Re-check disk path using the potentially updated img_pk
            if not diskPath(img_pk, ".jpg").exists():
                if state == 1:
                    print(f"  [WARN] [{pk}] Still no image. Setting to retry state (done=2).")
                    DB._db.execute("UPDATE idx SET done=2 WHERE image_pk=?", [img_pk])
                    DB._db.commit()
                else:
                    print(f"  [ERROR] [{pk}] Still no image after retry. Marking as permanent error.")
                    # Mark ALL entries sharing this image as permanent error
                    uids = DB._db.execute("SELECT pk FROM idx WHERE image_pk=?", [img_pk]).fetchall()
                    for (uid,) in uids:
                        DB.setPermanentError(uid)
    print("done.")


###############################################
# Database
###############################################

###############################################
# Database
###############################################

# --- PRE-COMPILED REGEX FOR INSTANT SPEED ---
RE_HASH = re.compile(r'-[0-9a-f]{32}')
RE_BRACKETS = re.compile(r'[\(\[].*?[\)\]]')
RE_VERSION = re.compile(r'[-.]v?\d+(\.\d+)*')
RE_CAMEL = re.compile(r'([a-z])([A-Z])')
RE_WORDS = re.compile(r'[a-z0-9]{2,}')
RE_BID = re.compile(r'([a-z]{2,}\.[a-z0-9]{2,}\.[a-z0-9\.]+)')

def get_clean_words(text):
    """Optimized word extractor"""
    text = RE_CAMEL.sub(r'\1 \2', str(text))
    return RE_WORDS.findall(text.lower())

def is_hint_match(word, target):
    """Optimized fuzzy matcher"""
    if not word or not target: return False
    it = iter(target.lower())
    return all(c in it for c in word.lower())

def prettify_title(title: str, bundle_id: str, path_name: str) -> str:
    """Ultra-fast local title polisher"""
    if not title or title.lower() in ["cfbundledisplayname", "cfbundlename", "templete", "null", "unknown"]:
        source = path_name.split('##')[-1].split('/')[-1].replace('.ipa', '')
        if len(source) < 3 and bundle_id:
            source = bundle_id.split('.')[-1]
    elif title.lower().startswith(('com.', 'net.', 'org.')):
        source = title.split('.')[-1]
    else:
        source = title

    # Clean noise using pre-compiled regex
    source = RE_HASH.sub('', source)
    source = RE_BRACKETS.sub('', source)
    source = RE_VERSION.sub('', source)
    
    pretty = source.replace('.', ' ').replace('-', ' ').replace('_', ' ')
    pretty = ' '.join(pretty.split()).title()
    
    if len(pretty) < 2 and bundle_id:
        pretty = str(bundle_id).split('.')[-1].title()
    return pretty

class CacheDB:
    def __init__(self) -> None:
        self._db = sqlite3.connect(CACHE_DIR / 'ipa_cache.db', timeout=60.0)
        self._db.execute('PRAGMA journal_mode=WAL;')
        self._db.execute('PRAGMA busy_timeout=60000;')

    def init(self):
        self._db.execute('''
            CREATE TABLE IF NOT EXISTS urls(
                pk INTEGER PRIMARY KEY,
                url TEXT NOT NULL UNIQUE,
                date INTEGER DEFAULT (strftime('%s','now'))
            );
        ''')
        self._db.execute('''
            CREATE TABLE IF NOT EXISTS idx(
                pk INTEGER PRIMARY KEY,
                base_url INTEGER NOT NULL,
                path_name TEXT NOT NULL,
                done INTEGER DEFAULT 0,
                fsize INTEGER DEFAULT 0,

                min_os INTEGER DEFAULT NULL,
                platform INTEGER DEFAULT NULL,
                title TEXT DEFAULT NULL,
                bundle_id TEXT DEFAULT NULL,
                version TEXT DEFAULT NULL,
                image_pk INTEGER DEFAULT NULL,

                UNIQUE(base_url, path_name) ON CONFLICT ABORT,
                FOREIGN KEY (base_url) REFERENCES urls (pk) ON DELETE RESTRICT
            );
        ''')
        self._db.execute('''
            CREATE TABLE IF NOT EXISTS scrape_queue(
                url TEXT PRIMARY KEY
            );
        ''')
        self._db.execute('''
            CREATE TABLE IF NOT EXISTS scanned_archives(
                base_url_id INTEGER,
                archive_name TEXT,
                size INTEGER,
                crc TEXT,
                PRIMARY KEY(base_url_id, archive_name),
                FOREIGN KEY (base_url_id) REFERENCES urls (pk) ON DELETE CASCADE
            );
        ''')

    def __del__(self) -> None:
        self._db.close()

    def addToScrapeQueue(self, url: str):
        self._db.execute('INSERT OR IGNORE INTO scrape_queue (url) VALUES (?);', [url])
        self._db.commit()

    def removeFromScrapeQueue(self, url: str):
        self._db.execute('DELETE FROM scrape_queue WHERE url=?;', [url])
        self._db.commit()

    def getScrapeQueue(self) -> 'list[str]':
        x = self._db.execute('SELECT url FROM scrape_queue;')
        return [row[0] for row in x.fetchall()]

    def isArchiveScanned(self, baseUrlId: int, name: str, size: int, crc: str) -> bool:
        x = self._db.execute('''SELECT 1 FROM scanned_archives 
            WHERE base_url_id=? AND archive_name=? AND size=? AND crc=?;''', 
            [baseUrlId, name, size, crc])
        return x.fetchone() is not None

    def markArchiveScanned(self, baseUrlId: int, name: str, size: int, crc: str):
        self._db.execute('''INSERT OR REPLACE INTO scanned_archives 
            (base_url_id, archive_name, size, crc) VALUES (?,?,?,?);''',
            [baseUrlId, name, size, crc])
        self._db.commit()

    def getNestedIpasFromIdx(self, baseUrlId: int, archiveName: str) -> 'list[tuple[str, int, str]]':
        prefix = archiveName + NESTED_SEP
        x = self._db.execute('''SELECT path_name, fsize FROM idx 
            WHERE base_url=? AND path_name LIKE ?;''', [baseUrlId, prefix + '%'])
        return [(row[0], row[1], None) for row in x.fetchall()]

    def clearScannedArchives(self, baseUrlId: int = None):
        if baseUrlId:
            self._db.execute('DELETE FROM scanned_archives WHERE base_url_id=?;', [baseUrlId])
        else:
            self._db.execute('DELETE FROM scanned_archives;')
        self._db.commit()

    # Get URL

    def getIdForBaseUrl(self, url: str) -> 'int|None':
        x = self._db.execute('SELECT pk FROM urls WHERE url=?', [url])
        row = x.fetchone()
        return row[0] if row else None

    def getBaseUrlForId(self, uid: int) -> 'str|None':
        x = self._db.execute('SELECT url FROM urls WHERE pk=?', [uid])
        row = x.fetchone()
        return row[0] if row else None

    def getId(self, baseUrlId: int, pathName: str) -> 'int|None':
        x = self._db.execute('''SELECT pk FROM idx
            WHERE base_url=? AND path_name=?;''', [baseUrlId, pathName])
        row = x.fetchone()
        return row[0] if row else None
    def getUrl(self, uid: int) -> str:
        x = self._db.execute('''SELECT url, path_name FROM idx
            INNER JOIN urls ON urls.pk=base_url WHERE idx.pk=?;''', [uid])
        base, path = x.fetchone()
        # Convert the internal ## separator to a slash for the final URL
        path = path.replace(NESTED_SEP, '/')
        return base + '/' + quote(path)

    def hasImage(self, bundle_id: str, version: str) -> 'int|None':
        # --- SPECIAL SITUATION: Configuration Exceptions ---
        if bundle_id in EXCEPTIONS:
            target_pk = EXCEPTIONS[bundle_id]
            if diskPath(target_pk, '.jpg').exists():
                return target_pk

        if not bundle_id or not version:
            return None
        res = self._db.execute('''
            SELECT image_pk FROM idx 
            WHERE bundle_id=? AND version=? AND image_pk IS NOT NULL 
            LIMIT 1''', [bundle_id, version]).fetchone()
        if res:
            pk = res[0]
            jpg_file = diskPath(pk, '.jpg')
            if jpg_file.exists():
                if is_bad_artwork(jpg_file):
                    try: jpg_file.unlink()
                    except Exception: pass
                    return None
                return pk
        return None

    # Insert URL

    def insertBaseUrl(self, base: str) -> int:
        try:
            x = self._db.execute('INSERT INTO urls (url) VALUES (?);', [base])
            self._db.commit()
            return x.lastrowid  # type: ignore
        except sqlite3.IntegrityError:
            x = self._db.execute('SELECT pk FROM urls WHERE url = ?;', [base])
            return x.fetchone()[0]

    def insertIpaUrls(
        self, baseUrlId: int, entries: 'Iterable[tuple[str, int, str]]'
    ) -> int:
        ''' :entries: must be iterable of `(path_name, filesize, crc32)` '''
        before = self._db.total_changes
        self._db.executemany('''
        INSERT OR IGNORE INTO idx (base_url, path_name, fsize) VALUES (?,?,?);
        ''', ((baseUrlId, path, size) for path, size, _crc in entries))
        self._db.commit()
        return self._db.total_changes - before

    # Update URL

    def getUpdateUrlIds(self, *, sinceNow: str) -> 'list[int]':
        x = self._db.execute('''SELECT pk FROM urls
            WHERE date IS NULL OR date < strftime('%s','now', ?)
        ''', [sinceNow])
        return [row[0] for row in x.fetchall()]

    def markBaseUrlUpdated(self, uid: int) -> None:
        self._db.execute('''
            UPDATE urls SET date=strftime('%s','now') WHERE pk=?''', [uid])
        self._db.commit()

    def updateIpaUrl(self, baseUrlId: int, entry: 'tuple[str, int, str]') \
            -> 'int|None':
        ''' :entry: must be `(path_name, filesize, crc32)` '''
        uid = self.getId(baseUrlId, entry[0])
        if uid:
            self._db.execute('UPDATE idx SET done=0, fsize=? WHERE pk=?;',
                             [entry[1], uid])
            self._db.commit()
            return uid
        if self.insertIpaUrls(baseUrlId, [entry]) > 0:
            x = self._db.execute('SELECT MAX(pk) FROM idx;')
            return x.fetchone()[0]
        return None

    # Export JSON

    def jsonUrlMap(self) -> 'dict[int, str]':
        x = self._db.execute('SELECT pk, url FROM urls')
        rv = {}
        for pk, url in x:
            rv[pk] = url
        return rv

    def enumJsonIpa(self, *, done: int) -> Iterable[tuple]:
        yield from self._db.execute('''
            SELECT pk, platform, IFNULL(min_os, 0),
                TRIM(IFNULL(title,
                    REPLACE(path_name,RTRIM(path_name,REPLACE(path_name,'/','')),'')
                )) as tt, IFNULL(bundle_id, ""),
                version, base_url, path_name, fsize / 1024,
                image_pk
            FROM idx WHERE done=?
            ORDER BY tt COLLATE NOCASE, bundle_id, min_os, version;''', [done])

    def getUniqueImagePks(self) -> Iterable[tuple[int, int]]:
        ''' Returns (pk, image_pk) for each unique image_pk, excluding known errors (done=4) '''
        yield from self._db.execute('''
            SELECT MIN(pk), image_pk 
            FROM idx 
            WHERE done IN (1, 2) AND image_pk IS NOT NULL 
            GROUP BY image_pk
        ''')

    # Filesize

    def enumFilesize(self) -> Iterable[tuple]:
        yield from self._db.execute('SELECT pk, fsize FROM idx WHERE fsize>0;')

    def setFilesize(self, uid: int, size: int) -> None:
        if size > 0:
            self._db.execute('UPDATE idx SET fsize=? WHERE pk=?;', [size, uid])
            self._db.commit()

    # Process Pending

    def count(self, *, done: int) -> int:
        x = self._db.execute('SELECT COUNT() FROM idx WHERE done=?;', [done])
        return x.fetchone()[0]

    def getPendingQueue(self, *, done: int, batchsize: int) \
            -> 'list[tuple[int, str, str]]':
        # url || "/" || REPLACE(REPLACE(path_name, '#', '%23'), '?', '%3F')
        x = self._db.execute('''SELECT idx.pk, url, path_name
            FROM idx INNER JOIN urls ON urls.pk=base_url
            WHERE done=? LIMIT ?;''', [done, batchsize])
        return x.fetchall()

    def setAllUndone(self, *, whereDone: int) -> None:
        self._db.execute('UPDATE idx SET done=0 WHERE done=?;', [whereDone])
        self._db.commit()

    def deleteAllErrors(self) -> int:
        '''
        DELETE all entries with done=3 or done=4 from database.
        Will also delete all plist and image files for these entries.
        '''
        # First find IDs to delete
        x = self._db.execute('SELECT pk FROM idx WHERE done IN (3, 4);')
        ids = [row[0] for row in x.fetchall()]
        
        # Delete files
        for uid in ids:
            for ext in ['.plist', '.png', '.jpg']:
                fname = diskPath(uid, ext)
                if fname.exists():
                    os.remove(fname)
        
        # DELETE from DB
        x = self._db.execute('DELETE FROM idx WHERE done IN (3, 4);')
        self._db.commit()
        return x.rowcount

    def clearQueue(self, type: str = 'run') -> int:
        ''' 
        DELETE entries from the database.
        run: pending entries (done=0) from idx table.
        add: scraping queue and scanned archives cache.
        '''
        if type == 'run':
            x = self._db.execute('DELETE FROM idx WHERE done=0;')
            self._db.commit()
            return x.rowcount
        elif type == 'add':
            x1 = self._db.execute('DELETE FROM scrape_queue;')
            x2 = self._db.execute('DELETE FROM scanned_archives;')
            self._db.commit()
            return x1.rowcount
        return 0

    def setError(self, uid: int, *, done: int) -> None:
        self._db.execute('UPDATE idx SET done=? WHERE pk=?;', [done, uid])
        self._db.commit()

    def setPermanentError(self, uid: int) -> None:
        '''
        Set done=4 and all file related columns to NULL.
        Will also delete all plist, and image files for {uid} in CACHE_DIR
        '''
        self._db.execute('''
            UPDATE idx SET done=4, min_os=NULL, platform=NULL, title=NULL,
            bundle_id=NULL, version=NULL WHERE pk=?;''', [uid])
        self._db.commit()
        for ext in ['.plist', '.png', '.jpg']:
            fname = diskPath(uid, ext)
            if fname.exists():
                os.remove(fname)

    def setDone(self, uid: int) -> None:
        plist_path = diskPath(uid, '.plist')
        if not plist_path.exists():
            return
        with open(plist_path, 'rb') as fp:
            try:
                plist = plistlib.load(fp)
            except Exception as e:
                print(f'ERROR: [{uid}] PLIST: {e}', file=stderr)
                self.setError(uid, done=3)
                return

        bundleId = plist.get('CFBundleIdentifier')
        if not isinstance(bundleId, str):
            bundleId = clean_plist_str(bundleId)
        title = plist.get('CFBundleDisplayName') or plist.get('CFBundleName')
        if not isinstance(title, str):
            title = clean_plist_str(title)
        v_short = clean_plist_str(plist.get('CFBundleShortVersionString'))
        v_long = clean_plist_str(plist.get('CFBundleVersion'))
        version = v_short or v_long
        if version != v_long and v_long:
            version += f' ({v_long})'
        # minOS = [int(x) for x in plist.get('MinimumOSVersion', '0').split('.')]
        raw = plist.get('MinimumOSVersion')
        if raw is not None:
            raw = str(raw)

        # Handle empty / missing MinimumOSVersion (log once per UID)
        if not raw or raw.strip() == "":
            if not hasattr(self, "_warned_empty_min_os"):
                self._warned_empty_min_os = set()

            if uid not in self._warned_empty_min_os:
                print(f"[WARN] Empty MinimumOSVersion for uid={uid}")
                self._warned_empty_min_os.add(uid)

            minOS = [0]
        else:
            minOS = [int(x) for x in raw.split('.') if x.isdigit()]

        minOS += [0, 0, 0]  # ensures at least 3 components are given
        platforms = sum(1 << int(x) for x in plist.get('UIDeviceFamily', []))
        if not platforms and minOS[0] in [0, 1, 2, 3]:
            platforms = 1 << 1  # fallback to iPhone for old versions

        # --- ARCHITECTURAL GUARD: UNIVERSAL SENTINEL RULE ---
        res = self._db.execute('SELECT path_name FROM idx WHERE pk=?', [uid]).fetchone()
        path_name = res[0] if res else ""
        
        if path_name:
            fn_words = get_clean_words(path_name.split('##')[-1])
            bid_full = str(bundleId).lower()
            tl_full = str(title).lower()
            
            has_hint = False
            for w in fn_words:
                if is_hint_match(w, bid_full) or is_hint_match(w, tl_full):
                    has_hint = True
                    break
            
            # BLOCK & AUTO-FIX
            if not has_hint:
                # 1. Purge corrupted files
                for ext in ['.plist', '.png', '.jpg']:
                    p = diskPath(uid, ext)
                    if p.exists(): p.unlink()
                
                # 2. Attempt AUTHENTIC Inference from filename
                fn = path_name.split('##')[-1].replace('.ipa', '')
                bid_pattern = RE_BID.search(fn.lower())
                
                if bid_pattern:
                    bundleId = bid_pattern.group(1)
                    title = bundleId.split('.')[-1].replace('-', ' ').replace('_', ' ').title()
                else:
                    noise = {'old', 'ios', 'ipa', 'v1', 'v2', 'v3'}
                    parts = [w for w in re.split(r'[\.\-_\s\(\)\[\]/]', fn) if w and w.lower() not in noise]
                    title = (parts[0] if parts else fn).title()
                    bundleId = f"com.archive.{title.lower()}"

        # --- SMART TITLE CLEANING ---
        title = prettify_title(title, bundleId, path_name)

        # --- IMAGE DEDUPLICATION & EXCEPTIONS ---
        image_pk = uid
        # SPECIAL SITUATION: Configuration Exceptions
        if bundleId in EXCEPTIONS:
            image_pk = EXCEPTIONS[bundleId]
            # Delete local icon if it exists to save space
            for ext in ['.jpg', '.png']:
                p = diskPath(uid, ext)
                if p.exists(): p.unlink()
        elif (title and ("Major Magnet" in str(title) or "Major Planet" in str(title))):
            # Fallback for Major Magnet name-based matching if BID is unknown
            image_pk = 71210
            for ext in ['.jpg', '.png']:
                p = diskPath(uid, ext)
                if p.exists(): p.unlink()
        elif bundleId and version:
            res = self._db.execute('''
                SELECT image_pk FROM idx 
                WHERE bundle_id=? AND version=? AND image_pk IS NOT NULL 
                LIMIT 1''', [bundleId, version]).fetchone()
            if res:
                potential_img_pk = res[0]
                if diskPath(potential_img_pk, '.jpg').exists():
                    image_pk = potential_img_pk
                    # If we found a duplicate, we can delete our own image if it exists
                    for ext in ['.jpg', '.png']:
                        p = diskPath(uid, ext)
                        if p.exists(): p.unlink()

        self._db.execute('''
            UPDATE idx SET
                done=1, min_os=?, platform=?, title=?, bundle_id=?, version=?, image_pk=?
            WHERE pk=?;''', [
            (minOS[0] * 10000 + minOS[1] * 100 + minOS[2]) or None,
            platforms or None,
            title or None,
            bundleId or None,
            version or None,
            image_pk,
            uid,
        ])
        self._db.commit()


###############################################
# [add] Process HTML link list
###############################################

def addNewUrl(url: str, resume: bool = False) -> None:
    DB = CacheDB()
    archiveId = extractArchiveOrgId(url)
    if not archiveId:
        return
    
    # Pre-calculate base URL ID
    baseUrl = urlForArchiveOrgId(archiveId)
    baseUrlId = DB.insertBaseUrl(baseUrl)

    if not resume:
        # If explicitly adding a new URL, clear previous scan cache for this URL
        # as requested "process a new URL from the beginning"
        print(f'Starting fresh scan for: {url}')
        DB.clearScannedArchives(baseUrlId)
    
    # Add to resume queue
    DB.addToScrapeQueue(url)

    # Track initial count to report progress correctly
    initial_count = DB._db.execute("SELECT COUNT(*) FROM idx WHERE base_url=?", [baseUrlId]).fetchone()[0]

    json_file = pathToListJson(archiveId)
    entries = downloadListArchiveOrg(baseUrlId, archiveId, json_file, force=not resume, resume=resume)
    if entries is None:
        print(f'[ERROR] Could not fetch metadata for {archiveId}. Aborting.')
        return
    
    # Final insert for any entries not already handled by downloadListArchiveOrg
    DB.insertIpaUrls(baseUrlId, entries)
    
    # Calculate how many were actually added
    final_count = DB._db.execute("SELECT COUNT(*) FROM idx WHERE base_url=?", [baseUrlId]).fetchone()[0]
    inserted = final_count - initial_count
    
    # If successful, remove from queue
    DB.removeFromScrapeQueue(url)
    print(f'new links added: {inserted} of {len(entries)}')


def extractArchiveOrgId(url: str) -> 'str|None':
    match = re_archive_url.match(url)
    if not match:
        print(f'[WARN] not an archive.org url. Ignoring "{url}"', file=stderr)
        return None
    return match.group(1)


def urlForArchiveOrgId(archiveId: str) -> str:
    return f'https://archive.org/download/{archiveId}'


def pathToListJson(archiveId: str, *, tmp: bool = False) -> Path:
    if tmp:
        return CACHE_DIR / 'url_cache' / f'tmp_{archiveId}.json.gz'
    return CACHE_DIR / 'url_cache' / f'{archiveId}.json.gz'


def getNestedIpas(url: str, zipPath: str) -> 'list[tuple[str, int, str]]':
    ''' 
    Peeks into a zip file on Archive.org and returns a list of .ipa files found inside.
    Path format: "Archive.zip##Internal/Path/App.ipa"
    '''
    print(f'  peeking into zip: {zipPath}')
    try:
        with RemoteZip(url) as rz:
            return [(f'{zipPath}{NESTED_SEP}{info.filename}', info.file_size, None)
                    for info in rz.infolist()
                    if info.filename.lower().endswith('.ipa') and info.file_size > 0]
    except Exception as e:
        print(f'  [WARN] could not peek into zip {zipPath}: {e}', file=stderr)
    return []


def getNestedIpasViaViewArchive(url: str, archivePath: str) -> 'list[tuple[str, int, str]]':
    ''' 
    Peeks into a non-zip archive (RAR, 7z, tar) on Archive.org using its view_archive.php bridge.
    This avoids downloading the whole archive just to list its contents.
    '''
    print(f'  peeking into archive (via bridge): {archivePath}')
    try:
        # Construct the bridge URL
        bridge_url = url
        if not bridge_url.endswith('/'):
            bridge_url += '/'
        
        req = Request(bridge_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req) as res:
            html = res.read().decode('utf-8', errors='ignore')
        
        # Regex to find files inside the table
        pattern = r'<tr><td><a [^>]*href="[^"]*">([^<]+)</a><td><td>[^<]*<td [^>]*size">(\d+)</tr>'
        matches = re.findall(pattern, html)
        
        return [(f'{archivePath}{NESTED_SEP}{name}', int(size), None)
                for name, size in matches
                if name.lower().endswith('.ipa') and int(size) > 0]
    except Exception as e:
        print(f'  [WARN] could not peek into archive {archivePath}: {e}', file=stderr)
    return []


def downloadListArchiveOrg(
    baseUrlId: int, archiveId: str, json_file: Path, *, force: bool = False, resume: bool = False
) -> 'list[tuple[str, int, str]]|None':
    ''' :returns: List of `(path_name, file_size, crc32)` or None on failure '''
    # store json for later
    if force or not json_file.exists():
        json_file.parent.mkdir(exist_ok=True)
        print(f'load: {archiveId}')
        req = Request(f'https://archive.org/metadata/{archiveId}/files')
        req.add_header('Accept-Encoding', 'deflate, gzip')
        
        import time
        for attempt in range(3):
            try:
                with urlopen(req) as page:
                    with open(json_file, 'wb') as fp:
                        while True:
                            block = page.read(8096)
                            if not block:
                                break
                            fp.write(block)
                break # Success
            except Exception as e:
                if attempt == 2:
                    print(f'[ERROR] Failed to fetch metadata for {archiveId} after 3 attempts: {e}', file=stderr)
                    return None
                print(f'[WARN] Attempt {attempt+1} failed for {archiveId}: {e}. Retrying...', file=stderr)
                time.sleep(1)

    # read saved json from disk
    try:
        with gzip.open(json_file, 'rb') as fp:
            data = json.load(fp)
    except (EOFError, OSError, json.JSONDecodeError) as e:
        print(f'[WARN] Cache file corrupted for {archiveId} ({e}). Re-downloading...', file=stderr)
        if json_file.exists():
            json_file.unlink()
        return downloadListArchiveOrg(baseUrlId, archiveId, json_file, force=True, resume=resume)
    # process and add to DB
    if 'result' not in data:
        if 'error' in data:
            print(f'[ERROR] Archive.org: {data["error"]}', file=stderr)
        return []

    baseUrl = urlForArchiveOrgId(archiveId)
    rv = []
    DB = CacheDB()
    for x in data['result']:
        if x.get('source') != 'original':
            continue
        name = x['name']
        size = int(x.get('size', 0))
        crc = x.get('crc32')

        name_lower = name.lower()
        if name_lower.endswith('.ipa'):
            rv.append((name, size, crc))
        elif name_lower.endswith('.zip'):
            if resume and DB.isArchiveScanned(baseUrlId, name, size, crc):
                # Skip re-peeking, fetch from idx
                cached = DB.getNestedIpasFromIdx(baseUrlId, name)
                if cached:
                    print(f'  skipping already scanned zip: {name}')
                    rv.extend(cached)
                    continue

            url = f'{baseUrl}/{quote(name)}'
            nested_ipas = getNestedIpas(url, name)
            # Efficiently insert as we go to avoid data loss on crash
            DB.insertIpaUrls(baseUrlId, nested_ipas)
            DB.markArchiveScanned(baseUrlId, name, size, crc)
            rv.extend(nested_ipas)
        elif name_lower.endswith(('.rar', '.7z', '.tar', '.tar.gz', '.tgz')) and not name_lower.endswith('_archive.torrent'):
            if resume and DB.isArchiveScanned(baseUrlId, name, size, crc):
                # Skip re-peeking, fetch from idx
                cached = DB.getNestedIpasFromIdx(baseUrlId, name)
                if cached:
                    print(f'  skipping already scanned archive: {name}')
                    rv.extend(cached)
                    continue

            url = f'{baseUrl}/{quote(name)}'
            nested_ipas = getNestedIpasViaViewArchive(url, name)
            # Efficiently insert as we go
            DB.insertIpaUrls(baseUrlId, nested_ipas)
            DB.markArchiveScanned(baseUrlId, name, size, crc)
            rv.extend(nested_ipas)
    return rv


###############################################
# [update] Re-index existing URL caches
###############################################

def updateUrl(url_or_uid: 'str|int', proc_i: int, proc_total: int):
    baseUrlId, url = _lookupBaseUrl(url_or_uid)
    if not baseUrlId or not url:
        print(f'[ERROR] Ignoring "{url_or_uid}". Not found in DB', file=stderr)
        return

    archiveId = extractArchiveOrgId(url) or ''  # guaranteed to return str
    print(f'Updating [{proc_i}/{proc_total}] {archiveId}')

    old_json_file = pathToListJson(archiveId)
    new_json_file = pathToListJson(archiveId, tmp=True)
    old_entries_raw = downloadListArchiveOrg(baseUrlId, archiveId, old_json_file, resume=True)
    new_entries_raw = downloadListArchiveOrg(baseUrlId, archiveId, new_json_file, resume=True)
    
    if old_entries_raw is None or new_entries_raw is None:
        print(f'  [SKIP] Could not fetch metadata for {archiveId}. Skipping update.')
        DB.markBaseUrlUpdated(baseUrlId)
        return

    old_entries = set(old_entries_raw)
    new_entries = set(new_entries_raw)
    old_diff = old_entries - new_entries
    new_diff = new_entries - old_entries

    DB = CacheDB()
    if old_diff or new_diff:
        c_del = 0
        c_new = 0
        for old_entry in old_diff:  # no need to sort
            uid = DB.getId(baseUrlId, old_entry[0])
            if uid:
                print(f'  rm: [{uid}] {old_entry}')
                DB.setPermanentError(uid)
                c_del += 1
            else:
                print(f'  [ERROR] could not find old entry {old_entry[0]}',
                      file=stderr)
        for new_entry in sorted(new_diff):
            uid = DB.updateIpaUrl(baseUrlId, new_entry)
            if uid:
                print(f'  add: [{uid}] {new_entry}')
                c_new += 1
            else:
                print(f'  [ERROR] updating {new_entry[0]}', file=stderr)
        print(f'  updated -{c_del}/+{c_new} entries.')
        os.rename(new_json_file, old_json_file)
    else:
        print('  no changes.')

    DB.markBaseUrlUpdated(baseUrlId)
    if new_json_file.exists():
        os.remove(new_json_file)


def _lookupBaseUrl(url_or_index: 'str|int') -> 'tuple[int|None, str|None]':
    if isinstance(url_or_index, str):
        if url_or_index.isnumeric():
            url_or_index = int(url_or_index)
    if isinstance(url_or_index, int):
        baseUrlId = url_or_index
        url = CacheDB().getBaseUrlForId(baseUrlId)
    else:
        archiveId = extractArchiveOrgId(url_or_index)
        if not archiveId:
            return None, None
        url = urlForArchiveOrgId(archiveId)
        baseUrlId = CacheDB().getIdForBaseUrl(url)
    return baseUrlId, url


###############################################
# [run] Process pending urls from DB
###############################################

def processPending():
    processed = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        while True:
            DB = CacheDB()
            pending = DB.count(done=0)
            batch = DB.getPendingQueue(done=0, batchsize=100)
            del DB
            if not batch:
                print('Queue empty. done.')
                break

            batch = [(processed + i + 1, pending - i - 1, *x)
                     for i, x in enumerate(batch)]

            for uid, res in executor.map(_procSinglePendingWrapper, batch):
                processed += 1
                success, _img_pk = res
                DB = CacheDB()
                fsize = onceReadSizeFromFile(uid)
                if fsize:
                    DB.setFilesize(uid, fsize)
                if success:
                    DB.setDone(uid)
                    print(f'  [DONE] [{uid}]')
                else:
                    DB.setError(uid, done=3)
                    print(f'  [FAILED] [{uid}]')
                del DB
    DB = CacheDB()
    err_count = DB.count(done=3)
    if err_count > 0:
        print()
        print('URLs with Error:', err_count)
        for uid, base, path_name in DB.getPendingQueue(done=3, batchsize=10):
            print(f' - [{uid}] {base}/{quote(path_name)}')


def _procSinglePendingWrapper(args):
    return procSinglePending(*args)


def procSinglePending(
    processed: int, pending: int, uid: int, base_url: str, path_name
) -> 'tuple[int, tuple[bool, int]]':
    # ... (code truncated)
    full_path = path_name
    display_path = path_name.replace(NESTED_SEP, ' -> ')
    print(f'[{processed}|{pending} queued]: load[{uid}] {display_path}')
    
    DB = CacheDB()
    url = DB.getUrl(uid)
    del DB
    
    try:
        return uid, loadIpa(uid, url)
    except Exception as e:
        print(f'ERROR: [{uid}] {e}', file=stderr)
    return uid, (False, uid)


def onceReadSizeFromFile(uid: int) -> 'int|None':
    size_path = diskPath(uid, '.size')
    if size_path.exists():
        with open(size_path, 'r') as fp:
            size = int(fp.read())
        os.remove(size_path)
        return size
    return None


###############################################
# Process IPA zip
###############################################

def loadIpa(uid: int, url: str, *,
            overwrite: bool = True, image_only: bool = False) -> 'tuple[bool, int]':
    basename = diskPath(uid, '')
    basename.parent.mkdir(exist_ok=True, mode=0o755)
    img_path = basename.with_suffix('.png')
    plist_path = basename.with_suffix('.plist')

    # --- LAW OF FRESH EXTRACTION ---
    # Always delete stale metadata to prevent "Sticky Corruption"
    if not image_only:
        for ext in ['.plist', '.png', '.jpg']:
            p = basename.with_suffix(ext)
            if p.exists(): p.unlink()

    # Support both old format (##) and new format (nested slash)
    inner_path = None
    
    # Handle the ## separator (possibly quoted as %23%23)
    for sep in [NESTED_SEP, quote(NESTED_SEP)]:
        if sep in url:
            base_url, inner_path = url.split(sep, 1)
            url = base_url
            inner_path = unquote(inner_path)
            break

    # Check for implicit nested path (archive.rar/file.ipa) if ## wasn't found
    if not inner_path:
        for ext in ['.zip/', '.rar/', '.7z/', '.tar/', '.tar.gz/', '.tgz/']:
            if ext in url.lower():
                # Split at the end of the extension
                idx = url.lower().find(ext) + len(ext) - 1
                base_url = url[:idx]
                inner_path = unquote(url[idx+1:])
                url = base_url
                break

    # Handle non-ZIP nested archives (RAR, 7z, etc.)
    # RemoteZip does not work on these via the Archive.org bridge.
    if inner_path and not url.lower().endswith('.zip'):
        direct_inner_url = f"{url}/{quote(inner_path)}"
        try:
            with tempfile.NamedTemporaryFile(suffix='.ipa') as tmp:
                print(f"  downloading inner ipa from bridge: {inner_path}")
                req = Request(direct_inner_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urlopen(req) as response:
                    data = response.read(1024)
                    if data.startswith(b'<!DOCTYPE html>') or data.startswith(b'<html>'):
                        print(f"ERROR: [{uid}] bridge returned HTML instead of file", file=stderr)
                        return False, uid
                    
                    with open(tmp.name, 'wb') as f:
                        f.write(data)
                        while True:
                            chunk = response.read(1024*1024)
                            if not chunk: break
                            f.write(chunk)
                
                import zipfile
                try:
                    with zipfile.ZipFile(tmp.name) as zip:
                        return _processIpaZip(uid, zip, basename, img_path, plist_path, image_only)
                except zipfile.BadZipFile:
                    print(f"ERROR: [{uid}] downloaded file is not a valid zip", file=stderr)
                    return False, uid
        except Exception as e:
            print(f"ERROR: [{uid}] could not download/process inner ipa: {e}", file=stderr)
            return False, uid

    # Handle ZIP archives (RemoteZip is fast here)
    try:
        with RemoteZip(url) as outer_zip:
            if inner_path:
                # Open nested IPA from outer ZIP and save to temp file to ensure seekability
                import zipfile
                with tempfile.NamedTemporaryFile(suffix='.ipa') as tmp:
                    print(f"  extracting nested ipa to temp: {inner_path}")
                    with outer_zip.open(inner_path) as src:
                        while True:
                            buf = src.read(1024*1024)
                            if not buf: break
                            tmp.write(buf)
                    tmp.flush()
                    with zipfile.ZipFile(tmp.name) as zip:
                        return _processIpaZip(uid, zip, basename, img_path, plist_path, image_only)
            else:
                # Regular direct IPA
                if USE_ZIP_FILESIZE:
                    filesize = outer_zip.fp.tell() if outer_zip.fp else 0
                    with open(basename.with_suffix('.size'), 'w') as fp:
                        fp.write(str(filesize))
                return _processIpaZip(uid, outer_zip, basename, img_path, plist_path, image_only)
    except Exception as e:
        if '404' in str(e):
            print(f"ERROR: [{uid}] File not found (404): {url}", file=stderr)
        elif "File is not a zip file" in str(e):
            print(f"  [ERROR] [{uid}] BadZipFile: {url} is not a valid zip.", file=stderr)
        else:
            print(f"ERROR: [{uid}] connection failed: {e}", file=stderr)
    return False, uid


def _processIpaZip(uid: int, zip, basename, img_path, plist_path, image_only) -> 'tuple[bool, int]':
    app_prefix = None
    artwork = False
    used_image_pk = uid
    zip_listing = zip.infolist()
    
    # First pass: find Info.plist AND check for duplicates
    for entry in zip_listing:
        fn = entry.filename.lstrip('/')

        if not app_prefix:
            plist_match = re_info_plist.match(fn)
            if plist_match:
                app_prefix = plist_match.group(1)
                extractZipEntry(zip, entry, plist_path)
                
                # Deduplication check: if we already have this app's icon, don't download it again
                if plist_path.exists():
                    try:
                        with open(plist_path, 'rb') as fp:
                            plist = plistlib.load(fp)
                            bid = plist.get('CFBundleIdentifier')
                            if not isinstance(bid, str):
                                bid = clean_plist_str(bid)
                            v_short = clean_plist_str(plist.get('CFBundleShortVersionString'))
                            v_long = clean_plist_str(plist.get('CFBundleVersion'))
                            ver = v_short or v_long
                            if ver != v_long and v_long:
                                ver += f' ({v_long})'
                            
                            db = CacheDB()
                            existing_img_pk = db.hasImage(bid, ver)
                            if existing_img_pk:
                                print(f'  reusing image from {existing_img_pk}')
                                artwork = True # Skip further icon extraction
                                used_image_pk = existing_img_pk
                                if image_only: return True, used_image_pk # Optimization: if only image requested, we are done
                            del db
                    except:
                        pass
                
                if image_only and not artwork:
                    pass # Continue to find icon
                elif image_only and artwork:
                    return True, used_image_pk

    # Second pass: load icon files referenced in Info.plist (highest authenticity app icons)
    if not artwork and app_prefix and plist_path.exists():
        with open(plist_path, 'rb') as fp:
            try:
                plist = plistlib.load(fp)
                icon_names = iconNameFromPlist(plist)
                # Try all candidates until one works
                for icon_name in icon_names + ['Icon', 'icon']:
                    icon = expandImageName(zip_listing, app_prefix, [icon_name])
                    if icon:
                        extractZipEntry(zip, icon, img_path)
                        if os.path.exists(img_path) and os.path.getsize(img_path) > 8:
                            if processImage(img_path):
                                artwork = True
                                break
                            else:
                                if img_path.exists(): img_path.unlink() # Cleanup
            except Exception as e:
                print(f'ERROR: [{uid}] failed to parse plist or find icon: {e}', file=stderr)

    # Third pass: extract iTunesArtwork inside app_prefix if no plist icon was found
    if not artwork and app_prefix:
        app_prefix_lower = app_prefix.lower().rstrip('/') + '/'
        for entry in zip_listing:
            fn = entry.filename.lstrip('/')
            fn_lower = fn.lower()
            # Require iTunesArtwork to be inside the app bundle directory to avoid root-level zip leakage
            if fn_lower.startswith(app_prefix_lower) and fn_lower.endswith('itunesartwork') and entry.file_size > 0:
                extractZipEntry(zip, entry, img_path)
                if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                    if processImage(img_path):
                        # Verify the processed image is not a known generic bad artwork (MD5 or dHash)
                        jpg_candidate = img_path.with_suffix('.jpg')
                        if jpg_candidate.exists() and is_bad_artwork(jpg_candidate):
                            print(f'  [WARN] [{uid}] Rejected generic bad iTunesArtwork (MD5/dHash), removing...')
                            try: jpg_candidate.unlink()
                            except Exception: pass
                            artwork = False
                            continue
                        artwork = True
                        break
                    else:
                        if img_path.exists(): img_path.unlink() # Cleanup
    
    # If artwork was found via iTunesArtwork in first pass, process it
    if artwork and not os.path.exists(basename.with_suffix('.jpg')) and os.path.exists(img_path):
        processImage(img_path)

    return plist_path.exists(), used_image_pk


def extractZipEntry(zip: 'RemoteZip', zipInfo: 'ZipInfo', dest_filename: Path):
    import time
    for attempt in range(3):
        try:
            with zip.open(zipInfo) as src:
                data = src.read()
                if data and (data.startswith(b'<!DOCTYPE html>') or data.startswith(b'<html>')):
                    print(f'  [WARN] detected HTML 404 instead of data for {zipInfo.filename}', file=stderr)
                    return
                
                if data and data.startswith(b'\x00' * 32):
                    print(f'  [WARN] detected zero-filled data for {zipInfo.filename}', file=stderr)
                    return

                if data and data.startswith(b'PK\x03\x04'):
                    print(f'  [WARN] detected ZIP header instead of data for {zipInfo.filename}', file=stderr)
                    return

                if data:
                    with open(dest_filename, 'wb') as tgt:
                        tgt.write(data)
                    return
        except Exception as e:
            print(f'  [WARN] attempt {attempt+1} failed to extract {zipInfo.filename}: {e}', file=stderr)
            if '500' in str(e) or '503' in str(e):
                time.sleep(1)
                continue
            break

def processImage(png_path: Path) -> bool:
    if not png_path.exists() or png_path.stat().st_size < 8:
        if png_path.exists():
            try: png_path.unlink()
            except Exception: pass
        return False
    
    success = False
    try:
        # Check if zero-filled
        with open(png_path, 'rb') as f:
            header = f.read(32)
            if header.startswith(b'\x00' * 8):
                return False

        # Fix CGBI if present
        if b'CgBI' in header:
            try:
                # -s for silent, -free to create [name]-free.png
                subprocess.run([str(PNGDEFRY_BIN), '-s', '-free', str(png_path)], 
                               check=True, capture_output=True)
                fixed_path = png_path.with_name(png_path.stem + "-free.png")
                if fixed_path.exists():
                    fixed_path.replace(png_path)
            except Exception as e:
                print(f"  [WARN] pngdefry failed: {e}", file=stderr)
                
        # Convert to JPG and Optimize
        jpg_path = png_path.with_suffix('.jpg')
        with Image.open(png_path) as img:
            # Convert to RGB
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Downscale if larger than 128x128
            MAX_SIZE = (128, 128)
            if img.width > MAX_SIZE[0] or img.height > MAX_SIZE[1]:
                img.thumbnail(MAX_SIZE, Image.Resampling.LANCZOS)
            
            # Save optimized JPEG
            img.save(jpg_path, 'JPEG', quality=80, optimize=True)
            os.chmod(jpg_path, 0o644)
            success = True
    except Exception as e:
        print(f"  [WARN] PIL conversion/optimization failed for {png_path}: {e}", file=stderr)
        success = False
    finally:
        # Guarantee PNG is ALWAYS auto-deleted
        if png_path.exists():
            try:
                png_path.unlink()
            except Exception:
                pass
    return success



###############################################
# Icon name extraction
###############################################
RESOLUTION_ORDER = ['3x', '2x', '180', '167', '152', '120']


def expandImageName(
    zip_listing: 'list[ZipInfo]', app_prefix: str, iconList: 'list[str]'
) -> 'ZipInfo|None':
    prefix = app_prefix.lower()
    if not prefix.endswith('/'):
        prefix += '/'
    
    # Normalize icon names
    search_names = []
    for name in iconList + ['Icon', 'icon']:
        if not name: continue
        search_names.append(name.lower())
        if not name.lower().endswith('.png'):
            search_names.append(name.lower() + '.png')

    # 1. Try case-insensitive exact matches
    for x in zip_listing:
        if x.file_size == 0 or x.filename.endswith('/'):
            continue
        fn = x.filename.lstrip('/')
        if fn.lower().startswith(prefix):
            rel_fn = fn[len(prefix):].lower()
            if rel_fn in search_names:
                return x

    # 2. Try matching by resolution (if multiple files match the base name)
    for name in search_names:
        zipPath = f'{prefix}{name}'.lower()
        matching = [x for x in zip_listing 
                    if x.file_size > 0 and not x.filename.endswith('/') and x.filename.lstrip('/').lower().startswith(zipPath)]
        if matching:
            return sorted(matching, key=lambda x: resolutionIndex(x.filename))[0]

    # 3. Fallback: Any PNG in the app folder that looks like an icon
    fallback_icons = []
    for x in zip_listing:
        fn = x.filename.lstrip('/')
        if fn.lower().startswith(prefix) and fn.lower().endswith('.png'):
            rel_fn = fn[len(prefix):].lower()
            # If it contains 'icon', or starts with 'icon', or is just a small PNG
            if 'icon' in rel_fn or 'πéó' in rel_fn or x.file_size < 500000:
                fallback_icons.append(x)
    
    if fallback_icons:
        # Prefer files with "icon" in them, otherwise sort by size (heuristic)
        fallback_icons.sort(key=lambda x: ('icon' in x.filename.lower() or 'πéó' in x.filename.lower(), x.file_size), reverse=True)
        return fallback_icons[0]

    return None


def unpackNameListFromPlistDict(bundleDict: 'dict|None') -> 'list[str]|None':
    if not bundleDict or not isinstance(bundleDict, dict):
        return None
    primaryDict = bundleDict.get('CFBundlePrimaryIcon', {})
    if not isinstance(primaryDict, dict):
        return None
    icons = primaryDict.get('CFBundleIconFiles')
    if not icons:
        singular = primaryDict.get('CFBundleIconName')
        if singular:
            return [singular]
    return icons


def resolutionIndex(icon_name: str):
    penalty = 0
    if 'small' in icon_name.lower() or icon_name.lower().startswith('default'):
        penalty = 10
    for i, match in enumerate(RESOLUTION_ORDER):
        if match in icon_name:
            return i + penalty
    return 50 + penalty


def sortedByResolution(icons: 'list[str]') -> 'list[str]':
    if not isinstance(icons, list):
        if isinstance(icons, str):
            icons = [icons]
        else:
            return []
            
    # Filter to only strings
    icons = [str(x) for x in icons if x]
    
    icons.sort(key=resolutionIndex)
    return icons


def iconNameFromPlist(plist: dict) -> 'list[str]':
    # Check for CFBundleIcons (since 5.0)
    icons = unpackNameListFromPlistDict(plist.get('CFBundleIcons'))
    if not icons:
        icons = unpackNameListFromPlistDict(plist.get('CFBundleIcons~ipad'))
        if not icons:
            # Check for CFBundleIconFiles (since 3.2)
            icons = plist.get('CFBundleIconFiles')
            if not icons:
                # key found on iTunesU app
                icons = plist.get('Icon files')
                if not icons:
                    # Check for CFBundleIconFile (legacy, before 3.2)
                    icon = plist.get('CFBundleIconFile')  # may be None
                    return [str(icon)] if icon else []
    
    # Ensure icons is a list before sorting
    if isinstance(icons, str):
        icons = [icons]
    elif not isinstance(icons, list):
        return []

    return sortedByResolution(icons)


###############################################
# [json] Export to json
###############################################

def parse_version(v: str) -> list:
    ''' Returns a list of integers for version comparison. '''
    if not v:
        return []
    # Handle version strings like "1.2.3 (456)" or "1.2.3-beta"
    v = str(v).split(' ')[0].split('-')[0]
    return [int(x) for x in re.findall(r'\d+', v)]


def normalize_title(t: str) -> str:
    ''' Returns a normalized title for better grouping. '''
    if not t:
        return ''
    # Remove all non-alphanumeric characters and lowercase
    return re.sub(r'[^a-z0-9]', '', t.lower())


def export_json():
    DB = CacheDB()
    url_map = DB.jsonUrlMap()
    maxUrlId = max(url_map.keys())
    # just a visual separator
    maxUrlId += 1
    url_map[maxUrlId] = '---'
    submap = {}
    total = DB.count(done=1)
    
    entries = []
    print(f'Collecting {total} entries...')
    for i, entry in enumerate(DB.enumJsonIpa(done=1)):
        if i % 1000 == 0:
            print(f'\rcollected [{i}/{total}]', end='')
        
        # Normalize path: replace ## with / for the JSON export
        entry = list(entry)
        path_name = entry[7].replace(NESTED_SEP, '/')
        entry[7] = path_name

        # if path_name is in a subdirectory, reindex URLs
        if '/' in entry[7]:
            baseurl = url_map[entry[6]]
            sub_dir, sub_file = entry[7].rsplit('/', 1)
            newurl = baseurl + '/' + sub_dir
            subIdx = submap.get(newurl, None)
            if subIdx is None:
                maxUrlId += 1
                submap[newurl] = maxUrlId
                subIdx = maxUrlId
            entry[6] = subIdx
            entry[7] = sub_file
        
        entries.append(entry)
    print(f'\rcollected [{total}/{total}] done.')

    # Custom sort: Normalized Title -> Bundle ID -> Version -> Platform
    print('Sorting entries...')
    entries.sort(key=lambda x: (
        normalize_title(x[3] or ''),
        x[4] or '',
        parse_version(x[5]),
        x[1] or 0
    ))

    print(f'Writing {CACHE_DIR / "ipa.json"}...')
    with open(CACHE_DIR / 'ipa.json', 'w') as fp:
        fp.write('[')
        for i, entry in enumerate(entries):
            fp.write(json.dumps(entry, separators=(',', ':')))
            if i < len(entries) - 1:
                fp.write(',\n')
        fp.write(']')
    print(f'write ipa.json: {len(entries)} entries')

    for newurl, newidx in submap.items():
        url_map[newidx] = newurl
    with open(CACHE_DIR / 'urls.json', 'w') as fp:
        fp.write(json.dumps(url_map, separators=(',\n', ':'), sort_keys=True))
    print(f'write urls.json: {len(url_map)} entries')


def export_filesize():
    ignored = 0
    written = 0
    for i, (uid, fsize) in enumerate(CacheDB().enumFilesize()):
        size_path = diskPath(uid, '.size')
        if not size_path.exists():
            with open(size_path, 'w') as fp:
                fp.write(str(fsize))
            written += 1
        else:
            ignored += 1
        if i % 113 == 0:
            print(f'\r{written} files written. {ignored} ignored', end='')
    print(f'\r{written} files written. {ignored} ignored. done.')


###############################################
# Helper
###############################################

def diskPath(uid: int, ext: str) -> Path:
    return CACHE_DIR / str(uid // 1000) / f'{uid}{ext}'


def printProgress(blocknum, bs, size):
    if size == 0:
        return
    percent = (blocknum * bs) / size
    done = "#" * int(40 * percent)
    print(f'\r[{done:<40}] {percent:.1%}', end='')

# def b64e(text: str) -> str:
#     return b64encode(text.encode('utf8')).decode('ascii')


if __name__ == '__main__':
    main()
