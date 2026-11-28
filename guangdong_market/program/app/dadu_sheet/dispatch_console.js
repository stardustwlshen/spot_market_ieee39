
const sourceTypes = [
  ['hydro','常规水电','#398b96'], ['thermal','燃煤','#687b94'],
  ['fixed','气电保供','#ac8457'], ['renewable','光伏','#e1b65b'],
  ['storage','抽蓄发电','#827eac']
];
const format = (value, digits=0) => Number.isFinite(value)
  ? (Math.abs(value)<0.5*10**-digits ? 0 : value).toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
const clock = q => String(Math.floor(q/4)).padStart(2,'0')+':'+String(q%4*15).padStart(2,'0');
const span = q => clock(q)+'–'+clock(q+1);
const escape = v => String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const intervalOptions = q => Array.from({length:96},(_,i)=>'<option value="'+i+'" '+(i===q?'selected':'')+'>'+span(i)+'</option>').join('');

function supplyChart(system){
  const totalDemand=system.load.map((v,i)=>v+system.pump[i]);
  const cumulative=system.load.map(()=>0);
  const supply=system.load.map((_,i)=>sourceTypes.reduce((sum,[key])=>sum+system[key][i],0));
  const maximum=Math.max(1,...supply,...totalDemand);
  const step=10**Math.floor(Math.log10(maximum/3));
  const top=Math.ceil(maximum/step)*step;
  const x=t=>47+640*t/96, y=v=>163-139*v/top;
  const steps=values=>values.flatMap((v,t)=>[[x(t),y(v)],[x(t+1),y(v)]]);
  const coords=points=>points.map(p=>p.map(n=>n.toFixed(2)).join(',')).join(' ');
  const fills=sourceTypes.map(([key,,color])=>{
    const lower=cumulative.slice();
    cumulative.forEach((v,i)=>cumulative[i]=v+system[key][i]);
    return '<polygon fill="'+color+'" points="'+coords([...steps(cumulative),...steps(lower).reverse()])+'"/>';
  }).join('');
  const grid=[0,.25,.5,.75,1].map(f=>'<line x1="47" x2="687" y1="'+y(f*top)+'" y2="'+y(f*top)+'"/><text x="41" y="'+(y(f*top)+4)+'" text-anchor="end">'+format(f*top/1000,1)+'</text>').join('');
  const ticks=[0,16,32,48,64,80,96].map(t=>'<text x="'+x(t)+'" y="183" text-anchor="middle">'+clock(t)+'</text>').join('');
  return '<svg class="supply-chart" role="img" aria-label="96 时段分电源计划与系统需求，另可用时段选择器定位" viewBox="0 0 714 192" preserveAspectRatio="none"><g class="axis">'+grid+ticks+'<text x="12" y="13">GW</text></g>'+fills+'<polyline points="'+coords(steps(totalDemand))+'" fill="none" stroke="#23384c" stroke-width="2" stroke-dasharray="4 2"/><g class="time-marker"><line y1="21" y2="163"/><path d="M-4,19 h8 l-4,6 z"/></g></svg>';
}

