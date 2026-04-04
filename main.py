#!/usr/bin/env python3
from typing import TYPE_CHECKING, Iterable
from multiprocessing import Pool
from pathlib import Path
from urllib.parse import quote
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
import shutil

import warnings
with warnings.catch_warnings():  # hide macOS LibreSSL warning
    warnings.filterwarnings('ignore')
    from remotezip import RemoteZip  # pip install remotezip

if TYPE_CHECKING:
    from zipfile import ZipInfo


USE_ZIP_FILESIZE = False
re_info_plist = re.compile(r'Payload/([^/]+)/Info.plist')
# re_links = re.compile(r'''<a\s[^>]*href=["']([^>]+\.ipa)["'][^>]*>''')
re_archive_url = re.compile(
    r'https?://archive.org/(?:metadata|details|download)/([^/]+)(?:/.*)?')
CACHE_DIR = Path(__file__).parent / 'data'
CACHE_DIR.mkdir(exist_ok=True)

PNGDEFRY_BIN = Path(__file__).parent.parent / 'node-pngdefry' / 'lib' / 'pngdefry' / 'source' / 'pngdefry'

def fix_extracted_image(png_path: Path):
    """Automatically defry and convert PNG to JPG."""
    if not png_path.exists():
        return

    # 1. Check if it's CgBI (Apple crushed PNG)
    is_cgbi = False
    try:
        with open(png_path, 'rb') as f:
            header = f.read(32)
            if b'CgBI' in header:
                is_cgbi = True
    except Exception:
        pass

    # 2. Defry if needed
    if is_cgbi and PNGDEFRY_BIN.exists():
        temp_dir = png_path.parent / f'temp_defry_{png_path.stem}'
        temp_dir.mkdir(exist_ok=True)
        try:
            subprocess.run([str(PNGDEFRY_BIN), f'-o{temp_dir}', str(png_path)], 
                           capture_output=True, check=False)
            output_file = temp_dir / png_path.name
            if output_file.exists():
                output_file.replace(png_path)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    # 3. Convert to JPG (128px max)
    jpg_path = png_path.with_suffix('.jpg')
    try:
        subprocess.run(['magick', str(png_path), '-resize', '128x128', str(jpg_path)], 
                       check=True, capture_output=True)
        if jpg_path.exists():
            png_path.unlink() # Remove PNG after successful conversion
    except Exception as e:
        print(f"  [WARN] Failed to convert {png_path.name} to JPG: {e}")


def main():
    CacheDB().init()
    parser = ArgumentParser()
    cli = parser.add_subparsers(metavar='command', dest='cmd', required=True)

    cmd = cli.add_parser('add', help='Add urls to cache')
    cmd.add_argument('urls', metavar='URL', nargs='+',
                     help='Search URLs for .ipa links')

    cmd = cli.add_parser('update', help='Update all urls')
    cmd.add_argument('urls', metavar='URL', nargs='*', help='URLs or index')

    cmd = cli.add_parser('run', help='Download and process pending urls')
    cmd.add_argument('-force', '-f', action='store_true',
                     help='Reindex local data / populate DB.'
                     'Make sure to export fsize before!')
    cmd.add_argument('pk', metavar='PK', type=int,
                     nargs='*', help='Primary key')

    cmd = cli.add_parser('export', help='Export data')
    cmd.add_argument('export_type', choices=['json', 'fsize'],
                     help='Export to json or temporary-filesize file')

    cmd = cli.add_parser('err', help='Handle problematic entries')
    cmd.add_argument('err_type', choices=['reset'], help='Set done=0 to retry')

    cmd = cli.add_parser('get', help='Lookup value')
    cmd.add_argument('get_type', choices=['url', 'img', 'ipa'],
                     help='Get data field or download image.')
    cmd.add_argument('pk', metavar='PK', type=int,
                     nargs='+', help='Primary key')

    cmd = cli.add_parser('set', help='(Re)set value')
    cmd.add_argument('set_type', choices=['err'], help='Data field/column')
    cmd.add_argument('pk', metavar='PK', type=int,
                     nargs='+', help='Primary key')

    args = parser.parse_args()

    if args.cmd == 'add':
        for url in args.urls:
            addNewUrl(url)
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
                loadIpa(pk, url, overwrite=True)
        else:
            if args.force:
                print('Resetting done state ...')
                DB.setAllUndone(whereDone=1)
            processPending()

    elif args.cmd == 'err':
        if args.err_type == 'reset':
            print('Resetting error state ...')
            CacheDB().setAllUndone(whereDone=3)

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


