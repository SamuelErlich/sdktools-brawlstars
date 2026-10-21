import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from analyze_capture import summarize,unique_object

class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.target=json.loads((ROOT/'resources/Target.json').read_text())['target']
        self.index=json.loads((ROOT/'resources/AssetIndex.json').read_text())
        event={'elapsed_ms':1,'touch_id':1,'phase':'began','point':[20,40],'normalized':[.2,.4],'window_bounds_points':[0,0,100,100]}
        self.report={'schema':'SDKLaserCapture.Diagnostic','schema_version':1,'identity':self.target.copy(),
            'duration_ms':100,'events':[event,dict(event,elapsed_ms=50,phase='ended')],
            'snapshots':[{'elapsed_ms':100,'windows':[]}],'markers':[],'static_assets_exported':[]}
    def test_complete_contact_not_two_actions(self):
        r=summarize(self.report,self.target,self.index)
        self.assertEqual(r['counts']['complete_contacts'],1);self.assertFalse(r['coverage']['autoplay'])
    def test_wrong_build(self):
        self.report['identity']['version']='old'
        with self.assertRaisesRegex(ValueError,'identity'):summarize(self.report,self.target,self.index)
    def test_snapshot_after_session(self):
        self.report['snapshots'][0]['elapsed_ms']=200
        with self.assertRaisesRegex(ValueError,'timestamp'):summarize(self.report,self.target,self.index)
    def test_reused_contact(self):
        self.report['events'].append(dict(self.report['events'][0],elapsed_ms=60))
        with self.assertRaisesRegex(ValueError,'ended'):summarize(self.report,self.target,self.index)
    def test_nonfinite_coordinate(self):
        self.report['events'][0]['point'][0]=float('nan')
        with self.assertRaisesRegex(ValueError,'geometry'):summarize(self.report,self.target,self.index)
    def test_duplicate_json_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):json.loads('{"x":1,"x":2}',object_pairs_hook=unique_object)
    def test_asset_claim_not_file_proof(self):
        r=self.index['assets'][0]
        self.report['static_assets_exported']=[dict(r,status='exported')]
        result=summarize(self.report,self.target,self.index)['static_assets'][0]
        self.assertTrue(result['metadata_matches_base']);self.assertFalse(result['file_content_verified_by_this_summary'])

if __name__=='__main__':unittest.main()
