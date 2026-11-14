"""Provincial thermal/storage energy-component arithmetic, never a formal bill.
Reuses retained R14 typed provenance, directional ledger and settlement formulas.
"""
import importlib.util
from pathlib import Path
from hashlib import sha256
import sys
from decimal import Decimal
from datetime import timedelta
SOURCE=Path(__file__).resolve().parent.parent/'guangdong_r21_day_verified_final_v1_20261112/source/repo/src/smm/gd_r14_settlement.py'
_spec=importlib.util.spec_from_file_location('_r36_retained_r14_settlement',SOURCE)
legacy=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=legacy;_spec.loader.exec_module(legacy)
require=legacy.require

def settle_energy(*, technology, participation, participation_source, agency_mode, agency_contracts, **inputs):
    """Inputs follow retained calculate typed API. Agency contracts cannot overlap LT IDs.
    Thermal: hourly, no nuclear base. Storage: distinct charge/discharge units.
    Non-participation is not silently rewritten into a zero DA award.
    """
    require(technology in ('coal','gas','independent_storage'),'unsupported category')
    require(participation in ('participates','does_not_participate'),'explicit participation state required')
    require(isinstance(participation_source,str) and participation_source.strip(),'participation source required')
    require(participation=='participates','non-DA settlement mapping not established by available provincial transition sources')
    key=inputs['key'];channel=inputs['channel'];asof=inputs['asof']
    expected='independent_storage' if technology=='independent_storage' else 'generator'
    require(inputs.get('settlement_class')==expected,'technology/settlement class mismatch')
    require(inputs.get('contracts') is not None,'explicit sourced LT contracts, including known zero, required')
    agency_rows=[]
    if technology=='independent_storage':
        require(agency_mode=='not_applicable' and agency_contracts is None,'storage has no generator agency deductions')
    else:
        require(key.end-key.start==timedelta(hours=1),'thermal energy settlement is hourly')
        require(inputs.get('base') is not None and inputs['base'].check('MWh',channel,asof)==0,
                'coal/gas nuclear base is not applicable; explicit sourced zero required')
        require(agency_mode=='non_decoupled_provincial','decoupled regime requires out-of-scope system boundary; do not invent it')
        require(type(agency_contracts) is tuple and len(agency_contracts)>0,'explicit sourced agency contracts including zero required')
        ltids={c.contract_id for c in inputs['contracts']}
        require(not ltids.intersection(c.contract_id for c in agency_contracts),'agency/LT double counting')
        require(len({c.contract_id for c in agency_contracts})==len(agency_contracts),'duplicate agency contract')
        pd=inputs['da'].price.check('CNY/MWh',channel,asof)
        qsum=Decimal(0)
        for c in agency_contracts:
            # Scope declaration must accompany each agency instrument; exports are excluded.
            require(c.contract_id.startswith('GD_LOCAL:'),'agency instrument must explicitly declare provincial-only scope')
            q=c.quantity.check('MWh',channel,asof);p=c.strike.check('CNY/MWh',channel,asof);ref=c.reference_price.check('CNY/MWh',channel,asof)
            require(all(x is not None for x in (q,p,ref,pd)),'missing agency/DA price')
            require(q>=0,'thermal agency sign')
            if channel=='formal_data':
                require(all(v.evidence.status=='official' for v in (c.quantity,c.strike,c.reference_price)),'official agency data required')
            qsum+=q
            agency_rows.append({'contract_id':c.contract_id,'energy':q*p,'congestion':q*(pd-ref),'total':q*(p+pd-ref),'rule':'GD §7.2.2 non-decoupled provincial subset'})
        require(inputs.get('agency_export') is not None and inputs['agency_export'].check('MWh',channel,asof)==qsum,'agency deduction must equal same instruments, no export quantity')
    old=legacy.calculate(**inputs)
    energy=old['rows'][:3]
    require(all(r['amount_CNY'] is not None for r in energy),'required settlement input/basis missing: '+str([r['missing_reason'] for r in energy if r['amount_CNY'] is None]))
    # Split embedded LT congestion for inspection, not a second charge.
    pd=inputs['da'].price.check('CNY/MWh',channel,asof)
    lt_principal=sum((c.quantity.check('MWh',channel,asof)*c.strike.check('CNY/MWh',channel,asof) for c in inputs['contracts']),Decimal(0))
    lt_total=energy[0]['amount_CNY'];agency_total=sum((r['total'] for r in agency_rows),Decimal(0))
    subtotal=old['energy_subtotal_CNY']+agency_total
    require(subtotal.is_finite(),'nonfinite arithmetic')
    return {'key':key,'technology':technology,'participation':participation,'participation_source':participation_source,
            'components':{'LT_principal':lt_principal,'LT_congestion':lt_total-lt_principal,
                          'agency_principal':sum((r['energy'] for r in agency_rows),Decimal(0)),
                          'agency_congestion':sum((r['congestion'] for r in agency_rows),Decimal(0)),
                          'DA_deviation':energy[1]['amount_CNY'],'RT_deviation':energy[2]['amount_CNY'],
                          'nuclear_base':Decimal(0)},
            'energy_component_subtotal':subtotal,'agency_rows':agency_rows,'formal_bill':None,'payable':False,
            'not_included':['monthly compensation/recovery/allocations','ancillary services','tax/invoice adjustments'],
            'legacy_result':old,'legacy_source_sha256':sha256(SOURCE.read_bytes()).hexdigest(),
            'applicability':'2026 provincial repost §6.5 requires regional/provincial transition verification',
            'official_source_authenticated':False}

# Reuse rather than changing hourly means to volume-weighted prices or power schedules to meters.
aggregate_hour=legacy.aggregate_hour
split_hourly_contract=legacy.split_hourly_contract

def paired_storage(discharge,charge):
    require(discharge['technology']==charge['technology']=='independent_storage','storage ledgers only')
    a,b=discharge['key'],charge['key']
    require(a.direction=='discharge' and b.direction=='charge','directional pair')
    require(a.resource_id==b.resource_id and a.node_id==b.node_id and a.start==b.start and a.end==b.end,'exact resource/node/time match')
    require(a.settlement_unit_id!=b.settlement_unit_id,'separate directional settlement units required')
    return {'discharge':discharge,'charge':charge,'net_energy_component_subtotal':discharge['energy_component_subtotal']+charge['energy_component_subtotal'],'formal_bill':None,'payable':False}
