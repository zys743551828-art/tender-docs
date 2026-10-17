"""同连接初始化后的pgvector精确能力授权；隔离PG，无真实模型。"""
from pathlib import Path
import hashlib
import json

import psycopg

from isolated_pg import database,APP

HERE=Path(__file__).resolve().parent


def main():
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:
            schema=c.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector'").fetchone()['nspname']
        assert schema=='design'
        connect.owner_sql("CREATE TABLE design.architect_vector_probe(value design.vector(1024) CHECK(abs(design.vector_norm(value)-1)<0.001)); GRANT SELECT,INSERT ON design.architect_vector_probe TO tender_worker;")
        vector='['+','.join(map(str,[1.0]+[0.0]*1023))+']'
        with connect() as c:
            try:c.execute('INSERT INTO design.architect_vector_probe VALUES(%s::design.vector)',(vector,));raise AssertionError('Expected missing runtime function privilege')
            except psycopg.errors.InsufficientPrivilege as error:before={'sqlstate':error.sqlstate,'function_denied':True}
        proposed="GRANT EXECUTE ON FUNCTION design.vector(design.vector,integer,boolean),design.vector_norm(design.vector),design.cosine_distance(design.vector,design.vector) TO tender_worker;"
        connect.owner_sql(proposed)
        with connect() as c:
            c.execute('SET LOCAL search_path=design,public')
            c.execute('INSERT INTO design.architect_vector_probe VALUES(%s::vector)',(vector,))
            row=c.execute('SELECT vector_norm(value) AS norm,value <=> %s::vector AS distance FROM architect_vector_probe',(vector,)).fetchone()
            assert row['norm']==1 and row['distance']==0
        negative=[]
        for label,encoded,expected in [('wrong_dimensions','['+','.join(['1']*768)+']','22000'),('zero_norm','['+','.join(['0']*1024)+']','23514')]:
            with connect() as c:
                try:c.execute('INSERT INTO design.architect_vector_probe VALUES(%s::design.vector)',(encoded,));raise AssertionError('Invalid vector accepted')
                except psycopg.Error as error:
                    assert error.sqlstate==expected,(label,error.sqlstate)
                    negative.append({'case':label,'rejected_sqlstate':error.sqlstate})
        with connect() as c:
            denied=c.execute("SELECT has_function_privilege('tender_score_read','design.vector_norm(design.vector)','EXECUTE') AS score,has_function_privilege('tender_research_worker','design.vector_norm(design.vector)','EXECUTE') AS research").fetchone()
            assert denied=={'score':False,'research':False}
        for statement in ('SELECT count(*) FROM design.outcomes','UPDATE design.model_artifacts SET id=id WHERE false'):
            with connect() as c:
                try:c.execute(statement);raise AssertionError('Unrelated capability unexpectedly permitted')
                except psycopg.errors.InsufficientPrivilege:pass
        result={'scope':'actual same-connection initializer and nonowner pgvector capability mechanism; synthetic vector only','extension_schema':schema,'before_minimal_grant':before,'minimal_grant_sql':proposed,'after':dict(row),'invalid_vectors':negative,'score_and_research_no_vector_grant':True,'worker_labels_and_model_publication_still_denied':True,'model_inference':False,'paid_calls':0,'credentials_read':False,'inputs':{name:hashlib.sha256((APP/'packages/contracts/sql'/name).read_bytes()).hexdigest() for name in ('core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql')},'not_claimed':['real BGE insertion','API retrieval','formal scoring']}
        (HERE/'extension-minimal-grant-result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'extension_schema':schema,'before_sqlstate':before['sqlstate'],'after_exact_three_grants_pass':True,'worker_forbidden_capabilities_still_denied':True}))


if __name__=='__main__':main()
