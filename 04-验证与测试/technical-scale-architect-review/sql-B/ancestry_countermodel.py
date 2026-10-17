"""Offline set contradiction only; no SQL, imports from app, DB or remote services."""
from pathlib import Path
from itertools import combinations
import json,hashlib
out=Path(__file__).parent
sql=Path('/workspace/tender-docs/04-验证与测试/research-backend-checks/technical-scale/sql-draft-B/technical-scale-authority.sql')
assert hashlib.sha256(sql.read_bytes()).hexdigest()=='3fdce8d604ad1f17bf324330b7d15078b8cc063fa883eaa201852e67d48e9674'
# One complete converted PDF contains the sourced total declaration.
# Its parse child and PDF root reference PDF; a Word root is the provenance ancestor.
direct_evidence_documents={'converted-pdf'}
ancestor_documents={'converted-pdf','original-word'}
rows=[]
for n in range(3):
 for selected in combinations(sorted(ancestor_documents),n):
  claims=set(selected)
  exact_direct=(claims==direct_evidence_documents) # SQL line129
  all_ancestors=ancestor_documents<=claims          # SQL line168
  rows.append({'selected_claim_documents':list(selected),'line129_exact_direct_evidence':exact_direct,'line168_all_ancestors_have_claim':all_ancestors,'both':exact_direct and all_ancestors})
assert not any(row['both'] for row in rows)
result={'kind':'static relational set countermodel, not executed PostgreSQL qualification','sql_sha256':hashlib.sha256(sql.read_bytes()).hexdigest(),'direct_evidence_documents':sorted(direct_evidence_documents),'source_ancestor_document_versions':sorted(ancestor_documents),'all_possible_claim_subsets':rows,'conclusion':'No selected claim set can satisfy both guards for a distinct original-Word ancestor. Adding fake direct Word evidence is not a valid remedy: no complete readable Word parse is created by the actual bridge.'}
(out/'ancestry-countermodel.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
