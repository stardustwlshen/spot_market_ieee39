"""Descriptions of actual input consumption, not promises of dispatch changes."""

HELP = {
    'enabled': '启用后进入下一次模拟；停用机组保留技术档案，不进入出清。',
    'bus': '接入的IEEE39节点；决定该机组的网络注入位置与节点电价。',
    'pmax': '机组功率上限。已有报价容量不自动缩放，保存时一并校验。',
    'pmin': '火电在线时的最小技术出力；停机时出力为零。',
    'cap': '风光装机容量，限制可用出力并用于合成曲线；上传曲线优先。',
    'profile': '合成风光日形状。已上传该机组曲线时使用上传值。',
    'ramp': '相邻时段出力变化限额，以MW/min计；水电按单机率乘在线台数。',
    'ut': '火电启动后至少连续在线的小时数；跨日初态已持续时间也参与计算。',
    'dt': '火电停机后至少连续停机的小时数；跨日初态已持续时间也参与计算。',
    'noload': '生成报价时使用的空载缺省值；已有报价的空载报价优先。独立成本档案另用于利润核算。',
    'cold': '冷态启动档案缺省值；已有启动报价优先，单次启动报价模式不按冷热态计算。',
    'warm': '温态启动档案缺省值；已有启动报价优先，单次启动报价模式不按冷热态计算。',
    'hot': '热态启动档案缺省值；新建传统单次启动报价时可作为初值，已有启动报价不自动变化。',
    'very_hot': '极热态历史档案值；当前统一启动报价模式不使用此状态。',
    'online': '模拟开始前火电是否在线；初始功率须与在线状态一致。',
    'p0': '模拟开始前火电实际出力，参与首段爬坡和开停状态校验。',
    'hours': '模拟开始前当前开机或停机状态已持续的小时数。',
    'regulation': '水库式采用库容与日电量约束；径流式采用来水和调节池，日末回到初始库容。',
    'n_units': '聚合电站包含的同型机组台数；须为正整数。',
    'unit_pmax': '每台水电机组最大发电功率；全站上限为台数乘单机上限。',
    'zones': '每行是单台水轮机允许连续运行的功率区间；区间之间的振动区不可运行。',
    'water_rate': '每发1 MW所需流量，m³/s/MW；用于功率、水量和库容平衡换算。',
    'init_units_on': '模拟开始前在线水轮机台数；和初始单机功率共同确定全站初始出力。',
    'init_p_unit': '模拟开始前每台在线机组的功率；总初始功率=在线台数×此值。',
    'qin': '旧模型按该水期使用恒定天然来水；causal_physical_v3把它作为显式场景基准，实际与已发布预测逐时变化。受控上游滞后出库另计，不重复加入天然来水。',
    'v0': '模拟开始时的水库水量，单位万m³。实时滚动后改用真实执行末库容。',
    'vmin': '水库式电站每时段的物理库容下限，单位万m³。',
    'vmax': '水库式电站每时段的物理库容上限，单位万m³。',
    'v_end': '水库式电站日前日末库容要求；实时如何承接取决于已保存的终态策略。',
    'qeco': '最小生态下泄流量，发电用水与弃水合计满足，单位m³/s。',
    'e_day': '水库式电站全天发电量上限；滚动只扣已真实执行电量。',
    'e_day_min': '水库式电站全天发电量下限；不设置表示无此要求，不保证未来全网一定可行。',
    'qout_max': '发电流量与弃水合计的出库流量上限；不设置表示无此上限。',
    'qout_max_source': '出库流量上限的来源记录，仅说明来源，不改变模型。',
    'upstream': '上游水电ID；仅支持完整出流传递的串联梯级，不支持一站水量复制给多个下游。',
    'lag_periods': '上游出库流量传到本站的滞后时段数；每段长度由场景时间栅格决定。',
    'pondage': '径流站可调节水量；有效库容范围为[v0−此值,v0]，日末回到v0。',
    'unit_gen_max': '抽蓄每台机组的发电功率上限。',
    'unit_gen_min': '抽蓄发电工况每台机组的最小功率。',
    'unit_pump': '每台机组抽水功率；定速机组固定为此值，变速机组将其作为上限。',
    'variable_speed': '开启后允许抽水功率在单机最小值与额定值之间变化；关闭时为定速抽水。',
    'unit_pump_min': '仅变速抽蓄使用的单机抽水功率下限；定速模式不使用此参数。',
    'e_gen': '每减少1万m³上库水量可发电的MWh；综合循环效率=e_gen/e_pump。',
    'e_pump': '每增加1万m³上库水量需要的抽水MWh；须不小于发电转换系数。',
    'v_total': '闭式抽蓄上下库总水量；下库水量始终=总水量−上库水量。',
    'vup_min': '上库物理水量下限；实际有效界限还须同时满足下库上下限。',
    'vup_max': '上库物理水量上限；实际有效界限还须同时满足下库上下限。',
    'vdn_min': '下库物理水量下限，会转换为上库的有效上限。',
    'vdn_max': '下库物理水量上限，会转换为上库的有效下限。',
    'vup0': '模拟开始前的上库水量，单位万m³。',
    'vup_end_bid': '新建或明确重建报价时的缺省日末申报；已有报价的日末上库申报优先。请在报价处修改当前申报。',
    'switch_gap': '抽水与发电工况转换需要空出的时段数；不是小时数。',
    'max_gen_starts': '每日发电启动次数按全站聚合台次限制，预算为单台上限×机组台数。',
    'max_pump_starts': '每日抽水启动次数按全站聚合台次限制，预算为单台上限×机组台数。',
    'start_cost_gen': '模型目标中的每台发电启动费用；利润采用独立成本档案，不由此字段替代。',
    'start_cost_pump': '模型目标中的每台抽水启动费用；利润采用独立成本档案，不由此字段替代。',
    'init_mode': '开始前全厂工况：全部闲置、全部发电或全部抽水；当前不支持独立部分台数初态。',
    'ecap': '额定储能容量，MWh；实际可用能量还受SOC范围与效率限制。',
    'eta_c': '充电功率转为库存能量的效率，取值大于0且不超过1。',
    'eta_d': '库存能量转为放电功率的效率，取值大于0且不超过1。',
    'soc_min': '技术允许SOC下限；当前报价可申报更窄范围，模型使用申报范围。',
    'soc_max': '技术允许SOC上限；当前报价可申报更窄范围，模型使用申报范围。',
    'soc0': '开始时库存/额定能量容量的比例；例如0.5表示50%。',
    'soc_end_bid': '新建或明确重建报价时的缺省日末SOC；已有报价的日末SOC申报优先。请在报价处修改当前申报。',
}