###############################################
# Database
###############################################

class CacheDB:
    def __init__(self) -> None:
        self._db = sqlite3.connect(CACHE_DIR / 'ipa_cache.db')
        self._db.execute('pragma busy_timeout=5000')

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

                UNIQUE(base_url, path_name) ON CONFLICT ABORT,
                FOREIGN KEY (base_url) REFERENCES urls (pk) ON DELETE RESTRICT
            );
        ''')

    def __del__(self) -> None:
        self._db.close()

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
        return base + '/' + quote(path)

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
        self._db.executemany('''
        INSERT OR IGNORE INTO idx (base_url, path_name, fsize) VALUES (?,?,?);
        ''', ((baseUrlId, path, size) for path, size, _crc in entries))
        self._db.commit()
        return self._db.total_changes

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
                version, base_url, path_name, fsize / 1024
            FROM idx WHERE done=?
            ORDER BY tt COLLATE NOCASE, min_os, platform, version;''', [done])

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

    # Finalize / Postprocessing

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
        title = plist.get('CFBundleDisplayName') or plist.get('CFBundleName')
        v_short = str(plist.get('CFBundleShortVersionString', ''))
        v_long = str(plist.get('CFBundleVersion', ''))
        version = v_short or v_long
        if version != v_long and v_long:
            version += f' ({v_long})'
        # minOS = [int(x) for x in plist.get('MinimumOSVersion', '0').split('.')]
        raw = plist.get('MinimumOSVersion')
        
        # FIX crash: ensure raw is always string
        raw_str = "" if raw is None else str(raw)
        raw_str = raw_str.strip()

        # Handle empty / missing MinimumOSVersion (log once per UID)
        if raw_str == "":
            if not hasattr(self, "_warned_empty_min_os"):
                self._warned_empty_min_os = set()

            if uid not in self._warned_empty_min_os:
                print(f"[WARN] Empty MinimumOSVersion for uid={uid}")
                self._warned_empty_min_os.add(uid)

            minOS = [0]
        else:
            minOS = [int(x) for x in raw_str.split('.') if x.isdigit()]

        minOS += [0, 0, 0]  # ensures at least 3 components are given
        platforms = sum(1 << int(x) for x in plist.get('UIDeviceFamily', []))
        if not platforms and minOS[0] in [0, 1, 2, 3]:
            platforms = 1 << 1  # fallback to iPhone for old versions

        self._db.execute('''
            UPDATE idx SET
                done=1, min_os=?, platform=?, title=?, bundle_id=?, version=?
            WHERE pk=?;''', [
            (minOS[0] * 10000 + minOS[1] * 100 + minOS[2]) or None,
            platforms or None,
            title or None,
            bundleId or None,
            version or None,
            uid,
        ])
        self._db.commit()


###############################################
# [add] Process HTML link list
###############################################

def addNewUrl(url: str) -> None:
    archiveId = extractArchiveOrgId(url)
    if not archiveId:
        return
    baseUrlId = CacheDB().insertBaseUrl(urlForArchiveOrgId(archiveId))
    json_file = pathToListJson(baseUrlId)
    entries = downloadListArchiveOrg(archiveId, json_file)
    inserted = CacheDB().insertIpaUrls(baseUrlId, entries)
    print(f'new links added: {inserted} of {len(entries)}')


