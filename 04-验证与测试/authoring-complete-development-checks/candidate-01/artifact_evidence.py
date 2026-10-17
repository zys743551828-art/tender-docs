"""One public synthetic DOCX from frozen source, real PG and explicit memory object port."""
import io
import json
from pathlib import Path
import sys
from uuid import UUID
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE / 'frozen-tree'
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
import test_authoring_integration as fixture
from packages.authoring import templates, structure_repository
from packages.authoring.repository import create_draft, latest_sections, save_draft
from packages.authoring.content import SCHEMA_DIGEST
from packages.authoring.build_repository import freeze_build, prepare_build, compute_prepared_build
from packages.domain import storage
from packages.domain.runtime import Command, connection, etag, uid, clean, sha

actor, bid, _, _, _ = fixture.draft.__wrapped__()
with connection() as c:
    directory = templates.create(c, actor, {'name':'公开合成施工目录','kind':'outline','config':{'schema_version':1,'nodes':[{'key':'technical','parent_key':None,'ordinal':0,'title':'施工组织方案','node_type':'chapter','goal':'编写施工总体安排','planned_pages':5},{'key':'schedule','parent_key':'technical','ordinal':0,'title':'进度安排','node_type':'chapter'},{'key':'appendix','parent_key':None,'ordinal':1,'title':'附录','node_type':'appendix'}]}})
    style = templates.create(c, actor, {'name':'公开合成版式','kind':'layout','config':templates.DEFAULT_STYLE|{'font_size_halfpoints':24,'margin_left_twips':1080,'cover_enabled':True,'cover_title':'公开合成投标正文','header_text':'合成项目 · 施工组织','footer_text':'仅用于工程机制验证'}})
    draft, _ = create_draft(c, bid, actor, '公开合成正文', UUID(directory['id']))
    changes=[]
    for index, row in enumerate(latest_sections(c,bid,draft)):
        content=row['content']
        content['content'][0]['content']=[{'type':'text','text':['施工安排围绕工序、人员和材料组织展开。','阶段计划尚待本项目原件与人工确认。','保留人工编写内容，历史方案不自动成为本项目事实。'][index],'marks':[{'type':'bold'}] if index==0 else []}]
        changes.append({'section_id':str(row['section_id']),'base_section_revision':1,'schema_digest':SCHEMA_DIGEST,'content':content})
    save_draft(c,bid,draft,actor,{'base_draft_revision':1,'changes':changes},etag('draft',draft,1))
    candidate=structure_repository.style_preview(c,bid,draft,actor,{'base_draft_revision':2,'style_template_version_id':style['id'],'format_bindings':[],'format_review_ack':None},etag('draft',draft,2))
    command=Command(c,actor,'public-a2-artifact-style',UUID(candidate['action_id']),str(uid()),{},None)
    saved=structure_repository.resolve(c,bid,UUID(candidate['action_id']),actor,command.id,'confirm',etag('draft',draft,2));command.finish(saved)
    input_id,manifest=freeze_build(c,bid,{'draft_id':str(draft),'draft_revision':3,'directory_template_version_id':directory['id'],'style_template_version_id':style['id'],'formats':['docx'],'follow_up':'none'},actor,None)
with connection(True) as c:
    prepared=prepare_build(c,bid,input_id)
objects={}
storage.put=lambda key,data,media: objects.__setitem__(key,data)
storage.get=objects.__getitem__
computed=compute_prepared_build(prepared)
output=next(o for o in computed['outputs'] if o['format']=='docx')
raw=objects[output['object_key']]
(HERE/'evidence/synthetic-tree-plan-templates.docx').write_bytes(raw)
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    parts=archive.namelist()
    assert 'word/header.xml' in parts and 'word/footer.xml' in parts
    assert b'fldSimple' not in archive.read('word/footer.xml')
report={'scope':'public synthetic DOCX only; real nonowner PG frozen snapshot; explicit memory objects; no renderer/model/Kafka','bid_id':str(bid),'draft_id':str(draft),'input_id':str(input_id),'snapshot_id':saved['snapshot_id'],'docx_digest':sha(raw),'byte_size':len(raw),'parts':parts,'template_versions':{'directory':directory['id'],'style':style['id']},'manifest':manifest,'outputs':computed['outputs'],'pdf':'not_requested_in_public_artifact; failed format independence tested separately','dynamic_page_numbers':'unsupported_in_candidate01','actual_section_page_ranges':None}
(HERE/'evidence/synthetic-artifact.json').write_text(json.dumps(clean(report),ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'docx_digest':sha(raw),'byte_size':len(raw),'parts':len(parts)},ensure_ascii=False))