_DIRECT = {
    'thermal': {'bus','enabled','pmax','pmin','ramp','ut','dt','noload'},
    'renewable': {'bus','enabled','cap','profile'},
    'storage': {'bus','enabled','pmax','ecap','eta_c','eta_d','soc_min','soc_max','soc0','soc_end_bid'},
    'hydro': {'bus','enabled','regulation','n_units','unit_pmax','zones','ramp','ut','dt','water_rate',
              'init_units_on','init_p_unit','e_day_min','qout_max','qout_max_source','upstream','lag_periods','pondage'},
    'pumped': {'bus','enabled','n_units','unit_gen_max','unit_gen_min','unit_pump','variable_speed',
               'unit_pump_min','e_gen','e_pump','v_total','vup_min','vup_max','vdn_min','vdn_max',
               'vup0','vup_end_bid','switch_gap','max_gen_starts','max_pump_starts',
               'start_cost_gen','start_cost_pump','init_mode'},
}


def _known_field(family,path):
    if len(path)==1:return path[0] in _DIRECT.get(family,set())
    if family=='thermal' and len(path)==2:
        return (path[0]=='init' and path[1] in {'online','p0','hours'} or
                path[0]=='startup' and path[1] in {'cold','warm','hot','very_hot'})
    return (family=='hydro' and len(path)==3 and path[0]=='seasons'
            and path[1] in {'wet','dry'} and path[2] in {'qin','v0','vmin','vmax','v_end','qeco','e_day'})


