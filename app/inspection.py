from __future__ import annotations
import base64, json, time
from pathlib import Path
from app.credits import as_credit_error
from app.models import InspectionOutput

class Inspector:
    def __init__(self,settings,drive,client=None):
        self.settings=settings;self.drive=drive
        if client is None:
            from openai import OpenAI
            client=OpenAI(api_key=settings.openai_api_key,max_retries=0,timeout=180)
        self.client=client

    def inspect(self,job,batch,sources,context,rules):
        docs=Path(self.settings.governing_docs_dir)
        references=[]
        for path in sorted(docs.iterdir()):
            if path.suffix.lower() in {'.md','.txt'}:
                references.append(path.name+'\n'+path.read_text())
            elif path.suffix.lower()=='.docx':
                from docx import Document
                references.append(path.name+'\n'+'\n'.join(p.text for p in Document(path).paragraphs))
        if not references: raise ValueError('No readable governing documents')
        images=[]
        for src in sources:
            data=self.drive.image(src)
            images.append({'type':'input_text','text':f'Source filename: {src.name}; Drive ID: {src.id}'})
            images.append({'type':'input_image','image_url':f'data:{src.mime_type};base64,{base64.b64encode(data).decode()}'})
        instruction='\n\n'.join(references)+'\n\nReturn only the exact Lean Split v2 staging schema. Preserve filenames and never invent a missing image. Boundary states may remain open. No spreadsheet updates or cumulative calculations. All values must be strings.'
        payload={'job_id':job,'batch_id':batch,'context':context,'rules':rules,'authorized_filenames':[s.name for s in sources]}
        start=time.monotonic()
        try:
            response=self.client.responses.parse(model=self.settings.openai_model,input=[{'role':'system','content':instruction},{'role':'user','content':[{'type':'input_text','text':json.dumps(payload)}]+images}],text_format=InspectionOutput)
            output=response.output_parsed
            if output is None: raise ValueError('Missing structured output')
            usage=response.usage
            details=getattr(usage,'input_tokens_details',None)
            return output,{'input_tokens':usage.input_tokens,'cached_input_tokens':getattr(details,'cached_tokens',0) or 0,'output_tokens':usage.output_tokens,'elapsed_seconds':round(time.monotonic()-start,2),'response_id':response.id}
        except Exception as e:
            # API failures can be ambiguous. Caller checks staging before another attempt.
            setattr(e,'elapsed_seconds',round(time.monotonic()-start,2))
            credit = as_credit_error(e)
            if credit is not e:
                raise credit from e
            raise
