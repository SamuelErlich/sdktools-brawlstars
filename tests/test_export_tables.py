import copy
import hashlib
import io
import sys
import unittest
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from export_tables import ALLOWLIST, select_assets, summarize_csv

class TablesTests(unittest.TestCase):
    def fixture(self):
        raw=b'Name,Value\nstring,int\nExample,0\n,\n'
        specs=[dict(relative_path=p,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),column_count=2,logical_rows=4,named_records=1) for p in sorted(ALLOWLIST)]
        memory=io.BytesIO()
        with zipfile.ZipFile(memory,'w') as z:
            for r in specs:z.writestr('Payload/test.app/'+r['relative_path'],raw)
        memory.seek(0)
        return memory,{'schema_version':1,'assets':specs},raw
    def test_preserves_continuation_and_zero(self):
        stream,index,raw=self.fixture()
        with zipfile.ZipFile(stream) as z: rows=select_assets(z,index,'Payload/test.app')
        self.assertEqual(rows[0][1],raw)
        self.assertEqual(rows[0][2]['continuation_or_empty_rows'],1)
        self.assertEqual(rows[0][2]['named_records'],1)
    def test_wrong_hash_rejected(self):
        stream,index,_=self.fixture(); index['assets'][0]['sha256']='0'*64
        with zipfile.ZipFile(stream) as z,self.assertRaisesRegex(ValueError,'hash'):select_assets(z,index,'Payload/test.app')
    def test_path_outside_allowlist_rejected(self):
        stream,index,_=self.fixture();index['assets'][0]['relative_path']='../private.csv'
        with zipfile.ZipFile(stream) as z,self.assertRaisesRegex(ValueError,'allowlist'):select_assets(z,index,'Payload/test.app')
    def test_wrong_csv_metadata_rejected(self):
        stream,index,_=self.fixture();index['assets'][0]['logical_rows']+=1
        with zipfile.ZipFile(stream) as z,self.assertRaisesRegex(ValueError,'metadata'):select_assets(z,index,'Payload/test.app')
    def test_duplicate_columns_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):summarize_csv(b'Name,Name\nstring,string\nA,B\n')
    def test_size_limit(self):
        with self.assertRaisesRegex(ValueError,'size'):summarize_csv(b'a'*(1024*1024+1))

if __name__=='__main__':unittest.main()
