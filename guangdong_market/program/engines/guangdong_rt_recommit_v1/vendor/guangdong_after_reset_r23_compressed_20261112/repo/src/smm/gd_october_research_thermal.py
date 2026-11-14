"""Explicit research thermal hypotheses. Never generate operator confirmations.

The same endpoint/age/ramp constraints as external curves remain in force.
Fifteen-minute instantaneous hypotheses are linearly integrated into five-minute
means; binary connected states are generated from event intervals, not interpolated.
"""
from hashlib import sha256
from .gd_october_roll import _keys, _require, _text, _parse, _close, _number, FIVE
from .gd_october_thermal import _utc, _grid

ASSUMPTION_SCHEMA = 'gd.october.research_assumptions.v1'
POINT_SCHEMA = 'gd.october.research_assumption_curve_points.v1'


def validate_research_declaration(record, *, cutoff=None):
    d=record
    _keys(d['conversion'],('method','source','uses_future_actual'),'research conversion')
    _require(d['conversion']['method']=='research_piecewise_linear_points_exact_means_v1' and
             d['conversion']['uses_future_actual'] is False,'explicit research curve conversion required')
    _text(d['conversion']['source'],'research conversion source')
    a=d['assumption']
    _keys(a,('kind','authored_at','available_at','ledger_utf8','ledger_sha256','gas_applicability_hypothesis'), 'research assumption')
    _require(a['kind']=='research_assumption','research assumption identity required')
    _text(a['gas_applicability_hypothesis'],'explicit gas applicability hypothesis')
    _text(a['ledger_utf8'],'research assumption ledger bytes')
    _text(a['ledger_sha256'],'research assumption ledger SHA256')
    _require(sha256(a['ledger_utf8'].encode()).hexdigest()==a['ledger_sha256'],'assumption ledger hash mismatch')
    ledger=_parse(a['ledger_utf8'].encode())
    _require(ledger.get('schema_version')==ASSUMPTION_SCHEMA and ledger.get('source_kind')=='research_assumption',
             'research assumption ledger required')
    _require({'operator_confirmation','official_market_accepted'} <= set(ledger) and ledger.get('operator_confirmation') is None and ledger.get('official_market_accepted') is False,
             'research ledger cannot claim operator confirmation or official acceptance')
    authored,available=(_utc(a[k]) for k in ('authored_at','available_at'))
    _require(authored<=available,'research availability precedes authorship')
    if cutoff is not None:_require(available<=_utc(cutoff),'research curve unavailable at cutoff')
    points=_parse(d['typical_curve']['artifact_utf8'].encode())
    _keys(points,('schema_version','source_kind','unit_id','kind','points'),'research curve points')
    _require(points['schema_version']==POINT_SCHEMA and points['source_kind']=='research_assumption' and
             points['unit_id']==d['unit_id'] and points['kind']==d['kind'],'research curve identity mismatch')
    ps=points['points'];_require(type(ps) is list and len(ps)>=2,'research endpoints required')
    for row in ps:
        _keys(row,('at','mw'),'research endpoint');_number(row['mw'],'research endpoint MW',0)
    expected=[]
    for left,right in zip(ps,ps[1:]):
        start,end=_grid(left['at']),_grid(right['at'])
        _require(end-start==3*FIVE,'research source endpoints must be fifteen minutes apart')
        for j in range(3):
            p=left['mw']+(right['mw']-left['mw'])*j/3
            q=left['mw']+(right['mw']-left['mw'])*(j+1)/3
            expected.append((start+j*FIVE,start+(j+1)*FIVE,p,q,(p+q)/2))
    _require(len(expected)==len(d['steps']),'research resampling coverage mismatch')
    for s,(start,end,p,q,mean) in zip(d['steps'],expected):
        _require(_utc(s['start'])==start and _utc(s['end'])==end,'research resampling time mismatch')
        for key,value in [('start_mw',p),('end_mw',q),('mean_mw',mean)]:_close(s[key],value,'research exact linear integration')
    return available


def reject_research_promotion(text):
    """Check semantic JSON markers, not only unescaped byte spellings."""
    def visit(node):
        if isinstance(node,str):
            _require('research_assumption' not in node and 'research_thermal' not in node,
                     'research typical curve cannot become externally confirmed')
        elif isinstance(node,dict):
            for k,v in node.items():visit(k);visit(v)
        elif isinstance(node,list):
            for v in node:visit(v)
    if text.lstrip('\ufeff \n\r\t').startswith(('{','[')):
        visit(_parse(text.lstrip('\ufeff').encode()))
    else:visit(text)