def extractArchiveOrgId(url: str) -> 'str|None':
    match = re_archive_url.match(url)
    if not match:
        print(f'[WARN] not an archive.org url. Ignoring "{url}"', file=stderr)
        return None
    return match.group(1)


def urlForArchiveOrgId(archiveId: str) -> str:
    return f'https://archive.org/download/{archiveId}'


def pathToListJson(baseUrlId: int, *, tmp: bool = False) -> Path:
    if tmp:
        return CACHE_DIR / 'url_cache' / f'tmp_{baseUrlId}.json.gz'
    return CACHE_DIR / 'url_cache' / f'{baseUrlId}.json.gz'


def downloadListArchiveOrg(
    archiveId: str, json_file: Path, *, force: bool = False
) -> 'list[tuple[str, int, str]]':
    ''' :returns: List of `(path_name, file_size, crc32)` '''
    # store json for later
    if force or not json_file.exists():
        json_file.parent.mkdir(exist_ok=True)
        print(f'load: {archiveId}')
        req = Request(f'https://archive.org/metadata/{archiveId}/files')
        req.add_header('Accept-Encoding', 'deflate, gzip')
        with urlopen(req) as page:
            with open(json_file, 'wb') as fp:
                while True:
                    block = page.read(8096)
                    if not block:
                        break
                    fp.write(block)
    # read saved json from disk
    with gzip.open(json_file, 'rb') as fp:
        data = json.load(fp)

    # 🔥 FIX: handle broken API response
    if 'result' not in data:
        print(f"[ERROR] archive.org response missing 'result' for {archiveId}")
        print(f"[DEBUG] Response keys: {list(data.keys())}")
        return []

    return [
        (x['name'], int(x.get('size', 0)), x.get('crc32'))
        for x in data['result']
        if x.get('source') == 'original' and x.get('name', '').endswith('.ipa')
    ]
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

    old_json_file = pathToListJson(baseUrlId)
    new_json_file = pathToListJson(baseUrlId, tmp=True)
    old_entries = set(downloadListArchiveOrg(archiveId, old_json_file))
    new_entries = set(downloadListArchiveOrg(archiveId, new_json_file))
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
    import os
    with Pool(processes=os.cpu_count() - 1) as pool:
        while True:
            DB = CacheDB()
            pending = DB.count(done=0)
            batch = DB.getPendingQueue(done=0, batchsize=300)
            del DB
            if not batch:
                print('Queue empty. done.')
                break

            batch = [(processed + i + 1, pending - i - 1, *x)
                     for i, x in enumerate(batch)]

            result = pool.starmap_async(procSinglePending, batch, chunksize=10).get()
            processed += len(result)
            DB = CacheDB()
            for uid, success in result:
                fsize = onceReadSizeFromFile(uid)
                if fsize:
                    DB.setFilesize(uid, fsize)
                if success:
                    print(f"[DEBUG] About to mark DONE: uid={uid}")
                    DB.setDone(uid)
                else:
                    DB.setError(uid, done=3)
            del DB
    DB = CacheDB()
    err_count = DB.count(done=3)
    if err_count > 0:
        print()
        print('URLs with Error:', err_count)
        for uid, base, path_name in DB.getPendingQueue(done=3, batchsize=10):
            print(f' - [{uid}] {base}/{quote(path_name)}')


