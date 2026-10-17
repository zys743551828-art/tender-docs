"""Read only the exact retained synthetic restore and its three owned OSS objects."""
import hashlib
import json
import os
from pathlib import Path
import sys

import psycopg
from psycopg.conninfo import make_conninfo

OUT = Path(__file__).resolve().parent
BASE = Path('/workspace/tender-workbench')
sys.path.insert(0, str(OUT / 'runtime'))
from deploy.backups import generic, run
from packages.domain import storage


def main():
    checks = []
    result = {'passed': False, 'checks': checks, 'database_mutations': 0,
              'new_databases_created': 0, 'model_calls': 0, 'consumers_started': 0,
              'object_requests': {'GET': 0, 'PUT': 0, 'LIST': 0, 'DELETE': 0},
              'full_application_restore_qualified': False,
              'production_rpo_rto_qualified': False}

    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})
        assert condition, name

    try:
        rows = json.loads((OUT / 'input-manifest.json').read_text())['sources']
        check('ten_frozen_sources_match', all(
            hashlib.sha256((OUT / 'runtime' / row['path']).read_bytes()).hexdigest() == row['sha256']
            for row in rows))
        values = run.configuration(BASE / '.local/external-release.env')
        # Only the original restricted API DSN and object-port keys are used.
        api_dsn = values['API_DATABASE_DSN']
        values = {k: v for k, v in values.items() if k.startswith('OBJECT_STORE_')}
        archive = BASE / '.local/backups/generic-native-04843840b3f94e04ac96ab948bcb8374'
        proof = json.loads((archive / 'native-proof.json').read_text())
        # Path adapter: the frozen generic module validates this existing private archive.
        run.ROOT = BASE
        run.ARCHIVE_ROOT = BASE / '.local/backups'
        manifest = generic.validate(archive, require_objects=True)
        check('actual_dump_and_three_archived_objects_validate', len(manifest['objects']) == 3)
        check('retained_archive_is_private', all(
            not p.is_symlink() and not (p.stat().st_mode & 0o077)
            for p in [archive, *archive.rglob('*')]))
        target = make_conninfo(api_dsn, dbname=proof['database'])
        with psycopg.connect(target) as c:
            check('restore_defaults_read_only', c.execute('SHOW transaction_read_only').fetchone()[0] == 'on')
            generic.restricted(c)
            check('all_108_table_contents_match_archived_snapshot', generic.inventory(c) == manifest['tables'])
            check('exact_catalog_extensions_and_schemas_match', generic.catalog(c) == manifest['catalog'])
            check('three_originals_and_blobs_only', c.execute('SELECT count(*) FROM design.blobs').fetchone()[0] == 3
                  and c.execute('SELECT count(*) FROM design.document_versions').fetchone()[0] == 3)
            check('quoted_schema_vector_content_survives', c.execute(
                'SELECT note,embedding FROM "future.native"."quote""rows"').fetchone()
                  == ('真实新schema合成行', '[1,0]'))
        try:
            with psycopg.connect(make_conninfo(target, options='-c default_transaction_read_only=off')) as c:
                # Transaction always rolls back; only an actual ACL denial is acceptance.
                c.execute('INSERT INTO "future.native"."quote""rows"(id,note) VALUES(gen_random_uuid(),%s)', ('must fail',))
                c.rollback()
        except psycopg.errors.InsufficientPrivilege:
            check('actual_insert_denied_with_readonly_default_disabled', True)
        else:
            check('actual_insert_denied_with_readonly_default_disabled', False)
        with psycopg.connect(make_conninfo(api_dsn, dbname=proof['source_probe_database'])) as c:
            check('source_four_objects_and_two_rows_preserved', c.execute('SELECT count(*) FROM design.blobs').fetchone()[0] == 4
                  and c.execute('SELECT count(*) FROM "future.native"."quote""rows"').fetchone()[0] == 2)
        for k, v in values.items():
            os.environ[k] = v
        os.environ['OBJECT_STORE_PREFIX'] = proof['restored_object_prefix']
        port = storage.client()
        for item in manifest['objects']:
            result['object_requests']['GET'] += 1
            data = run.read_object(port, storage.bucket(), item['key'], item['bytes'], item['sha256'])
            check('exact_restored_object_bytes_' + item['id'], data == (archive / item['file']).read_bytes())
        result.update(passed=True, tables_verified=len(manifest['tables']), objects_verified=3,
                      object_bytes_verified=sum(item['bytes'] for item in manifest['objects']),
                      limitation='Readback of existing synthetic developer restore; did not independently execute a new restore, newest schema or application recovery')
    except Exception as error:
        result['failure_type'] = type(error).__name__
        # Suppress exception text, DSNs, private keys and native HTTP details.
    (OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'checks'}, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
