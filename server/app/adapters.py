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

    def enumerate(self, root: str):
        from app.models import Source
        root_name = self._execute(lambda: self.api.files().get(fileId=root, fields='name', supportsAllDrives=True))['name']
        seen, out, stack = set(), [], [(root, root_name)]
        while stack:
            fid, path = stack.pop()
            if fid in seen: raise ValueError('Drive folder cycle or duplicate folder ID')
            seen.add(fid)
            token = None
            children = []
            while True:
                response = self._execute(lambda fid=fid, token=token: self.api.files().list(q=f"'{fid}' in parents and trashed = false", fields='nextPageToken,files(id,name,mimeType)',pageSize=1000,pageToken=token,supportsAllDrives=True,includeItemsFromAllDrives=True))
                children.extend(response.get('files',[]))
                token = response.get('nextPageToken')
                if not token: break
            for f in sorted(children,key=lambda x:(x['name'],x['id']),reverse=True):
                if f['mimeType']=='application/vnd.google-apps.folder': stack.append((f['id'],path+'/'+f['name']))
                elif f['mimeType'] in IMAGE_MIME:
                    out.append(Source(id=f['id'],name=f['name'],folder_id=fid,folder_name=path.rsplit('/',1)[-1] or fid,path=path+'/'+f['name'],mime_type=f['mimeType']))
        out.sort(key=lambda s:(s.path,s.id))
        if len({s.name for s in out})!=len(out): raise ValueError('Duplicate source filename across folder hierarchy; cannot reconcile Asset QC by filename')
        return out

    def image(self, source):
        # Only called by inspection. Never injected into the commit service.
        return self._execute(lambda: self.api.files().get_media(fileId=source.id,supportsAllDrives=True))

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

    def field(self,sid,name):
        for row in self.rows(sid,'Continuation State'):
            if row['State Field']==name:return row['Current Value']
        raise ValueError('Missing continuation field '+name)

    def set_field(self,sid,name,value):
        self.write(sid,'Continuation State',{'Current Value':str(value)},('State Field',name))
