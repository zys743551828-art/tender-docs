import sys,io,json
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,'/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/rich-port-static-01/frozen-tree')
from PIL import Image,ImageDraw
import pypdfium2 as pdfium
from packages.authoring import rich_images,rich_pdf,templates
out=Path(__file__).parent
im=Image.new('RGB',(120,90),'white');ImageDraw.Draw(im).text((50,35),'9',fill='black');buf=io.BytesIO();im.save(buf,format='PNG');im.close();normal=rich_images.normalize(buf.getvalue(),'image/png');asset,version=str(uuid4()),str(uuid4());snap={'sections':[{'content':[{'type':'image','attrs':{'id':'image-node','asset_id':asset,'asset_revision':1,'asset_version_id':version,'width_twips':2400,'height_twips':1800}}]}]};m=[{'asset_id':asset,'asset_revision':1,'asset_version_id':version,'bytes':normal['bytes'],'normalization_proof':normal['proof']}]
result={}
for covered in [False,True]:
 doc=pdfium.PdfDocument.new();page=doc.new_page(11906/20,16838/20);orig=Image.open(io.BytesIO(normal['bytes']));bitmap=pdfium.PdfBitmap.from_pil(orig,recopy=True)
 try:
  image=pdfium.PdfImage.new(doc);image.set_bitmap(bitmap);image.set_matrix(pdfium.PdfMatrix(120,0,0,90,72,600));page.insert_obj(image)
  if covered:
   cover=pdfium.raw.FPDFPageObj_CreateNewRect(72+45,600+90-55,25,25);pdfium.raw.FPDFPageObj_SetFillColor(cover,255,255,255,255);pdfium.raw.FPDFPath_SetDrawMode(cover,pdfium.raw.FPDF_FILLMODE_WINDING,0);pdfium.raw.FPDFPage_InsertObject(page,cover)
  page.gen_content();b=io.BytesIO();doc.save(b);raw=b.getvalue()
 finally:bitmap.close();orig.close();page.close();doc.close()
 proof=rich_pdf.inspect_images(raw,snap,m,templates.DEFAULT_STYLE);result['covered' if covered else 'baseline']=proof
 (out/('image-digit-covered.pdf' if covered else 'image-digit-baseline.pdf')).write_bytes(raw)
assert result['baseline']['coverage']=='complete'
assert result['covered']['coverage']=='complete',result
(out/'partial-cover-counterexample.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'baseline_coverage':result['baseline']['coverage'],'covered_coverage':result['covered']['coverage'],'covered_pixel_error':result['covered']['images'][0]['page_pixel_mean_error'],'scope':'actual synthetic PDFium; embedded digit covered by opaque white rect; no renderer/DB/network'},ensure_ascii=False))
