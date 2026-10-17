from xml.etree import ElementTree as ET
from packages.authoring import generated_renderer as r
from packages.authoring.build import W
import pytest

def pdf(pages):
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'']
    pageids=[]
    for lines in pages:
        pageid=len(objects)+1;contentid=pageid+1;pageids.append(pageid)
        objects.append(('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.3 841.9] /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /Contents %s 0 R >>'%contentid).encode())
        data='\n'.join('BT /F1 12 Tf 1 0 0 1 %s %s Tm (%s) Tj ET'%(x,y,text) for text,x,y in lines).encode()
        objects.append(b'<< /Length '+str(len(data)).encode()+b' >>\nstream\n'+data+b'\nendstream')
    objects[1]=('<< /Type /Pages /Count %s /Kids [%s] >>'%(len(pageids),' '.join('%s 0 R'%i for i in pageids))).encode()
    output=b'%PDF-1.4\n';offsets=[0]
    for n,obj in enumerate(objects,1):offsets.append(len(output));output+=str(n).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    pos=len(output);output+=('xref\n0 %s\n0000000000 65535 f \n'%(len(objects)+1)).encode()
    for offset in offsets[1:]:output+=('%010d 00000 n \n'%offset).encode()
    return output+('trailer\n<< /Size %s /Root 1 0 R >>\nstartxref\n%s\n%%%%EOF'%(len(objects)+1,pos)).encode()

def prepared():
    root=ET.fromstring('<w:document xmlns:w="'+W+'"><w:body><w:p><w:r><w:t>Body1Body2</w:t></w:r></w:p></w:body></w:document>')
    return {'config':{'margin_bottom_twips':1440,'margin_top_twips':1440,'font_size_halfpoints':24,'footer_text':'','header_text':'','page_number':True},'roots':{'word/document.xml':root,'word/numbering.xml':ET.Element('{'+W+'}numbering')}}

@pytest.mark.parametrize('mode',['valid','unrefreshed','body_digit_lost','footer_in_body','extra_text'])
def test_actual_pdfium_geometry(mode):
    pages=[[('Body1',72,700),('1',290,36)],[('Body2',72,700),('2',290,36)]]
    expected=None
    if mode=='unrefreshed':pages[1][-1]=('1',290,36);expected='AUTHORING_PAGE_NUMBER_OR_FOOTER_UNVERIFIED'
    if mode=='body_digit_lost':pages[1][0]=('Body',72,700);expected='AUTHORING_COMPLETE_BODY_TEXT_UNVERIFIED'
    if mode=='footer_in_body':pages[0][-1]=('1',290,100);expected='AUTHORING_PAGE_NUMBER_OR_FOOTER_UNVERIFIED'
    if mode=='extra_text':pages[0].append(('Extra',72,650));expected='AUTHORING_COMPLETE_BODY_TEXT_UNVERIFIED'
    result=r.inspect_pdf(pdf(pages),prepared())
    if expected:assert expected in result['gaps'],result
    else:assert result['gaps']==[],result
