"""Live integrations. Source reader and register writer are kept separate."""
from __future__ import annotations
from typing import Any
import io

IMAGE_MIME = {'image/jpeg','image/png','image/tiff','image/webp'}

class Drive:
    def __init__(self, credentials=None):
        from google.auth import default
        from googleapiclient.discovery import build
        creds = credentials or default(scopes=['https://www.googleapis.com/auth/drive.readonly'])[0]
        self.api = build('drive','v3',credentials=creds,cache_discovery=False)

    def enumerate(self, root: str):
        from app.models import Source
        seen, out, stack = set(), [], [(root,'')]
        while stack:
            fid, path = stack.pop()
            if fid in seen: raise ValueError('Drive folder cycle or duplicate folder ID')
            seen.add(fid)
            token = None
            children = []
            while True:
                response = self.api.files().list(q=f"'{fid}' in parents and trashed = false", fields='nextPageToken,files(id,name,mimeType)',pageSize=1000,pageToken=token,supportsAllDrives=True,includeItemsFromAllDrives=True).execute()
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
        return self.api.files().get_media(fileId=source.id,supportsAllDrives=True).execute()

class Sheets:
    def __init__(self, credentials=None):
        from google.auth import default
        from googleapiclient.discovery import build
        creds=credentials or default(scopes=['https://www.googleapis.com/auth/spreadsheets'])[0]
        self.api=build('sheets','v4',credentials=creds,cache_discovery=False).spreadsheets()
        self._headers={}

    def _values(self,sid,range_):
        return self.api.values().get(spreadsheetId=sid,range=range_,valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])

    def headers(self,sid,sheet):
        k=(sid,sheet)
        if k not in self._headers:
            rows=self._values(sid,f"'{sheet}'!1:12")
            found=next(((i+1,{str(v):j for j,v in enumerate(row) if v}) for i,row in enumerate(rows) if len(row)>1 and ('Project' in row or 'Check ID' in row or 'State Field' in row)),None)
            if not found: raise ValueError(f'Header row not found: {sheet}')
            self._headers[k]=found
        return self._headers[k]

    def rows(self,sid,sheet):
        h,columns=self.headers(sid,sheet)
        values=self._values(sid,f"'{sheet}'!A{h+1}:ZZ")
        out=[]
        for n,r in enumerate(values,h+1):
            row={name:str(r[j]) if j<len(r) and r[j] is not None else '' for name,j in columns.items()}
            if any(row.values()): row['_row']=n;out.append(row)
        return out

    def _put(self,sid,range_,values):
        self.api.values().update(spreadsheetId=sid,range=range_,valueInputOption='RAW',body={'values':values}).execute()
        actual=self._values(sid,range_)
        if [[str(v) for v in r] for r in actual]!=[[str(v) for v in r] for r in values]:
            raise RuntimeError(f'Readback failed for {range_}')

    @staticmethod
    def col(index):
        s='';index+=1
        while index: index,r=divmod(index-1,26);s=chr(65+r)+s
        return s

    def write(self,sid,sheet,row,key=None):
        h,headers=self.headers(sid,sheet)
        existing=self.rows(sid,sheet)
        matches=[r for r in existing if key and r.get(key[0])==str(key[1])]
        if len(matches)>1: raise ValueError(f'Duplicate key in {sheet}: {key}')
        target=matches[0]['_row'] if matches else max([h]+[r['_row'] for r in existing])+1
        if matches:
            for name,value in row.items():
                if name not in headers: raise ValueError(f'Missing {name} in {sheet}')
                col=self.col(headers[name]);self._put(sid,f"'{sheet}'!{col}{target}",[[str(value)]])
        else:
            unknown=set(row)-set(headers)
            if unknown: raise ValueError(f'Missing columns in {sheet}: {unknown}')
            width=max(headers.values())+1
            vals=['']*width
            for name,value in row.items(): vals[headers[name]]=str(value)
            self._put(sid,f"'{sheet}'!A{target}:{self.col(width-1)}{target}",[vals])
        return target

    def ensure_tab(self,sid,name,headers):
        meta=self.api.get(spreadsheetId=sid,fields='sheets.properties.title').execute()
        if name not in {x['properties']['title'] for x in meta['sheets']}:
            self.api.batchUpdate(spreadsheetId=sid,body={'requests':[{'addSheet':{'properties':{'title':name}}}]}).execute()
            self._put(sid,f"'{name}'!A1:{self.col(len(headers)-1)}1",[list(headers)])
        self._headers.pop((sid,name),None)

    def field(self,sid,name):
        for row in self.rows(sid,'Continuation State'):
            if row['State Field']==name:return row['Current Value']
        raise ValueError('Missing continuation field '+name)

    def set_field(self,sid,name,value):
        self.write(sid,'Continuation State',{'Current Value':str(value)},('State Field',name))
