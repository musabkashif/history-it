"""Live integrations. Source reader and register writer are kept separate."""
from __future__ import annotations
from typing import Any
import io, threading, time

IMAGE_MIME = {'image/jpeg','image/png','image/tiff','image/webp'}

def _status(exc):
    resp = getattr(exc, 'resp', None)
    status = getattr(resp, 'status', None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None

def _retryable(exc):
    status = _status(exc)
    if status == 429 or (status is not None and status >= 500):
        return True
    if status is not None:
        return False
    text = f'{type(exc).__name__} {exc}'
    markers = ('ConnectionResetError', 'ConnectionAbortedError', 'TimeoutError', 'ConnectionError', 'RemoteDisconnected', 'ServerNotFoundError', 'SSLError', 'timed out', 'WinError 10054', 'WinError 10053')
    return any(marker in text for marker in markers)

def _execute(lock, build, sleep=time.sleep):
    delay = 5
    for attempt in range(5):
        try:
            with lock:
                return build().execute()
        except Exception as exc:
            if not _retryable(exc) or attempt == 4:
                raise
            sleep(delay)
            delay = min(delay * 2, 60)

def _google_api(name, version, creds, timeout):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))
    return build(name, version, http=http, cache_discovery=False)

class Drive:
    def __init__(self, credentials=None):
        from google.auth import default
        creds = credentials or default(scopes=['https://www.googleapis.com/auth/drive.readonly'])[0]
        self.api = _google_api('drive', 'v3', creds, 300)
        self._lock = threading.RLock()

    def _execute(self, request):
        return _execute(self._lock, request)

    def _list_children(self, folder_id: str):
        token = None
        children = []
        while True:
            response = self._execute(lambda folder_id=folder_id, token=token: self.api.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields='nextPageToken,files(id,name,mimeType)',
                pageSize=1000,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ))
            children.extend(response.get('files', []))
            token = response.get('nextPageToken')
            if not token:
                break
        return children

    def enumerate(self, root: str):
        """Enumerate authorized images. Parent roots discover only immediate F* children."""
        from app.models import Source
        from app.box_folders import (
            detect_ambiguous_f_folders,
            is_f_folder_name,
            natural_f_key,
            select_authorized_sources,
        )
        root_name = self._execute(lambda: self.api.files().get(fileId=root, fields='name', supportsAllDrives=True))['name']
        try:
            top = self._list_children(root)
        except Exception as exc:
            raise ValueError(f'Authorized folder {root_name} ({root}) is inaccessible: {exc}') from exc
        f_children = [
            child for child in top
            if child.get('mimeType') == 'application/vnd.google-apps.folder' and is_f_folder_name(child.get('name'))
        ]
        if f_children:
            conflicts = detect_ambiguous_f_folders([child.get('name') for child in f_children])
            if conflicts:
                raise ValueError('Ambiguous F-folder identities: ' + '; '.join(conflicts))
            out = []
            for child in sorted(f_children, key=lambda item: natural_f_key(item.get('name'))):
                folder_id = child['id']
                folder_name = child['name']
                path = f'{root_name}/{folder_name}'
                try:
                    entries = self._list_children(folder_id)
                except Exception as exc:
                    raise ValueError(
                        f'Authorized F-folder {folder_name} ({folder_id}) is inaccessible and cannot be skipped: {exc}'
                    ) from exc
                images = [
                    Source(
                        id=entry['id'],
                        name=entry['name'],
                        folder_id=folder_id,
                        folder_name=folder_name,
                        path=f"{path}/{entry['name']}",
                        mime_type=entry['mimeType'],
                    )
                    for entry in entries
                    if entry.get('mimeType') in IMAGE_MIME
                ]
                if not images:
                    raise ValueError(
                        f'Authorized F-folder {folder_name} ({folder_id}) contains no images and cannot be skipped'
                    )
                out.extend(sorted(images, key=lambda item: (item.path, item.id)))
            out = select_authorized_sources(out)
            if len({source.name for source in out}) != len(out):
                raise ValueError('Duplicate source filename across folder hierarchy; cannot reconcile Asset QC by filename')
            return out

        seen, out, stack = set(), [], [(root, root_name)]
        while stack:
            fid, path = stack.pop()
            if fid in seen:
                raise ValueError('Drive folder cycle or duplicate folder ID')
            seen.add(fid)
            try:
                children = self._list_children(fid)
            except Exception as exc:
                raise ValueError(f'Source folder {path} ({fid}) is inaccessible: {exc}') from exc
            for entry in sorted(children, key=lambda item: (item['name'], item['id']), reverse=True):
                if entry['mimeType'] == 'application/vnd.google-apps.folder':
                    stack.append((entry['id'], path + '/' + entry['name']))
                elif entry['mimeType'] in IMAGE_MIME:
                    out.append(Source(
                        id=entry['id'],
                        name=entry['name'],
                        folder_id=fid,
                        folder_name=path.rsplit('/', 1)[-1] or fid,
                        path=path + '/' + entry['name'],
                        mime_type=entry['mimeType'],
                    ))
        out.sort(key=lambda source: (source.path, source.id))
        out = select_authorized_sources(out)
        if len({source.name for source in out}) != len(out):
            raise ValueError('Duplicate source filename across folder hierarchy; cannot reconcile Asset QC by filename')
        return out

    def image(self, source):
        # Only called by inspection. Never injected into the commit service.
        return self._execute(lambda: self.api.files().get_media(fileId=source.id,supportsAllDrives=True))

    def file_meta(self, file_id: str):
        """Read-only Drive metadata for accessibility/type checks (no download)."""
        return self._execute(lambda: self.api.files().get(
            fileId=file_id,
            fields='id,name,mimeType,driveId',
            supportsAllDrives=True,
        ))

