function renderDayReview(root, reviews) {
  const escape = value => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const fmt = (v, digits=1) => v == null ? '未归档' : (Math.abs(v)<0.5*10**-digits?0:v).toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits});
  const time = t => String(Math.floor(t/4)).padStart(2,'0')+':'+String(t%4*15).padStart(2,'0');
  const period = q => time(q)+'–'+time(q+1);
  const seriesColors = ['#08775e','#bd661b','#365da1'];
  const state = root._dayState || {case:0,station:'D10',peer:'D11',q:72};
  root._dayState = state;
  const opts = (pairs,value) => pairs.map(([k,v])=>'<option value="'+escape(k)+'"'+(String(k)===String(value)?' selected':'')+'>'+escape(v)+'</option>').join('');
  const head = (title,sub) => '<div class="head"><h3>'+escape(title)+'</h3><span>'+escape(sub)+'</span></div>';
  const card = content => '<section class="card">'+content+'</section>';
  const note = content => '<p class="note">'+content+'</p>';
  const line = (label,value) => '<div><small>'+escape(label)+'</small><b>'+escape(value)+'</b></div>';
  const metric = (label,value,unit) => '<div class="metric"><small>'+escape(label)+'</small><b>'+escape(value)+'</b><small>'+escape(unit)+'</small></div>';
  const table = (headers,rows) => '<div class="scroll"><table><thead><tr>'+headers.map(v=>'<th scope="col">'+escape(v)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(v=>'<td>'+escape(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
  const chart = (title, data, q, unit) => {
    const values=data.flatMap(d=>d[1]).filter(Number.isFinite);
    if (!values.length) return note('该归档缺少曲线。');
    const lo=Math.min(0,...values), hi=Math.max(lo+1,...values);
    const x=t=>55+t*465/96, y=v=>126-(v-lo)*103/(hi-lo);
    const guides=[lo,(hi+lo)/2,hi].map(v=>'<path d="M55 '+y(v)+' H520" stroke="#dfebe4"/><text x="50" y="'+(y(v)+4)+'" text-anchor="end">'+fmt(v,0)+'</text>').join('');
    const ticks=[0,24,48,72,96].map(t=>'<text x="'+x(t)+'" y="145" text-anchor="middle">'+time(t).slice(0,2)+'时</text>').join('');
    const traces=data.map(([,vs],i)=>{
      const points=vs.map((v,t)=>Number.isFinite(v)?x(t).toFixed(1)+','+y(v).toFixed(1)+' '+x(t+1).toFixed(1)+','+y(v).toFixed(1):'').join(' ');
      return '<polyline fill="none" stroke="'+seriesColors[i]+'" stroke-width="2" '+(i===2?'stroke-dasharray="4 4"':'')+' points="'+points+'"/>';
    }).join('');
    const marker='<path d="M'+x(q)+' 16 V126" stroke="#35443b" stroke-dasharray="3 3"/>';
    return '<div class="legend">'+data.map(([n,vs],i)=>'<span><i style="background:'+seriesColors[i]+'"></i>'+escape(n)+' '+fmt(vs[q])+' '+unit+'</span>').join('')+'</div><svg class="trend" viewBox="0 0 550 152" preserveAspectRatio="none" role="img" aria-label="'+escape(title)+'，点击选择时段" style="cursor:crosshair;font:11px sans-serif;fill:#526b64">'+guides+ticks+traces+marker+'</svg>';
  };
  function controls() {
    const r=reviews[state.case], stations=r.stations.map(s=>[s.id,s.name]);
    root.innerHTML='<header class="mast"><div><h2>日前预出清复盘</h2><p>大渡河 19 站 · 96 个时段 · 日前量价仅作计划参考</p></div>'+
      (reviews.length>1?'<label>场景<select data-role="case">'+opts(reviews.map((s,i)=>[i,s.label]),state.case)+'</select></label>':'<strong>'+escape(r.label)+'</strong>')+'</header>'+
      '<div class="tools"><label>主看电站<select data-role="station">'+opts(stations,state.station)+'</select></label><label>对照电站<select data-role="peer">'+opts(stations,state.peer)+'</select></label>'+
      '<label>全天时段 · 北京时间<input data-role="range" type="range" min="0" max="95" step="1" value="'+state.q+'"></label><label>精确时段<select data-role="quarter">'+opts(Array.from({length:96},(_,i)=>[i,period(i)]),state.q)+'</select></label></div><div class="content"></div><div class="footer"></div>';
    root.querySelector('[data-role="case"]')?.addEventListener('change',e=>{state.case=Number(e.target.value);controls();});
    ['station','peer'].forEach(role=>root.querySelector('[data-role="'+role+'"]').onchange=e=>{
      const other=role==='station'?'peer':'station';
      if(state[other]===e.target.value)state[other]=state[role];
      state[role]=e.target.value;
      root.querySelector('[data-role="'+other+'"]').value=state[other]; draw();
    });
    root.querySelector('[data-role="range"]').oninput=e=>move(Number(e.target.value));
    root.querySelector('[data-role="quarter"]').onchange=e=>move(Number(e.target.value));
    draw();
  }
  function move(q) {
    state.q=Math.max(0,Math.min(95,q));
    root.querySelector('[data-role="range"]').value=state.q;
    root.querySelector('[data-role="quarter"]').value=state.q;
    draw();
  }
  function draw() {
    const r=reviews[state.case], q=state.q, day=r.totals, sys=r.system, grid=r.grid[q];
    const main=r.stations.find(s=>s.id===state.station), peer=r.stations.find(s=>s.id===state.peer);
    const s=main.series, g=peer.series, typ=main.model==='reservoir'?'库容式':'径流式近似';
    const meta=period(q)+' · 节点 '+main.bus;
    const stages='<div class="strip">'+line('冻结申报 MW',fmt(s.declared_low[q],0)+'–'+fmt(s.declared_high[q],0))+line('可用于能量 MW',fmt(s.approved_low[q],0)+'–'+fmt(s.approved_high[q],0))+line('节点参考价 元/MWh',fmt(s.price[q],2))+'</div>';
    let end=0;
    const awards=main.raw.map(([width,raw],i)=>{
      const start=end; end+=width;
      return [fmt(start,0)+'–'+fmt(end,0),fmt(raw,2),fmt(main.effective[i][1],2),fmt(s.accepted[q]?.[i],1)];
    });
    const bids=card(head(main.name+' · 申报与分段中标',meta)+stages+table(['报价区间 MW','申报价','市场采用价','中标 MW'],awards)+note('价格单位为元/MWh。市场力缓解'+(r.mitigation?'已触发':'未触发')+'。分段中标量取自固定组合经济调度。'));
    const dispatch=card(head('机组组合 → 经济调度',period(q))+
      '<div class="strip">'+line(main.name+' SCUC',fmt(s.scuc[q])+' MW')+line(main.name+' SCED',fmt(s.sced[q])+' MW')+line(peer.name+' SCED',fmt(g.sced[q])+' MW')+'</div>'+
      chart('两站全天出力',[ [main.name+' SCED',s.sced],[peer.name+' SCED',g.sced],[main.name+' SCUC',s.scuc]],q,'MW')+
      note('SCUC 确定煤机开停，SCED 在固定组合下给出各站日前并网计划和节点参考价。点击曲线可定位至同一时段。'));
    const storageChange=(s.storage_m3[q]-s.initial_m3[q])/10000;
    const water=card(head(main.name+' · 来水与去向',typ+' · '+period(q))+
      table(['水账项目','数值','单位'],[
        ['企业申报的区间来水',fmt(s.forecast[q],2),'m³/s'],['市场侧区间来水',fmt(s.local[q],2),'m³/s'],['上游延时到水',fmt(s.arrival[q],2),'m³/s'],
        ['过机 / 生态旁泄 / 弃水',[s.turbine[q],s.bypass[q],s.spill[q]].map(v=>fmt(v,2)).join(' / '),'m³/s'],
        ['期初 → 段末库存',fmt(s.initial_m3[q]/1e4,2)+' → '+fmt(s.storage_m3[q]/1e4,2),'万 m³'],
        ['本段库存变化',fmt(storageChange,2),'万 m³']])+
      note(typ==='库容式'?'库容式电站可把来水留到后续时段发电，同时须满足库容、泄量和日末库存边界。':'径流式近似将可调库存设为零。本段来水应由过机、旁泄及弃水全部排出，下游按河段时滞收到下泄。'));
    const spill=card(head('本站弃水诊断',period(q))+
      '<div class="strip">'+line('出清弃水 m³/s',fmt(s.spill[q],1))+line('条件下界 m³/s',fmt(s.floor[q],1))+line('高于下界 m³/s',fmt(s.remaining[q],1))+'</div>'+
      chart('本站弃水及条件下界',[['出清弃水',s.spill],['条件下界',s.floor]],q,'m³/s')+
      note('固定本段期初库存、已到站水量和旁泄量，按允许的最高段末库存、最低下泄与最大过机流量计算弃水下界。剩余差额仍受全日水电联调、报价和电网约束影响，不能直接认定为可回收水量。'));
    const lineSummary=v=>v?escape(v[0])+' · '+fmt(v[1],1)+' / '+fmt(v[2],1)+' MW（'+fmt(100*Math.abs(v[1])/v[2],1)+'%）':'未归档';
    const security=card(head('节点参考价与电网边界',period(q))+
      '<div class="strip">'+line('最低 · 节点 '+grid.low[0],fmt(grid.low[1],2))+line('最高 · 节点 '+grid.high[0],fmt(grid.high[1],2))+line('负荷加权价',fmt(sys.weighted_price[q],2))+'</div>'+
      note('价格单位为元/MWh。基本工况负载率最高线路为 '+lineSummary(grid.base)+'。'+(grid.stress?'预测压力工况下为 '+lineSummary(grid.stress)+'。':''))+
      table(['本段调度指标','MW'],[['用户预测负荷',fmt(sys.load[q])],['气电保供出力',fmt(sys.fixed[q])],['煤机可靠性备用 / 要求',fmt(grid.reserve)+' / '+fmt(r.reserve)],['备用缺额 / 失负荷',fmt(sys.reserve_short[q],3)+' / '+fmt(sys.shed[q],3)]]));
    const typeNames={hydro:'大渡河水电',thermal:'燃煤',fixed:'气电保供',renewable:'光伏',storage:'闭式抽蓄'};
    const mix=card(head('各类电源与负荷',period(q))+
      table(['电源','设计 MW','可用 MW','发电 MW','全天 GWh'],r.mix.map(a=>[typeNames[a.kind],fmt(a.capacity,0),fmt(a.available[q],0),fmt(sys[a.kind][q],1),fmt(sys[a.kind].reduce((sum,v)=>sum+v,0)/4000,2)]))+
      chart('用户负荷与电源计划',[['用户负荷',sys.load],['常规水电',sys.hydro],['光伏',sys.renewable]],q,'MW')+
      note('主网发电量按 15 分钟积分。气电执行保供计划，抽水负荷单列。'));
    const ps=r.pump;
    const pumping=ps?card(head('抽蓄计划与上库库存','节点 '+ps.bus+' · '+period(q))+
      '<div class="strip">'+line('计划净注入 MW',fmt(ps.plan[q]))+line('抽水 / 发电 MW',fmt(sys.pump[q],0)+' / '+fmt(sys.storage[q],0))+line('段末可发电量 MWh',fmt(ps.energy[q],0))+'</div>'+
      chart('上库库存',[['段末可发电量',ps.energy]],q,'MWh')+
      note('期初 '+fmt(ps.start,0)+' MWh，日末 '+fmt(ps.energy[95],0)+' MWh。本算例按外部固定计划抽发，负的净注入代表抽水。')):'';
    const ref=r.reference;
    let comparison;
    if(ref.status==='verified'){
      const fields=[['常规水电 GWh','hydro_mwh',1000],['煤电 GWh','coal_mwh',1000],['弃光 GWh','pv_cut_mwh',1000],['各站弃水之和 万m³','station_spill_m3',1e4],['剩余负荷峰值 MW','residual_peak_mw',1],['负荷加权价 元/MWh','weighted_price',1]];
      comparison=card(head('1,200 MW 抽蓄 · 全天对照','同一份水电申报')+
        table(['指标','投入','停运','投入 − 停运'],fields.map(([n,k,scale])=>[n,fmt(day[k]/scale,2),fmt(ref.totals[k]/scale,2),fmt((day[k]-ref.totals[k])/scale,2)]))+
        note('两次均重新核定市场力缓解并出清，SCUC 的相对间隙容限为 1%。抽水 '+fmt(day.pump_mwh,0)+' MWh，发电 '+fmt(day.generation_mwh,0)+' MWh。剩余负荷为用户负荷加抽水，减抽蓄发电、光伏可用出力及气电计划。'));
    }else{
      comparison=card(head('抽蓄投运与停运对照','全天')+note(ref.status==='unavailable'?'停运对照未通过校核。'+escape(ref.reason||''):ref.status==='skipped'?'本次运行未选择停运对照。':'此历史归档未保存停运对照，重新运行可生成。'));
    }
    const basin=card(head('大渡河全流域','上游 → 下游 · 全天')+
      '<div class="scroll"><table><thead><tr><th>电站</th><th>模型类型</th><th>设计装机 MW</th><th>并网电量 GWh</th><th>本站弃水 万m³</th></tr></thead><tbody>'+
      r.stations.map(a=>'<tr><td><button type="button" data-station="'+escape(a.id)+'">'+escape(a.name)+'</button></td><td>'+(a.model==='reservoir'?'库容式':'径流式近似')+'</td><td>'+fmt(a.capacity,0)+'</td><td>'+fmt(a.daily_generation/1e3,2)+'</td><td>'+fmt(a.daily_water/1e4,1)+'</td></tr>').join('')+
      '</tbody></table></div>'+note('点站名核对该站。本站弃水仍可能沿梯级到达下游，同一份水可在多站统计，因此各站弃水之和不能作流域净损失。'));
    const content=root.querySelector('.content');
    content.innerHTML='<div class="metrics">'+metric('全流域并网水电量',fmt(day.hydro_mwh/1000,2),'GWh · 全天')+metric('各站弃水之和',fmt(day.station_spill_m3/1e8,2),'亿 m³ · 逐站累计')+metric('负荷加权参考价',fmt(day.weighted_price,2),'元/MWh · 全天')+metric('基本工况线路触限',day.tight_quarters+' / 96','时段 · 至少一条线路达限')+'</div><div class="grid">'+bids+dispatch+water+spill+mix+security+pumping+comparison+'</div>'+basin;
    root.querySelector('.footer').textContent='冻结申报 '+r.signature.slice(0,12)+'　·　全天最大节点平衡残差 '+Number(r.max_residual).toExponential(1)+' MW';
    content.querySelectorAll('.trend').forEach(svg=>svg.onclick=e=>{
      const rect=svg.getBoundingClientRect(); move(Math.floor((((e.clientX-rect.left)/rect.width)*550-55)*96/465));
    });
    content.querySelectorAll('[data-station]').forEach(button=>button.onclick=()=>{
      const sel=root.querySelector('[data-role="station"]');sel.value=button.dataset.station;sel.dispatchEvent(new Event('change'));sel.focus();
    });
  }
  controls();
}