def procSinglePending(
    processed: int, pending: int, uid: int, base_url: str, path_name
) -> 'tuple[int, bool]':
    url = base_url + '/' + quote(path_name)
    humanUrl = url.split('archive.org/download/')[-1]
    print(f'[{processed}|{pending} queued]: load[{uid}] {humanUrl}')
    try:
        return uid, loadIpa(uid, url)
    except Exception as e:
        print(f'ERROR: [{uid}] {e}', file=stderr)
    return uid, False


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
            overwrite: bool = False, image_only: bool = False) -> bool:
    basename = diskPath(uid, '')
    basename.parent.mkdir(exist_ok=True)
    img_path = basename.with_suffix('.png')
    plist_path = basename.with_suffix('.plist')
    if not overwrite and plist_path.exists():
        return True

    with RemoteZip(url) as zip:
        if USE_ZIP_FILESIZE:
            filesize = zip.fp.tell() if zip.fp else 0
            with open(basename.with_suffix('.size'), 'w') as fp:
                fp.write(str(filesize))

        app_name = None
        artwork = False
        zip_listing = zip.infolist()
        has_payload_folder = False

        for entry in zip_listing:
            fn = entry.filename.lstrip('/')
            has_payload_folder |= fn.startswith('Payload/')
            plist_match = re_info_plist.match(fn)
            if fn == 'iTunesArtwork':
                extractZipEntry(zip, entry, img_path)
                artwork = os.path.getsize(img_path) > 0
            elif plist_match:
                app_name = plist_match.group(1)
                if not image_only:
                    extractZipEntry(zip, entry, plist_path)

        if not has_payload_folder:
            print(f'ERROR: [{uid}] ipa has no "Payload/" root folder',
                  file=stderr)

        # if no iTunesArtwork found, load file referenced in plist
        if not artwork and app_name and plist_path.exists():
            with open(plist_path, 'rb') as fp:
                icon_names = iconNameFromPlist(plistlib.load(fp))
                icon = expandImageName(zip_listing, app_name, icon_names)
                if icon:
                    extractZipEntry(zip, icon, img_path)

        # Automatically defry and convert to JPG
        if img_path.exists():
            fix_extracted_image(img_path)

    return plist_path.exists()


def extractZipEntry(zip: 'RemoteZip', zipInfo: 'ZipInfo', dest_filename: Path):
    with zip.open(zipInfo) as src:
        with open(dest_filename, 'wb') as tgt:
            tgt.write(src.read())


###############################################
# Icon name extraction
###############################################
RESOLUTION_ORDER = ['3x', '2x', '180', '167', '152', '120']


def expandImageName(
    zip_listing: 'list[ZipInfo]', appName: str, iconList: 'list[str]'
) -> 'ZipInfo|None':
    for iconName in iconList + ['Icon', 'icon']:
        zipPath = f'Payload/{appName}/{iconName}'
        matchingNames = [x.filename.split('/', 2)[-1] for x in zip_listing
                         if x.filename.lstrip('/').startswith(zipPath)]
        if len(matchingNames) > 0:
            for bestName in sortedByResolution(matchingNames):
                bestPath = f'Payload/{appName}/{bestName}'
                for x in zip_listing:
                    if x.filename.lstrip('/') == bestPath and x.file_size > 0:
                        return x
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
                    return [icon] if icon else []
    return sortedByResolution(icons)


###############################################
# [json] Export to json
###############################################

def export_json():
    DB = CacheDB()
    url_map = DB.jsonUrlMap()
    maxUrlId = max(url_map.keys())
    # just a visual separator
    maxUrlId += 1
    url_map[maxUrlId] = '---'
    submap = {}
    total = DB.count(done=1)
    with open(CACHE_DIR / 'ipa.json', 'w') as fp:
        fp.write('[')
        for i, entry in enumerate(DB.enumJsonIpa(done=1)):
            if i % 113 == 0:
                print(f'\rprocessing [{i}/{total}]', end='')
            # if path_name is in a subdirectory, reindex URLs
            if '/' in entry[7]:
                baseurl = url_map[entry[6]]
                sub_dir, sub_file = entry[7].split('/', 1)
                newurl = baseurl + '/' + sub_dir
                subIdx = submap.get(newurl, None)
                if subIdx is None:
                    maxUrlId += 1
                    submap[newurl] = maxUrlId
                    subIdx = maxUrlId
                entry = list(entry)
                entry[6] = subIdx
                entry[7] = sub_file

            fp.write(json.dumps(entry, separators=(',', ':')) + ',\n')
        fp.seek(max(fp.tell(), 3) - 2)
        fp.write(']')
        print('\r', end='')
    print(f'write ipa.json: {total} entries')

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
