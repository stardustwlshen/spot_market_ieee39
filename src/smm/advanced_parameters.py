"""Whitelisted scene parameter edits; model equations and defaults stay unchanged."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
import hashlib
import io
import math
from numbers import Real
from pathlib import Path
import shutil
import tempfile

from ruamel.yaml import YAML

from .config import load_config
from .process_control import file_lock
from .unit_editor import _paths, _current_record, _json, scene_fingerprint
from .unit_edit_transaction import atomic_bytes, commit_inputs, scene_input_lock


@dataclass(frozen=True)
class Field:
    key: str
    group: str
    label: str
    help: str
    kind: str = 'number'
    default: object = None
    minimum: float | None = None
    maximum: float | None = None
    options: tuple = ()
    optional: bool = False


def get_value(cfg, key, default=None):
    namespace, *parts = key.split('.')
    value = cfg.base if namespace == 'base' else cfg.mode
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return copy.deepcopy(default)
        value = value[part]
    return copy.deepcopy(value)


def fields(cfg):
    """Only real, supported consumers are editable; absent optional keys stay absent."""
    result = []
    def add(key, group, label, help, **kwargs):
        result.append(Field(key, group, label, help, **kwargs))
    add('base.date','基础场景','运行日期','运行日期标签及报价归档日期；不会自动重置物理初态或改价。',kind='date')
    add('base.season','基础场景','水期','采用对应水期的已有来水和库容参数；当前报价保留。',kind='choice',options=('wet','dry'))
    add('base.load_scale','基础场景','区内负荷倍率','仅缩放合成负荷；上传的显式负荷列优先。',minimum=0.)
    add('base.seed','基础场景','随机种子','合成曲线的非负整数种子；同参数同种子可复现，不修改上传曲线。',kind='integer',minimum=0.)
    from .forecast import PHI, SIGMA
    for typ, cn in (('wind','风电'),('pv','光伏'),('load','负荷')):
        for stage, label in (('da','日前'),('id','日内'),('rt','实时')):
            add(f'base.forecast.sigma.{typ}.{stage}','预测误差',cn+label+'误差标准差',
                '截断前误差比例；风光按装机、负荷按当时负荷。新版风光须日前≥日内≥实时；上传列优先。',default=SIGMA[typ][stage],minimum=0.)
        add(f'base.forecast.phi.{typ}','预测误差',cn+'误差相关系数',
            '控制合成预测误差的时间持续性；绝对值小于1，新版风光还要求非负。上传列优先。',default=PHI[typ],minimum=-1.,maximum=1.)
    add('base.line_capacity_multiplier','线路限额','全网线路倍率','有效限额=原始限额×逐线倍率×全网倍率；不改变网络拓扑。',default=1.,minimum=0.)
    from .network import build_network
    for branch in build_network(cfg).branches.branch_id:
        add('base.line_scale.'+branch,'线路限额',branch+' 限额倍率','仅作用于此支路；1表示保留原始限额，再乘全网倍率。',default=1.,minimum=0.)
    for suffix,label,kind,default,low,high in (
        ('mip_rel_gap','日前相对间隙','number',.01,0.,1.),
        ('time_limit_s','日前求解预算（秒）','number',300.,0.,None),
        ('threads','求解线程数','integer',4,1.,2147483647.),
        ('log','显示求解器日志','bool',False,None,None)):
        add('base.solver.'+suffix,'求解设置',label,'控制求解预算/精度或日志；不保证达到最优或约束完全可行。高阶透传覆盖时禁止误导性修改。',kind=kind,default=default,minimum=low,maximum=high)
    # Rule fields expose existing consumers only. Old absent policy blocks are
    # not materialized simply by opening or saving another field.
    def mode(path,group,label,help,**kwargs):
        key='mode.'+path
        node=cfg.mode
        present=True
        for part in path.split('.'):
            if not isinstance(node,dict) or part not in node:
                present=False;break
            node=node[part]
        if present or kwargs.get('optional',False):
            add(key,group,label,help,**kwargs)
    for key,label in (('bid_min','全局报价下限'),('bid_max','全局报价上限'),
                      ('clear_min','出清结算下限'),('clear_max','出清结算上限')):
        mode('price_limits.'+key,'价格边界',label+'（元/MWh）',
             '报价边界仅约束申报价，默认/分类型覆盖优先；出清结算边界仅裁剪lmp_clamped与结算价，原始LMP可越界，不是调度硬约束。')
    for scope,label in [('default','全部类型默认覆盖')]+[
            ('by_type.'+typ,cn+'覆盖') for typ,cn in (
                ('coal','煤电'),('gas_ccgt','联合循环'),('gas_gt','燃机'),('wind','风电'),
                ('pv','光伏'),('hydro','水电'),('storage','储能'),('pumped','抽蓄'))]:
        for side,cn in (('min','下限'),('max','上限')):
            mode('bid_rules.'+scope+'.price_'+side,'报价限价覆盖',label+cn+'（元/MWh）',
                '只覆盖报价限价；优先级为全局→默认覆盖→分类型覆盖。取消设置恢复继承，不改出清结算限价。',optional=True)
    for key,label in (('rho_L','上备用负荷比例'),('rho_RE','上备用新能源比例'),
                      ('rho_L_dn','下备用负荷比例'),('rho_RE_dn','下备用新能源比例')):
        mode('reserve.'+key,'备用',label,'与相应负荷或新能源预测相乘形成需求；下备用关闭时下备用比例不使用。',minimum=0.)
    for key,label,help in (
        ('include_ctg','计入最大事故缺额','加入既有最大单机/配置事故缺额项；不等于完整N−1安全校核。'),
        ('storage_reserve','储能抽蓄参与备用','默认仅上备用；开启下备用采购后也控制下备用。抽蓄限发电工况，按既有库存筛查。'),
        ('down_enabled','采购下备用','关闭时下备用需求、供给与缺额为0；相关比例保留但不使用。')):
        mode('reserve.'+key,'备用',label,help,kind='bool')
    mode('reserve.mu_RE','备用','新能源备用置信因子','可用新能源余量计入上备用的比例，0到1。',minimum=0.,maximum=1.)
    mode('reserve.T_dur_h','备用','备用持续时长（小时）','按时段末库存筛查可交付备用；不是完整基准加备用全过程持续保证。',minimum=0.)
    mode('reserve.providers','备用','常规备用类型','只选择当前支持的常规资源类型；电池/抽蓄由单独开关控制。',kind='multi',options=('coal','gas_ccgt','gas_gt','hydro'))
    for key,cn in (('balance','供需平衡'),('line','线路越限'),('reserve','备用缺额')):
        mode('penalties.'+key,'松弛罚因子',cn+'正式罚因子','正式优化目标的松弛罚因子；不是电价，不能保证松弛为零。',minimum=0.)
    mode('auxiliary_pricing.enabled','辅助算价','启用辅助算价','独立辅助模型给出价格，实际执行量仍来自正式调度。',kind='bool')
    for key,cn in (('balance','供需平衡'),('line','线路越限'),('reserve','备用缺额')):
        mode('auxiliary_pricing.penalties_yuan_per_mwh.'+key,'辅助算价',cn+'辅助罚价（元/MWh）','仅启用辅助算价时生效；独立于正式调度罚因子。',minimum=0.)
    for key,cn in (('relative_to_dispatch','相对正式出力带比例'),('minimum_mw','最小出力带（MW）')):
        mode('auxiliary_pricing.dispatch_band.'+key,'辅助算价',cn,'仅辅助算价使用的正式出力附近允许调整范围，不改变执行出力。',minimum=0.)
    mode('realtime.lookahead_h','实时设置','实时前瞻（小时）','每轮求解的未来长度，只执行首段；须为步长整数倍，偶数整点锚定要求至少2小时。',minimum=0.)
    mode('realtime.first_period_actual','实时设置','首段使用实际值','开启后实时首段负荷/风光采用实际曲线；其余前瞻仍用预测。',kind='bool')
    for stage,group in (('realtime','实时设置'),('intraday','日内设置')):
        mode(stage+'.mip_rel_gap',group,group[:2]+'相对间隙','相应阶段MIP的相对精度目标；达到时限仍可能未达目标。',minimum=0.,maximum=1.)
        mode(stage+'.time_limit_s',group,group[:2]+'求解预算（秒）','相应阶段求解预算；不会被日前预算替代。',minimum=0.)
    mode('realtime.hydro','实时状态策略','水电实时策略','价值法/窗口末跟踪/固定日前/自由SCED；窗口末软跟踪须同时选择储能抽蓄锚定日前。',kind='choice',options=('value','window_end_da','fixed_da','sced'))
    mode('realtime.storage.mode','实时状态策略','储能抽蓄实时策略','价值法、锚定日前或固定日前；不改变充放互斥。',kind='choice',options=('value','sced_anchor','fixed_da'))
    mode('realtime.storage.anchor','实时状态策略','日前锚点类型','实时采用锚定日前时使用；日内开启且储能策略非价值法时，日内也使用此锚点。',kind='choice',options=('two_hour_eq','hourly_eq','window_end_pct'))
    mode('realtime.storage.x','实时状态策略','窗口末比例带','仅实时锚定的非日末窗口比例带使用；0.05表示±5%，日内窗口到日末时固定收为0。',minimum=0.,maximum=1.)
    mode('realtime.storage.anchor_penalty','实时状态策略','软锚点罚因子','日内或未采用窗口末软跟踪的实时软锚点使用；两小时硬锚点不使用此值。',minimum=0.)
    for key,label in (('band_fraction','储能抽蓄偏差带比例'),('hydro_band_fraction','水电偏差带比例')):
        mode('realtime.value_method.'+key,'价值法',label,'仅相应资源采用价值法时生效；储能按额定能量、库容按可调范围折算。',minimum=0.,maximum=1.)
    mode('realtime.value_method.fallback_penalty','价值法','不可行回退罚因子','仅明确硬不可行时用于价值法偏差带或两小时硬锚点回退；窗口末软跟踪存在时，其罚价优先。',minimum=0.)
    mode('realtime.value_method.hydro_lookahead','价值法','水电参考续程','仅水电价值法+软终态的非日末窗口使用；参考续程是软偏好，不执行后缀。',kind='choice',options=('off','reference_continuation'))
    mode('realtime.value_method.terminal_policy.mode','价值法','实时日末目标','价值法日末库存目标：硬约束或软偏差成本；储能/抽蓄双侧偏差，水电仅不足。',kind='choice',options=('hard','soft'))
    mode('realtime.value_method.terminal_policy.penalty_yuan_per_discharge_mwh','价值法','日末偏差罚价（元/可放电MWh）','仅价值法+软终态：储能/抽蓄双侧偏差，水电仅不足；优化成本，不是结算付款。',minimum=0.)
    mode('realtime.tracking_policy.penalty_yuan_per_discharge_mwh','实时状态策略','窗口末软跟踪罚价（元/可放电MWh）','水电窗口末软跟踪启用时，水电及储能/抽蓄软锚点均按此罚价折算；覆盖普通锚点及回退锚点罚因子。',minimum=0.)
    mode('intraday.enabled','日内设置','启用日内滚动','启用现有日内优化路径；关闭时其他日内设置不参与运行。',kind='bool')
    mode('intraday.interval_h','日内设置','日内重算间隔（小时）','仅日内启用时生效，须为时间步长的正整数倍。',minimum=0.)
    mode('intraday.lead_issue_periods','日内设置','日内提前发布时段数','仅日内启用时生效；单位是场景时段数，不是小时。',kind='integer',minimum=0.)
    mode('realtime.handoff_source','日内交接','状态参考来源','日内可用且选择日内时采用最新日内参考；否则按既有规则回退日前。',kind='choice',options=('intraday','day_ahead'))
    for key,cn in (('commitment','火电启停'),('storage','电池库存'),('pumped','抽蓄库容'),('hydro','水电库容')):
        mode('realtime.handoff.'+key,'日内交接',cn+'接受日内交接','仅日内启用且参考源选择日内时使用；库存还须对应资源采用价值法。火电关闭保留GT实时自由。',kind='bool')
    mode('realtime.intraday_commit_rt','日内交接','快速机组实时启停','日内交接启用时，继承日内启停或允许实时再次优化。',kind='choice',options=('inherit','free'))
    return result


def _validate_value(spec, value):
    if value is None and spec.optional:
        return
    if spec.kind == 'bool':
        if type(value) is not bool:
            raise ValueError(spec.label+'须为布尔值')
    elif spec.kind == 'choice':
        if value not in spec.options:
            raise ValueError(spec.label+'不是受支持的选项')
    elif spec.kind == 'multi':
        if not isinstance(value,list) or len(value)!=len(set(value)) or any(x not in spec.options for x in value):
            raise ValueError(spec.label+'须是不重复的受支持类型')
    elif spec.kind == 'date':
        if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:
            raise ValueError('运行日期须为YYYY-MM-DD')
    else:
        if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value):
            raise ValueError(spec.label+'须为有限数值')
        if spec.kind == 'integer' and int(value)!=value:
            raise ValueError(spec.label+'须为整数')
        if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
            raise ValueError(spec.label+'超出允许范围')
        positive = (spec.key.endswith(('time_limit_s','load_scale','line_capacity_multiplier','T_dur_h','interval_h','lookahead_h','anchor_penalty','fallback_penalty','penalty_yuan_per_discharge_mwh'))
                    or '.line_scale.' in spec.key or spec.key.startswith('mode.penalties.'))
        if positive and value <= 0:
            raise ValueError(spec.label+'须为正数')
        if '.forecast.phi.' in spec.key and abs(value)>=1:
            raise ValueError(spec.label+'须满足绝对值小于1')


def _update(document, parts, value):
    node = document
    for part in parts[:-1]:
        if part not in node:
            node[part] = {}
        if not isinstance(node[part],dict):
            raise ValueError('配置路径不是对象：'+'.'.join(parts))
        node = node[part]
    if value is None:
        node.pop(parts[-1],None)
    else:
        node[parts[-1]] = value


def _validate_consumption(cfg, changes):
    from .input_validation import validate_clearing_limits
    validate_clearing_limits(cfg.mode['price_limits'])
    rt=cfg.mode.get('realtime',{})
    tracking=rt.get('tracking_policy')
    if (rt.get('hydro','value')=='window_end_da' and tracking is not None
            and tracking.get('mode')!='legacy'
            and rt.get('storage',{}).get('mode','value')!='sced_anchor'):
        raise ValueError('水电窗口末软跟踪要求储能抽蓄选择锚定日前策略；请明确调整两项，未自动修改。')
    limits = cfg.mode['price_limits']
    if limits['bid_min'] > limits['bid_max']:
        raise ValueError('全局报价下限不能大于上限')
    from .bids import bid_rules_for_type
    for typ in ('coal','gas_ccgt','gas_gt','wind','pv','hydro','storage','pumped'):
        bid_rules_for_type(cfg,typ)
    extra = cfg.base.get('solver',{}).get('highs_extra') or {}
    for suffix,option in (('mip_rel_gap','mip_rel_gap'),('time_limit_s','time_limit'),('threads','threads'),('log','output_flag')):
        if any(key.endswith('solver.'+suffix) or key.endswith('.'+suffix) and key.startswith('mode.') for key in changes) and option in extra:
            raise ValueError(f'HiGHS高级透传 {option} 覆盖此设置；请先处理原透传配置，未保存。')


def prepare_parameter_edit(root, changes, *, expected=None):
    """Detached validation and derived evidence. No input is persisted here."""
    root=Path(root)
    with file_lock(root/'.cost_archive.lock',blocking=True),file_lock(root/'current_quote.lock',blocking=True),scene_input_lock(root):
        before=scene_fingerprint(root)
        if expected is not None and expected!=before:
            raise RuntimeError('场景已被另一页面修改，请取消后重新打开；未覆盖任何输入。')
        old=load_config(root=root)
        registry={f.key:f for f in fields(old)}
        if not isinstance(changes,dict) or set(changes)-set(registry):
            raise ValueError('包含未开放的高级参数；未保存')
        for key,value in changes.items():
            _validate_value(registry[key],value)
        changes={k:copy.deepcopy(v) for k,v in changes.items()
                 if v!=get_value(old,k,registry[k].default)}
        with tempfile.TemporaryDirectory(prefix='advanced-parameters-') as tmp:
            stage=Path(tmp)
            for path in _paths(root):
                target=stage/path.relative_to(root)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(path,target)
            # Load current quotes before a date/price change; never regenerate them.
            record=_current_record(stage,load_config(root=stage))
            yaml=YAML();yaml.preserve_quotes=True
            documents={name:yaml.load((stage/'config'/name).read_text(encoding='utf-8')) for name in ('base.yaml','modes.yaml')}
            touched=set()
            for key,value in changes.items():
                namespace,*parts=key.split('.')
                name='base.yaml' if namespace=='base' else 'modes.yaml'
                if namespace=='mode':parts=[old.rule_set,*parts]
                _update(documents[name],parts,value)
                touched.add(name)
            for name in touched:
                stream=io.StringIO();yaml.dump(documents[name],stream)
                (stage/'config'/name).write_text(stream.getvalue(),encoding='utf-8')
            candidate=load_config(root=stage)
            _validate_consumption(candidate,changes)
            from .bids import validate_bids,raise_on_errors
            issues=validate_bids(candidate,record['draft'],require_complete=True)
            raise_on_errors(issues)
            from .forecast import generate_profiles,apply_profile_overrides
            from .network import build_network
            from .case import build_da_case
            from .input_validation import validate_case_inputs
            profiles=apply_profile_overrides(candidate,generate_profiles(candidate))
            net=build_network(candidate)
            case=build_da_case(candidate,net,profiles,record['draft'])
            validate_case_inputs(case)
            replacements={'config/'+name:(stage/'config'/name).read_bytes() for name in touched}
            # A scenario without a current quote must retain the pre-edit quotes,
            # especially on date changes. Existing records are byte-preserved.
            if changes and not (root/'current_quote.json').exists():
                replacements['current_quote.json']=_json(record)
            if scene_fingerprint(root)!=before:
                raise RuntimeError('检查期间输入已变化，请重新检查；未保存')
            return dict(root=str(root),before=before,changes=changes,replacements=replacements,
                        effective=effective_rows(candidate),prices=price_rows(candidate),
                        lines=net.branches[['branch_id','rate_orig_mw','rate_mw']].to_dict('records'),
                        load_peak=float(profiles.load_da.max()),
                        warnings=[str(x) for x in issues if x.level!='error']+[
                            registry[key].label+'已保存为待用值：'+inactive_reason(candidate,key)
                            for key in changes if inactive_reason(candidate,key)])


def apply_parameter_edit(plan):
    root=Path(plan['root'])
    fresh=prepare_parameter_edit(root,plan['changes'],expected=plan['before'])
    with file_lock(root/'.cost_archive.lock',blocking=True),file_lock(root/'current_quote.lock',blocking=True),scene_input_lock(root):
        if scene_fingerprint(root)!=fresh['before']:
            raise RuntimeError('场景、报价或成本已变化，请重新检查；未保存')
        if not fresh['replacements']:
            return root
        versions=root/'parameter_edit_versions'
        if versions.is_symlink():
            raise ValueError('参数备份目录不能是符号链接')
        versions.mkdir(exist_ok=True)
        import base64
        previous={relative:base64.b64encode((root/relative).read_bytes()).decode() if (root/relative).exists() else None
                  for relative in fresh['replacements']}
        content=_json(dict(schema=1,before=fresh['before'],files_base64=previous))
        identity=hashlib.sha256(_json(dict(before=fresh['before'],paths=sorted(previous)))).hexdigest()
        backup=versions/(identity+'.json')
        if backup.exists() and backup.read_bytes()!=content:
            raise ValueError('参数备份不一致，未覆盖')
        if not backup.exists():atomic_bytes(backup,content)
        commit_inputs(root,fresh['replacements'])
    return root


def inactive_reason(cfg, key):
    """Concrete activation checks; saving a dormant parameter is never called effective."""
    rt=cfg.mode.get('realtime',{})
    storage=rt.get('storage',{}).get('mode','value')
    hydro=rt.get('hydro','value')
    tracking_active=(hydro=='window_end_da' and (rt.get('tracking_policy') or {}).get('mode')=='soft_deliverable_energy')
    if key.startswith('mode.auxiliary_pricing.') and key!='mode.auxiliary_pricing.enabled':
        if not cfg.mode.get('auxiliary_pricing',{}).get('enabled',False):return '辅助算价未开启'
    if key in ('mode.reserve.rho_L_dn','mode.reserve.rho_RE_dn'):
        if not cfg.mode['reserve'].get('down_enabled',True):return '下备用采购未开启'
    if key.startswith('mode.intraday.') and key!='mode.intraday.enabled':
        if not cfg.mode.get('intraday',{}).get('enabled',False):return '日内滚动未开启'
    if key.startswith('mode.realtime.handoff') or key=='mode.realtime.intraday_commit_rt':
        if not cfg.mode.get('intraday',{}).get('enabled',False):return '日内滚动未开启'
        if key!='mode.realtime.handoff_source' and rt.get('handoff_source','intraday')!='intraday':return '参考源未选择日内'
        if key in ('mode.realtime.handoff.storage','mode.realtime.handoff.pumped') and storage!='value':return '库存交接仅用于储能抽蓄价值法'
        if key=='mode.realtime.handoff.hydro' and hydro!='value':return '库容交接仅用于水电价值法'
        if key=='mode.realtime.intraday_commit_rt' and not rt.get('handoff',{}).get('commitment',True):return '火电日内交接未开启'
    if key.startswith('mode.realtime.storage.') and not key.endswith('.mode'):
        id_anchor=cfg.mode.get('intraday',{}).get('enabled',False) and storage!='value'
        if storage!='sced_anchor' and not id_anchor:return '实时未锚定且日内未采用锚点'
        anchor=rt.get('storage',{}).get('anchor')
        if key.endswith('.x') and (storage!='sced_anchor' or anchor!='window_end_pct'):return '仅实时窗口末比例带使用，日内日末比例固定为0'
        if key.endswith('.anchor_penalty'):
            if anchor=='two_hour_eq':return '两小时硬锚点不使用普通软锚点罚因子'
            if tracking_active and not id_anchor:return '窗口末软跟踪罚价覆盖实时软锚点系数'
    if key.startswith('mode.realtime.tracking_policy.') and hydro!='window_end_da':return '水电未采用窗口末跟踪'
    if key.startswith('mode.realtime.value_method.'):
        hard_anchor=(storage=='sced_anchor' and rt.get('storage',{}).get('anchor')=='two_hour_eq')
        if key.endswith('.fallback_penalty') and hard_anchor:
            return '窗口末软跟踪罚价覆盖回退软锚点系数' if tracking_active else None
        if storage!='value' and hydro!='value':return '未采用价值法'
        if key.endswith('.band_fraction') and storage!='value':return '储能抽蓄未采用价值法'
        if (key.endswith('.hydro_band_fraction') or key.endswith('.hydro_lookahead')) and hydro!='value':return '水电未采用价值法'
        if key.endswith('.hydro_lookahead') and rt.get('value_method',{}).get('terminal_policy',{}).get('mode','hard')!='soft':return '水电参考续程仅用于软终态'
        if '.terminal_policy.penalty_' in key and rt.get('value_method',{}).get('terminal_policy',{}).get('mode','hard')!='soft':return '未采用软终态'
    return None


def effective_rows(cfg):
    from .scuc import _solver
    from .rolling import _sub_cfg
    rows=[]
    def add(label,value,source):rows.append({'参数':label,'有效值':str(value),'来源':source})
    add('时间栅格',f'{cfg.T}段 × {cfg.dt*60:g}分钟','base；本编辑器只读')
    extra=cfg.base.get('solver',{}).get('highs_extra') or {}
    for stage,cn in (('da','日前'),('realtime','实时'),('intraday','日内')):
        source=cfg.base['solver'] if stage=='da' else cfg.mode.get(stage,{})
        fallback=({'mip_rel_gap':.005,'time_limit_s':60} if stage=='intraday' else
                  {'mip_rel_gap':.01,'time_limit_s':30} if stage=='realtime' else
                  {'mip_rel_gap':.01,'time_limit_s':300})
        budget=source.get('time_limit_s',fallback['time_limit_s'])
        current=cfg if stage=='da' else _sub_cfg(cfg,time_limit_s=budget,
            mip_rel_gap=source.get('mip_rel_gap',fallback['mip_rel_gap']))
        mip=_solver(current,mip=True).highs_options
        lp=_solver(current,mip=False).highs_options
        origin='base.solver' if stage=='da' else 'mode.'+stage
        disabled='；未启用' if stage=='intraday' and not source.get('enabled',False) else ''
        for option,label in (('mip_rel_gap','MIP相对间隙'),('time_limit','MIP时限选项（秒）'),
                             ('threads','MIP线程数'),('output_flag','MIP日志开关')):
            value=mip[option]
            used='HiGHS透传覆盖'+('，阶段目标仍按原配置报告' if option=='mip_rel_gap' and stage!='da' else '') if option in extra else ('base.solver' if option in ('threads','output_flag') else origin)
            if stage=='realtime' and option=='time_limit':
                used+='；每次还受共享剩余预算限制'
            add(cn+label,value,used+disabled)
        add(cn+'LP单次时限（秒）',lp['time_limit'],origin+'；LP不使用MIP透传'+disabled)
        if stage=='realtime':add('实时每窗口共享总预算（秒）',budget,origin+'；MIP/LP/回退共用，建模与导出时间不计')
    add('负荷倍率',cfg.base['load_scale'],'base；上传负荷列优先')
    add('水期',cfg.base['season'],'base；对应季节水文')
    add('出清/结算限价',f"{cfg.mode['price_limits']['clear_min']} ～ {cfg.mode['price_limits']['clear_max']}",'mode.price_limits；裁剪结算价，原始LMP可越界')
    return rows


def price_rows(cfg):
    from .bids import bid_rules_for_type
    rows=[]
    for typ in ('coal','gas_ccgt','gas_gt','wind','pv','hydro','storage','pumped'):
        rule=bid_rules_for_type(cfg,typ)
        row={'类型':typ}
        for suffix,label in (('min','下限'),('max','上限')):
            field='price_'+suffix
            override=cfg.mode.get('bid_rules',{})
            source=('分类型覆盖' if field in override.get('by_type',{}).get(typ,{}) else
                    '默认覆盖' if field in override.get('default',{}) else '全局报价限价')
            row['报价'+label]=rule[field];row[label+'来源']=source
        rows.append(row)
    return rows


def readonly_rows(cfg):
    """Concise inventory of fixed, separately managed or historical settings."""
    specs=[
        ('base.periods','场景总时段数','与步长共同定义运行日；本表不单独改变时间栅格。'),
        ('base.dt_h','时段长度（小时）','须与实时、日内和结算时间口径一致，本表只读。'),
        ('base.rule_set','规则集','本表只修改当前规则集，不切换整套规则。'),
        ('base.default_bid_model','新报价生成方式','仅用于生成报价；当前申报不被默认生成方式覆盖。'),
        ('base.forecast.model','合成曲线模型','使用页面已有预测模型切换入口；上传列仍优先。'),
        ('base.tie_line_mode','区外来电口径','当前按固定计划供给；旧转负荷场景使用专用恢复入口。'),
        ('base.solver.name','求解器','当前执行器固定使用HiGHS；不提供无对应执行器的名称编辑。'),
        ('base.solver.highs_extra','HiGHS透传选项','保留已存透传；实际覆盖值见运行生效参数，不开放任意键输入。'),
        ('base.solver.storage_binary','储能互斥整数变量','false为独立日前/LP研究对照，不能作为正常实时滚动配置。'),
        ('base.solver.tighten','启停收紧实验','会改变约束语义，本轮不作为普通调参项开放。'),
        ('base.solver.warm_start','启发式热启动','保留既有工程实验设置，本轮不新增切换路径。'),
        ('base.solver.commit_step_periods','火电启停决策步长','改变启停决策粒度，本轮只读。'),
        ('mode.pumped.mode_step_periods','抽蓄台数决策步长','改变聚合抽发台数决策粒度，本轮只读。'),
        ('mode.pricing','定价结构','当前实现节点边际电价LMP，不支持任意名称切换其他定价模型。'),
        ('mode.startup_cost_mode','启动报价口径','决定单次或分状态报价；涉及现有申报结构，不能作为孤立数字修改。'),
        ('mode.startup_states','历史启动状态阈值','仅分状态启动报价模式使用；单次启动模式不消费这些阈值。'),
        ('mode.segments','报价段数结构','逐类型还受报价profile和覆盖规则影响，本轮只开放价格上下限。'),
        ('mode.price_step','通用报价步长','逐类型有效值可能被profile/default/by_type覆盖；报价区显示实际规则。'),
        ('mode.price_strict_increasing','通用严格单调要求','逐类型报价规则优先，不把全局声明当所有资源有效规则。'),
        ('mode.min_seg_frac','通用最小报价段比例','逐类型报价规则可覆盖，报价区显示实际口径。'),
        ('mode.max_adjacent_step','通用相邻段价差','逐类型报价规则可覆盖，报价区显示实际口径。'),
        ('mode.bid_rules.profile','报价形状参考','保留当前已支持的形状规则；仅限价覆盖在上方单独编辑。'),
        ('mode.demand_bid','负荷申报声明','当前需求为外生固定负荷，没有需求报价求解路径。'),
        ('mode.tie_mode','联络线计划声明','外部计划边界，不作为任意可调机组。'),
        ('mode.storage_end_soc.da','日前储能日末口径','当前使用申报硬终态，未申报回初态；申报值在报价区修改。'),
        ('mode.realtime.interval_min','实时执行间隔（分钟）','与场景dt_h一致，不能单独修改。'),
        ('mode.realtime.binding_periods','每轮执行段数','当前滚动仅执行首段，其他段为前瞻。'),
        ('mode.realtime.boundary_lead_min','边界时序说明（分钟）','方案时序声明，不作为独立物理/预测延时编辑。'),
        ('mode.intraday.window','日内窗口结构','当前日内优化到运行日末，不支持任意窗口名称。'),
        ('mode.intraday.granularity_min','日内时段声明（分钟）','实际模型统一使用场景时间栅格，不单独改声明值。'),
        ('mode.intraday.lock_types','日内锁定类型','启停分类必须与实时和已建模资源一致，本轮只读。'),
        ('mode.intraday.free_types','日内自由类型','启停分类结构，本轮只读。'),
        ('mode.realtime.lock_types','实时锁定类型','启停分类必须与日内和已建模资源一致，本轮只读。'),
        ('mode.realtime.free_types','实时自由类型','启停分类结构，本轮只读。'),
        ('mode.pumped.rules','抽蓄市场口径','当前使用与储能一致的报价和结算路径。'),
        ('mode.settlement.scheme','结算结构','当前为日前加实时偏差的两级结算，不支持任意名称切换。'),
        ('mode.settlement.interval_min','结算间隔声明（分钟）','实际计算统一使用cfg.dt，不能单改声明值制造新结算粒度。'),
    ]
    rows=[]
    for key,label,reason in specs:
        value=get_value(cfg,key,None)
        if value is not None:rows.append({'参数':label,'保存值':str(value),'只读说明':reason})
    regulation=cfg.mode.get('regulation')
    if regulation is not None:
        rows.append({'参数':'历史调频实验','保存值':'启用' if regulation.get('enabled',False) else '关闭',
                     '只读说明':'当前网页仅支持电能量模拟；历史调频配置保留，不开放编辑。'})
    return rows
