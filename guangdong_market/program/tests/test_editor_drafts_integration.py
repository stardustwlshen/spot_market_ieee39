"""Actual default page, parameter validators and snapshot writer; no UI doubles."""
import sys,json
from pathlib import Path
from streamlit.testing.v1 import AppTest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))

def app():
 a=AppTest.from_file(str(ROOT/'app/主页.py'),default_timeout=60).run()
 assert not a.exception and not a.error
 return a

def number(a,label):return next(n for n in a.number_input if n.label==label)
def button(a,label):return next(b for b in a.button if b.label==label)
def selector(a,label):return next(n for n in a.selectbox if n.label==label)

def test_actual_thermal_draft_validated_then_written_to_snapshot(tmp_path):
 from gd_provincial_inputs import snapshot,codec
 a=app();unit=a.selectbox(key='edit_thermal_unit').value
 old=number(a,'最大出力（MW）').value
 number(a,'最大出力（MW）').set_value(old+100.).run()
 assert not a.exception and a.button(key='prov_prepare').disabled
 button(a,'保存这台机组的修改').click().run()
 assert not a.error and not a.button(key='prov_prepare').disabled
 edits=a.session_state['unit_edits'];saved=edits[unit]['parameters']
 target=tmp_path/'snapshot'
 snapshot(ROOT/'data/default_scenario',target,thermal_edits={unit:saved})
 loads,_=codec();case=loads((target/'seed/case.json').read_bytes())
 assert case['energy_inputs']['thermal'][unit]['max_mw'][0]==old+100.
 metadata=json.loads((target/'USER_EDITS.json').read_text())
 assert metadata['thermal_edits'][unit]['max_mw']==old+100.

def test_actual_optional_edits_reach_case_and_source_stays_unchanged(tmp_path):
 from gd_provincial_inputs import snapshot,codec
 a=app();source=ROOT/'data/default_scenario';original=(source/'seed/case.json').read_bytes()
 kind,resource=selector(a,'选择储能或抽蓄').value
 assert kind=='battery'
 old=number(a,'充电功率上限（MW）').value
 number(a,'充电功率上限（MW）').set_value(old+10.).run()
 assert a.button(key='prov_prepare').disabled
 button(a,'保存参数').click().run()
 assert not a.error
 bus=selector(a,'新能源所在节点').value
 number(a,'电能量报价（元/MWh）').set_value(42.).run()
 assert a.button(key='prov_prepare').disabled
 button(a,'保存新能源报价').click().run()
 assert not a.error and not a.button(key='prov_prepare').disabled
 import hashlib
 token=hashlib.sha256(a.session_state['unit_edits_source'].encode()).hexdigest()[:12]
 resources=a.session_state['resource_edits_'+token]
 bids=a.session_state['renewable_bids_'+token]
 target=tmp_path/'optional';snapshot(source,target,resource_edits=resources,renewable_bids=bids)
 loads,_=codec();case=loads((target/'seed/case.json').read_bytes())
 assert case['batteries'][resource]['charge_max_mw']==old+10.
 prices={v for (b,t),v in case['energy_inputs']['renewable_bid_yuan_per_mwh'].items() if str(b)==bus}
 assert prices=={42.}
 assert (source/'seed/case.json').read_bytes()==original
