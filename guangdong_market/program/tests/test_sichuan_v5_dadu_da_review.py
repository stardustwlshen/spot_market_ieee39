"""Check the physical interpretation and four paired day-ahead publications."""
from pathlib import Path
import sys

import pytest

PROGRAM = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROGRAM/'app'))

from sichuan_v5_da_report import review_data, standalone_report
from sichuan_v5_da_review import daily_metrics, spill_budget
from sichuan_v5_dadu_da import list_days, read_day


def test_spill_conditioned_on_arrivals_and_space():
    args = dict(inflow=190.,start_m3=0.,upper_m3=0.,minimum_release=20.,bypass=10.,
                maximum_turbine=100.,spill=120.)
    assert spill_budget(**args)==(80.,40.)
    assert spill_budget(**dict(args,upper_m3=90000.))==(0.,120.)
    assert spill_budget(**dict(args,upper_m3=90000.,minimum_release=160.))==(50.,70.)
    with pytest.raises(ValueError):
        spill_budget(**dict(args,spill=79.))


PUBLISHED_PAIRS = (
    'dry_extreme_20261128T063656501745Z_2dbdfb8ba1914d02a6f1aba6d38d6648b7a983021fe48a2f728eae58ee5c981a',
    'dry_normal_20261128T063804195897Z_fce8e7d0f2e759490e283405bf30ac7ce92c27703077e0a58dc24a7c12fa6a1a',
    'wet_extreme_20261128T063738526015Z_f4c58d43b8e609b3c8fe5474d19d208cd1accb6d09b8d337915a119675854363',
    'wet_normal_20261128T063849497317Z_f335368ab4248ae7306ba529b13cfebb9796d344f9560b27891c66bdb73d98a3',
)

@pytest.mark.parametrize('name',PUBLISHED_PAIRS)
def test_paired_publication(name):
    folder=PROGRAM/'runs_sichuan_v5_consultation'
    result=read_day(folder,name)
    report=review_data(result,result['scenario'])
    ref=report['reference']
    assert ref['status']=='verified' and ref['declaration_sha256']==report['signature']
    assert ref['audit']['max_node_residual_mw']<1e-4
    assert ref['audit']['max_arrival_error_m3']<.05
    assert len(ref['quarters'])==96 and len(report['stations'])==19
    assert daily_metrics(ref['quarters'])==ref['totals']
    assert ref['totals']['generation_mwh']==ref['totals']['pump_mwh']==0
    for i,off in enumerate(ref['quarters']):
        on={k:v[i] for k,v in report['system'].items()}
        assert on['load']==pytest.approx(off['load'])
        assert on['fixed']==pytest.approx(off['fixed'])
        assert on['renewable']+on['pv_cut']==pytest.approx(off['renewable']+off['pv_cut'])
        for x in (on,off):
            assert sum(x[k] for k in ('hydro','thermal','storage','renewable','fixed','shed'))==pytest.approx(x['load']+x['pump']+x['excess'])
    water_total=0.
    for station in report['stations']:
        s=station['series'];water_total+=station['daily_water']
        for i,p in enumerate(s['sced']):
            assert sum(s['accepted'][i])==pytest.approx(p,abs=1e-4)
            assert s['floor'][i]>=-1e-5 and s['remaining'][i]>=-1e-5
            assert s['floor'][i]+s['remaining'][i]==pytest.approx(s['spill'][i],abs=1e-5)
            balance=s['local'][i]+s['arrival'][i]-s['turbine'][i]-s['bypass'][i]-s['spill'][i]
            assert 900*balance==pytest.approx(s['storage_m3'][i]-s['initial_m3'][i],abs=.05)
    assert water_total==pytest.approx(report['totals']['station_spill_m3'])
    weighted=sum(sum(v*row['raw_nodal_prices'][bus] for bus,v in row['demand'].items()) for row in result['da'])
    assert report['totals']['weighted_price']==pytest.approx(weighted/sum(report['system']['load']))


def test_older_archive_and_html_payload():
    folder=PROGRAM/'runs_sichuan_v5_consultation'
    result=read_day(folder,list_days(folder,'wet_low')[-1])
    report=review_data(result,'</script><test>')
    assert report['reference']['status']=='historical'
    html=standalone_report([report])
    assert '</script><test>' not in html and '\\u003c/script>' in html


def test_paired_csv_export():
    from io import BytesIO
    from zipfile import ZipFile
    import pandas as pd
    from sichuan_v5_dadu_da_view import export_day
    folder=PROGRAM/'runs_sichuan_v5_consultation'
    with ZipFile(BytesIO(export_day(str(folder),PUBLISHED_PAIRS[0]))) as archive:
        paired=pd.read_csv(archive.open('抽蓄投运停运对照.csv'))
        water=pd.read_csv(archive.open('各站弃水条件下界.csv'))
    assert len(paired)==96 and len(water)==19*96
    assert (paired['停运 抽水 MW']==0).all()
    assert (water['出清弃水 m³/s']-water['条件下界 m³/s']-water['超出下界 m³/s']).abs().max()<1e-5