class Sheets:
    def __init__(self, credentials=None):
        from google.auth import default
        creds=credentials or default(scopes=['https://www.googleapis.com/auth/spreadsheets'])[0]
        self.api=_google_api('sheets','v4',creds,180).spreadsheets()
        self._headers={}
        self._cache={}
        self._lock=threading.RLock()

    def _execute(self, request):
        return _execute(self._lock, request)

    def _values(self,sid,range_):
        return self._execute(lambda: self.api.values().get(spreadsheetId=sid,range=range_,valueRenderOption='UNFORMATTED_VALUE')).get('values',[])

    def headers(self,sid,sheet):
        k=(sid,sheet)
        if k not in self._headers:
            rows=self._values(sid,f"'{sheet}'!1:12")
            found=next(((i+1,{str(v):j for j,v in enumerate(row) if v}) for i,row in enumerate(rows) if len(row)>1 and ('Project' in row or 'Check ID' in row or 'State Field' in row or 'Rule ID' in row)),None)
            if not found: raise ValueError(f'Header row not found: {sheet}')
            self._headers[k]=found
        return self._headers[k]

    def rows(self,sid,sheet):
        key=(sid,sheet)
        if key in self._cache:
            return [dict(row) for row in self._cache[key]]
        h,columns=self.headers(sid,sheet)
        values=self._values(sid,f"'{sheet}'!A{h+1}:ZZ")
        out=[]
        for n,r in enumerate(values,h+1):
            row={name:str(r[j]) if j<len(r) and r[j] is not None else '' for name,j in columns.items()}
            if any(row.values()): row['_row']=n;out.append(row)
        self._cache[key]=out
        return [dict(row) for row in out]

    def _put(self,sid,range_,values):
        self._execute(lambda: self.api.values().update(spreadsheetId=sid,range=range_,valueInputOption='RAW',body={'values':values}))
        actual=self._values(sid,range_)
        width=max((len(r) for r in values), default=0)
        def norm(rows):
            out=[]
            for r in rows:
                cells=[str(v) for v in r]+['']*width
                out.append(cells[:width])
            while len(out)<len(values):
                out.append(['']*width)
            return out
        # Sheets omits trailing empty cells, so a blank Response ID must still match.
        if norm(actual)!=norm(values):
            raise RuntimeError(f'Readback failed for {range_}')

    @staticmethod
    def col(index):
        s='';index+=1
        while index: index,r=divmod(index-1,26);s=chr(65+r)+s
        return s

    def write(self,sid,sheet,row,key=None):
        h,headers=self.headers(sid,sheet)
        existing=self.rows(sid,sheet)
        if isinstance(key, list):
            matches=[r for r in existing if all(r.get(k)==str(v) for k,v in key)]
        else:
            matches=[r for r in existing if key and r.get(key[0])==str(key[1])]
        if len(matches)>1: raise ValueError(f'Duplicate key in {sheet}: {key}')
        unknown=set(row)-set(headers)
        if unknown: raise ValueError(f'Missing columns in {sheet}: {unknown}')
        if matches:
            target=matches[0]['_row']
            updates=[(f"'{sheet}'!{self.col(headers[name])}{target}", '' if value is None else str(value)) for name,value in row.items()]
            self._put_cells(sid, updates)
            cached=self._cache.setdefault((sid,sheet),[])
            for current in cached:
                if current.get('_row')==target:
                    for name,value in row.items(): current[name]='' if value is None else str(value)
                    break
            return target
        target=max([h]+[r['_row'] for r in existing])+1
        width=max(headers.values())+1
        vals=['']*width
        stored={name:'' for name in headers}
        stored['_row']=target
        for name,value in row.items():
            text='' if value is None else str(value)
            vals[headers[name]]=text
            stored[name]=text
        self._put(sid,f"'{sheet}'!A{target}:{self.col(width-1)}{target}",[vals])
        self._cache.setdefault((sid,sheet),[]).append(stored)
        return target

    def _put_cells(self,sid,updates):
        data=[{'range':rng,'values':[[value]]} for rng,value in updates]
        self._execute(lambda: self.api.values().batchUpdate(spreadsheetId=sid,body={'valueInputOption':'RAW','data':data}))
        got=self._execute(lambda: self.api.values().batchGet(spreadsheetId=sid,ranges=[rng for rng,_ in updates],valueRenderOption='UNFORMATTED_VALUE')).get('valueRanges',[])
        if len(got)!=len(updates): raise RuntimeError('Readback failed for cell batch')
        for (rng,value),item in zip(updates,got):
            cells=item.get('values') or [['']]
            actual='' if not cells or not cells[0] else cells[0][0]
            if str(actual)!=str(value): raise RuntimeError(f'Readback failed for {rng}')

    def clear(self,sid,sheet,row_number,key):
        if not row_number: return
        h,headers=self.headers(sid,sheet)
        width=max(headers.values())+1
        self._put(sid,f"'{sheet}'!A{row_number}:{self.col(width-1)}{row_number}",[['']*width])
        cached=self._cache.get((sid,sheet))
        if cached is not None:
            self._cache[(sid,sheet)]=[row for row in cached if row.get('_row')!=row_number]

    def ensure_tab(self,sid,name,headers):
        meta=self._execute(lambda: self.api.get(spreadsheetId=sid,fields='sheets.properties.title'))
        if name not in {x['properties']['title'] for x in meta['sheets']}:
            self._execute(lambda: self.api.batchUpdate(spreadsheetId=sid,body={'requests':[{'addSheet':{'properties':{'title':name}}}]}))
            self._put(sid,f"'{name}'!A1:{self.col(len(headers)-1)}1",[list(headers)])
        self._headers.pop((sid,name),None)
        self._cache.pop((sid,name),None)

    def grid(self, sid, sheet, a1):
        return self._execute(lambda: self.api.values().get(
            spreadsheetId=sid, range=f"'{sheet}'!{a1}", valueRenderOption='UNFORMATTED_VALUE',
        )).get('values', [])

    def spreadsheet_meta(self, spreadsheet_id: str):
        """Read-only spreadsheet properties for accessibility checks (no tab writes)."""
        return self._execute(lambda: self.api.get(
            spreadsheetId=spreadsheet_id,
            fields='spreadsheetId,properties.title',
        ))

    def field(self,sid,name):
        for row in self.rows(sid,'Continuation State'):
            if row['State Field']==name:return row['Current Value']
        raise ValueError('Missing continuation field '+name)

    def set_field(self,sid,name,value):
        self.write(sid,'Continuation State',{'Current Value':str(value)},('State Field',name))