export default function({parentElement,data}){
  const root=parentElement.querySelector('.dispatch-console'), s=data.system;
  const peak=s.load.indexOf(Math.max(...s.load));
  const state=root._selection || {q:peak,type:'all'};
  root._selection=state;
  const metric=(title,key,unit)=>'<div class="dc-metric"><span>'+title+'</span><div><strong data-stat="'+key+'"></strong><small>'+unit+'</small></div></div>';
  const headers=['机组 / 节点','调节方式','申报区间 MW','核准上限 MW','SCUC 计划 MW','SCED 中标 MW','节点价 元/MWh','入流 m³/s','弃水 m³/s'];
  root.innerHTML='<div class="dc-title"><div><h2>运行总览</h2><p>最新日前归档 · '+escape(data.label)+'（北京时间）</p></div><div class="dc-seal"><span class="'+(data.audit_slack<1e-5?'ok':'warn')+'">'+(data.audit_slack<1e-5?'可靠性松弛为零':'存在可靠性松弛')+'</span><small>冻结申报 <code>'+escape(data.signature.slice(0,12))+'</code></small></div></div>'+
    '<div class="dc-board"><div class="dc-time"><strong>计划时段</strong><div class="dc-controls"><button data-step="-1" aria-label="上一时段">‹</button><select aria-label="计划时段">'+intervalOptions(state.q)+'</select><button data-step="1" aria-label="下一时段">›</button></div><button class="dc-peak">定位负荷峰值</button><small data-quarter></small></div>'+
    '<div class="dc-metrics">'+metric('预测用户负荷','load','MW')+metric('梯级水电中标','hydro','MW')+metric('负荷加权参考价','weighted_price','元/MWh')+metric('最高线路负载率','line','%')+'</div></div>'+
    '<div class="dc-grid"><section class="dc-panel"><div class="dc-head"><h3>电源出力与系统需求</h3><span>全日 · 15 分钟</span></div>'+supplyChart(s)+'<div class="dc-legend">'+sourceTypes.map(([key,label,color])=>'<span><i style="background:'+color+'"></i>'+label+' <strong data-source="'+key+'"></strong></span>').join('')+'<span><b></b>需求（含抽水） <strong data-source="demand"></strong></span><span>单位 MW</span></div></section>'+
    '<section class="dc-panel"><div class="dc-head"><h3>运行校核</h3><span data-check-time></span></div><dl class="dc-checks"></dl></section></div>'+
    '<section class="dc-panel dc-ledger"><div class="dc-head"><div><h3>大渡河梯级计划</h3><p>按上游至下游排列 · 主网并网口径</p></div><label>调节方式 <select aria-label="筛选调节方式"><option value="all">全部电站</option><option value="reservoir">库容式</option><option value="river">径流式近似</option></select></label></div><div class="dc-scroll" tabindex="0" aria-label="梯级计划表可左右滚动"><table><thead><tr>'+headers.map(h=>'<th scope="col">'+h+'</th>').join('')+'</tr></thead><tbody></tbody></table></div><div class="dc-foot"><span data-count></span><span>入流 = 区间来水 + 到达本站的上游下泄</span></div></section>'+
    '<details class="dc-help"><summary>指标口径与操作说明</summary><p>选择时段或点击供需图，可同步查看机组计划、节点价和线路校核。冻结申报是本次归档引用的版本。修改申报后，需重新冻结并运行，才会生成新归档。</p><p>SCUC 确定机组组合和初步计划，SCED 在既定组合下计算分站中标出力和节点参考价。负荷加权参考价以各节点用户负荷为权重。日前预出清用于安排计划，不产生结算金额。</p><p>线路负载率按基本工况的绝对潮流除以线限计算。煤机预留用于覆盖本模型的可靠性要求。流量以 m³/s 表示，每段水量等于流量乘 900 秒。</p></details>';

  const select=root.querySelector('[aria-label="计划时段"]');
  const filter=root.querySelector('[aria-label="筛选调节方式"]');
  filter.value=state.type;
  const chart=root.querySelector('.supply-chart');
  const checks=root.querySelector('.dc-checks'), body=root.querySelector('tbody');
  const check=(name,value,flag=false)=>'<div><dt>'+name+'</dt><dd'+(flag?' class="warn"':'')+'>'+value+'</dd></div>';
  function update(){
    const q=state.q, g=data.grid[q], line=g.base, percent=s.max_line_pu[q]*100;
    select.value=String(q);
    root.querySelector('[data-quarter]').textContent='第 '+(q+1)+' / 96 时段';
    root.querySelector('[data-check-time]').textContent=span(q);
    root.querySelectorAll('[data-source]').forEach(el=>{
      const key=el.dataset.source;
      el.textContent=format(key==='demand'?s.load[q]+s.pump[q]:s[key][q]);
    });
    root.querySelectorAll('[data-step]').forEach(b=>b.disabled=(q===0&&b.dataset.step==='-1')||(q===95&&b.dataset.step==='1'));
    root.querySelectorAll('[data-stat]').forEach(el=>{
      const key=el.dataset.stat;
      el.textContent=key==='line'?format(percent,1):format(s[key][q],key==='weighted_price'?2:0);
      el.classList.toggle('warn',key==='line' && percent>=100-1e-4);
    });
    root.querySelector('.time-marker').setAttribute('transform','translate('+(47+(q+.5)*640/96)+' 0)');
    checks.innerHTML=check('最高负载线路',line?escape(line[0])+' · '+format(percent,1)+'%':'—',percent>=100-1e-4)+
      check('线路潮流 / 限额',line?format(line[1])+' / '+format(line[2])+' MW':'—')+
      check('煤机预留 / 要求',format(g.reserve)+' / '+format(data.reserve)+' MW')+
      check('备用缺额 / 失负荷',format(s.reserve_short[q],2)+' / '+format(s.shed[q],2)+' MW',s.reserve_short[q]>1e-5||s.shed[q]>1e-5)+
      check('气电保供 / 抽水',format(s.fixed[q])+' / '+format(s.pump[q])+' MW');
    const stations=data.stations.filter(a=>state.type==='all'||(a.model==='reservoir')===(state.type==='reservoir'));
    body.innerHTML=stations.map(a=>{
      const v=a.series;
      return '<tr><th scope="row"><b>'+escape(a.name)+'</b> <small>'+escape(a.id)+' · N'+escape(a.bus)+'</small></th><td>'+(a.model==='reservoir'?'库容式':'径流式近似')+'</td><td>'+format(v.declared_low[q])+'–'+format(v.declared_high[q])+'</td><td>'+format(v.approved_high[q])+'</td><td>'+format(v.scuc[q],1)+'</td><td class="dc-award">'+format(v.sced[q],1)+'</td><td>'+format(v.price[q],2)+'</td><td>'+format(v.local[q]+v.arrival[q],1)+'</td><td>'+format(v.spill[q],1)+'</td></tr>';
    }).join('');
    root.querySelector('[data-count]').textContent='当前 '+stations.length+' / '+data.stations.length+' 站 · '+span(q);
  }
  select.onchange=()=>{state.q=Number(select.value);update();};
  filter.onchange=()=>{state.type=filter.value;update();};
  root.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>{state.q=Math.max(0,Math.min(95,state.q+Number(b.dataset.step)));update();});
  root.querySelector('.dc-peak').onclick=()=>{state.q=peak;update();};
  chart.onclick=event=>{
    const rect=chart.getBoundingClientRect(), x=714*(event.clientX-rect.left)/rect.width;
    if(x>=47 && x<=687){state.q=Math.min(95,Math.floor((x-47)*96/640));update();}
  };
  update();
}
