from pathlib import Path
import json,os,sys
from shutil import copy2
sys.path.insert(0,str(Path('app').resolve()))
from sichuan_v5_dadu_case import build_case
from sichuan_v5_dadu_run import default_declaration,freeze_declaration
from sichuan_v5_dadu_operations import with_operations
from sichuan_v5_dadu_da import run_day,read_day
root = Path('runs_sichuan_v5_consultation')
out = Path('qa-results')
out.mkdir(exist_ok=True)
stats = []
for season,template in [('wet','wet_low'),('dry','dry_peak')]:
    case,meta = build_case(template,39)
    base = default_declaration(case,meta)
    entries = [
        dict(station='D10',kind='fixed',first=72,last=75,value=1200.),
        dict(station='D10',kind='release',first=72,last=75,value=1000.),
        dict(station='D10',kind='level_min',first=72,last=75,value=800.),
        dict(station='D11',kind='island',first=48,last=51,value=80.)]
    company = with_operations(base,entries)
    path = freeze_declaration(root,company)
    copy2(path,out/(season+'-filing.json'))
    (path.parent/('latest-'+base['declaration_sha256']+'.txt')).write_text(company['declaration_sha256'],encoding='ascii')
    for error in ['normal','extreme']:
        scenario = season+'_'+error
        name,_ = run_day(root,scenario=scenario,declaration_sha256=company['declaration_sha256'])
        result = read_day(root,name)
        for t in range(72,76):
            for stage in ['da','scuc']:
                assert abs(result[stage][t]['power']['D10']-1200.) < 1e-4
            assert result['da'][t]['hydro']['D10']['release_m3s']>=999.99999
        for t in range(48,52):
            for stage in ['da','scuc']:
                assert abs(result[stage][t]['power']['D11']) < 1e-4
        a = result['audit']
        assert abs(a['island_energy_mwh']-80.) < 1e-6
        summary = dict(scenario=scenario,quarter_count=len(result['da']),fixed_mw=result['da'][72]['power']['D10'],
            island_mwh=a['island_energy_mwh'],island_turbine_m3s=result['da'][48]['hydro']['D11']['turbine_m3s'],
            max_balance_mw=a['max_node_residual_mw'],max_overload_mw=a['max_line_overload_mw'],max_water_error_m3=a['max_storage_error_m3'],
            max_slack_mw=a['max_slack_mw'],reference_at_pubugou=result['da'][72]['raw_nodal_prices']['33'])
        print(json.dumps(summary),flush=True)
        stats.append(summary)
        copy2(root/'dadu19_da'/(name+'.json.gz'),out/(name+'.json.gz'))
(out/'summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
    print('|场景|瀑布沟 18:00 并网 MW|深溪沟孤网 MWh|深溪沟 12:00 过机 m³/s|主网平衡最大残差 MW|线路越限 MW|最大库容误差 m³|最大可靠性松弛 MW|',file=f)
    print('|---|---:|---:|---:|---:|---:|---:|---:|',file=f)
    for s in stats:
        print('|{scenario}|{fixed_mw:.1f}|{island_mwh:.1f}|{island_turbine_m3s:.2f}|{max_balance_mw:.2g}|{max_overload_mw:.2g}|{max_water_error_m3:.2g}|{max_slack_mw:.2g}|'.format(**s),file=f)