def field_help(family, path, unit):
    name = path[-1]
    if not _known_field(family,path):
        return '历史附加字段，尚无可编辑的模型消费定义；保留原值，只读。'
    if family == 'hydro' and name in ('ut', 'dt'):
        if unit.get('commitment', {}).get('mode') == 'unit_duration_v1':
            return ('每台水轮机启动后至少连续在线的小时数。' if name == 'ut' else '每台水轮机停机后至少连续离线的小时数。') + '按时间网格满足；初始已持续时间和跨日剩余承诺共同生效。修改对下次运行生效。'
        return '旧模式未实现逐台最小开停时间约束；先启用逐台约束并核对研究初态。'
    if (family == 'hydro' and unit.get('regulation') == 'run_of_river'
            and name in ('vmin', 'vmax', 'v_end', 'e_day', 'e_day_min')):
        return '径流式不使用此独立约束；库容范围由初始库容和调节池容积推导，日末回到初值。'
    text = HELP.get(name, '历史附加字段，尚无可编辑的模型消费定义；保留原值，只读。')
    if len(path) > 1 and path[0] == 'seasons':
        text += ' 仅选择对应水期时生效。'
    return text


def field_readonly(family, path, unit):
    name = path[-1]
    return (not _known_field(family,path) or name == 'qout_max_source'
            or family == 'hydro' and ((name in ('ut', 'dt') and unit.get('commitment', {}).get('mode') != 'unit_duration_v1')
                or name == 'init_units_on' and unit.get('commitment', {}).get('mode') == 'unit_duration_v1'
                or unit.get('regulation') == 'run_of_river'
                    and name in ('vmin', 'vmax', 'v_end', 'e_day', 'e_day_min')))


def effective_quote_rows(cfg, unit, family):
    """Show the current quote's actual priority without resetting it."""
    from smm.unit_editor import _current_record
    bid = _current_record(cfg.root, cfg)['draft']['units'].get(unit['id'], {})
    rows = []
    def add(label, value, source):
        rows.append({'参数': label, '当前入模值': str(value), '来源': source})
    if family == 'storage':
        for field in ('soc_min', 'soc_max'):
            add('SOC' + ('下限' if field == 'soc_min' else '上限'), bid.get(field, unit[field]),
                '当前报价' if field in bid else '技术参数')
        add('日前日末SOC', bid.get('soc_end') if bid.get('soc_end') is not None else unit['soc0'],
            '当前报价' if bid.get('soc_end') is not None else '未申报，回到初始SOC')
    if family == 'pumped':
        target = bid.get('vup_end') if 'vup_end' in bid else unit.get('vup_end', unit.get('vup_end_bid', unit['vup0']))
        add('日前日末上库库容', unit['vup0'] if target is None else target,
            '未申报，回到初始上库' if target is None else ('当前报价' if 'vup_end' in bid else '技术旧申报vup_end' if 'vup_end' in unit else '技术缺省申报'))
        add('有效上库下限', max(unit['vup_min'], unit['v_total']-unit['vdn_max']), '上下库共同约束')
        add('有效上库上限', min(unit['vup_max'], unit['v_total']-unit['vdn_min']), '上下库共同约束')
        add('综合循环效率', unit['e_gen']/unit['e_pump'], '发电/抽水转换系数')
    if family == 'thermal' and bid:
        from smm.bids import resolve_startup_bid
        startup = resolve_startup_bid(cfg, bid)
        add('启动报价', startup['cost_yuan'] if startup['mode'] == 'uniform_per_unit' else bid.get('startup_cost'), '当前报价')
        add('空载报价', bid.get('noload_cost', unit['noload']), '当前报价' if 'noload_cost' in bid else '技术缺省值')
    return rows
