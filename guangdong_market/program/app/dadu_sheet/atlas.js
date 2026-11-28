const fmt = (n,d=1) => Number(n).toLocaleString("zh-CN",{minimumFractionDigits:d,maximumFractionDigits:d});
const clock = q => String(Math.floor(q/4)).padStart(2,"0")+":"+String(q%4*15).padStart(2,"0");
const esc = v => String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const colors=["#137a69","#ba7032","#536c9a"];
function table(labels,rows){
  return '<table><thead><tr>'+labels.map(x=>'<th scope="col">'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+
    rows.map(row=>'<tr>'+row.map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
}
function graph(lines,maximum,domain,marks,q){
  const W=420,H=148,L=48,R=14,T=10,B=24;
  const x=v=>L+v/domain*(W-L-R), y=v=>H-B-v/maximum*(H-T-B);
  const grid=[0,.5,1].map(f=>'<line x1="'+L+'" x2="'+(W-R)+'" y1="'+y(maximum*f)+'" y2="'+y(maximum*f)+'" stroke="#dfe8e6"/><text x="'+(L-5)+'" y="'+(y(maximum*f)+3)+'" text-anchor="end">'+fmt(maximum*f,maximum>=20?0:1)+'</text>').join('');
  const ticks=marks.map(([v,label])=>'<text x="'+x(v)+'" y="'+(H-5)+'" text-anchor="middle">'+label+'</text>').join('');
  const paths=lines.map((pts,i)=>'<polyline fill="none" stroke="'+colors[i]+'" stroke-width="2.4" points="'+pts.map(([a,b])=>x(a).toFixed(2)+','+y(b).toFixed(2)).join(' ')+'"/>').join('');
  const cursor=q===null?'':'<line stroke="#405457" stroke-dasharray="3 3" x1="'+x(q)+'" x2="'+x(q)+'" y1="'+T+'" y2="'+(H-B)+'"/>';
  return '<svg class="chart" aria-hidden="true" viewBox="0 0 '+W+' '+H+'">'+grid+ticks+paths+cursor+'</svg>';
}
function legend(labels){
  return '<div class="legend">'+labels.map((v,i)=>'<span><i style="background:'+colors[i]+'"></i>'+esc(v)+'</span>').join('')+'</div>';
}
function timeline(title,unit,series,q){
  const values=series.map(s=>s[1]), max=Math.max(1,...values.flat());
  const paths=values.map(v=>v.flatMap((n,i)=>[[i,n],[i+1,n]]));
  const reading=values.map((v,i)=>esc(series[i][0])+' '+fmt(v[q])).join(' · ');
  return '<section class="plot"><div class="plothead"><h3>'+title+'</h3><small>'+unit+'</small></div>'+
    graph(paths,max*1.04,96,[[0,"00:00"],[24,"06:00"],[48,"12:00"],[72,"18:00"],[96,"24:00"]],q+.5)+
    legend(series.map(s=>s[0]))+'<div class="hint">'+reading+'</div></section>';
}
function parameterGuide(s, stations, edge) {
  const idx = stations.findIndex(station => station.id === s.id);
  const upstreamMean = idx > 0 ? stations[idx - 1].meanFlow : 0;
  const reservoir = s.kind === 'reservoir';
  const storage = reservoir
    ? '台账调节库容 '+fmt(s.referenceStorage/1e8,3)+' 亿 m³。按场景折算，期初 '+fmt(s.initialStock/1e8,3)+' 亿 m³，运行上限 '+fmt(s.storageMax/1e8,3)+' 亿 m³，日末须落在 '+fmt(s.terminalLow/1e8,3)+'–'+fmt(s.terminalHigh/1e8,3)+' 亿 m³。期初和运行上限按台账库容的场景比例取值。期末区间由期初加减允许蓄放的水量确定，蓄放水量按场景断面流量 × 允许天数 × 86,400 秒折算，并截在运行边界内。'
    : '本站按零可调库容近似。每 15 分钟，区间来水加上延迟到达的上游下泄，必须由过机、专用旁泄和弃水消化。该近似忽略本站可能具备的小库调节能力。';
  const entries = [
    ['装机与能力上下限', '设计装机 '+fmt(s.capacity,0)+' MW，首段技术可用上限 '+fmt(s.technical[0],2)+' MW，由装机乘场景可用系数（91.5%–94.5%）得到。原申报在全梯级水账和爬坡约束下，分别寻找本站每段最低、最高可行出力。', '审核后的上下限直接约束日前 SCUC 与 SCED；市场另按独立预测检查爬坡、网络与联合水账。这些极值分别求得，不能把每个时段的上限拼成一条可执行计划。'],
    ['三小时顶峰', '在全日 85 个连续 12 段窗口中，寻找本站可以每段都达到的最高功率。本站 '+fmt(s.peak,1)+' MW，对应一个可行窗口 '+clock(s.start)+'–'+clock(s.start+12)+'。', '作为能力参考归档。出清没有必须连续三小时发足该值的约束，也没有把它自动计为中标备用。'],
    ['次日发电耗水率', esc(s.rateMethod)+'。本站固定采用 '+fmt(s.rate,4)+' m³/kWh。以 100 MW 发 15 分钟为例，发电量 25 MWh，过机水量约 '+fmt(25000*s.rate,0)+' m³。', '水量约束直接采用 Q过机 = P × 耗水率 / 3.6。专用生态旁泄和弃水另计，两者均不产生电量。'],
    ['区间来水', '多年平均断面流量差为 '+fmt(s.meanFlow,0)+' − '+fmt(upstreamMean,0)+' = '+fmt(s.meanFlow-upstreamMean,0)+' m³/s。首站以上游基数零计。按水期修正断面差（丰水代表日乘 1.35，枯水代表日乘 0.65），再叠加日内变化和公司预测扰动。', '图中是公司事前预测，日均值为 96 点平均。市场另用独立来水预测逐段做水量平衡；上游到水按河道队列另计。'],
    [reservoir?'库容与期末库存':'径流式水量守恒',storage,reservoir?'提前发电会降低后续库存。较贵时段能否增加出力，取决于当时存水、流量边界及期末库存是否仍可达。':'本站不能自行留水到晚峰。来水较多时仍可能受设备或线路限制而弃水。'],
    ['生态下泄与河段时滞','总下泄下限 '+fmt(s.minimumRelease,2)+' m³/s、其中专用旁泄 '+fmt(s.bypass,2)+' m³/s，分别按场景断面流量的 9.5%、0.6% 设定。河段以资料距离、合成流速 3.5 m/s 换算并离散到 15 分钟；当前所示河段 '+edge.minutes+' 分钟。','总下泄含过机、旁泄与弃水，延迟后进入下游。日前跨时段跟踪在途水量，日末仍在河道内的水单独归档。'],
    ['分段量价','使用已冻结的事前模板。原模板按装机分 '+s.segments.length+' 段，将事前联合优化得到的边际水机会成本排序、分组取中点样本，再拟合段价。本页不重新优化报价。','各段被接受的电量乘市场采用价进入出清成本；触发市场力缓解时采用价可能低于申报价。节点价来自固定组合 SCED 的节点平衡对偶。']
  ];
  return '<details class="summary parameter-guide"><summary>这些参数怎样确定，怎样进入出清？</summary><dl>'+entries.map(([label,origin,effect])=>'<dt>'+label+'</dt><dd><b>取值依据</b> '+origin+'</dd><dd><b>出清作用</b> '+effect+'</dd>').join('')+'</dl></details>';
}
export default function({parentElement,data}){
  const el=parentElement.querySelector('.atlas');
  const stations=data.stations, byId=new Map(stations.map(s=>[s.id,s]));
  const state=el._review || {uid:'D10',q:0};
  el._review=state;
  const choices=stations.map(s=>'<option value="'+esc(s.id)+'">'+esc(s.name)+'</option>').join('');
  el.innerHTML='<div class="board"><aside class="rail"><h2>大渡河梯级</h2><p>19 座电站 · 从上游到下游</p><label class="mobile-nav">沿河选站<select>'+choices+'</select></label><nav aria-label="梯级电站">'+stations.map(s=>
    '<button type="button" class="station '+(s.kind==='reservoir'?'res':'')+'" data-id="'+esc(s.id)+'"><i></i><span>'+esc(s.name)+'</span><small>'+(s.kind==='reservoir'?'库容':'径流')+'</small></button>').join('')+'</nav></aside><article class="main"></article></div>';
  const main=el.querySelector('.main'),select=el.querySelector('select');
  function draw(){
    const guideWasOpen = main.querySelector(".parameter-guide")?.open || false;
    const s=byId.get(state.uid), q=state.q;
    const edge=data.reaches.find(r=>r.up===s.id)||data.reaches.find(r=>r.down===s.id);
    const peer=byId.get(edge.up===s.id?edge.down:edge.up);
    const rel=esc(byId.get(edge.up).name)+' → '+esc(byId.get(edge.down).name);
    el.querySelectorAll('.station').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===s.id)));
    select.value=s.id;
    let last=0;
    const bands=s.segments.map(([w,p],i)=>{const start=last;last=Math.round((start+w)*100)/100;return [i+1,start,last,p]});
    const steps=bands.flatMap(([,a,b,p])=>[[a,p],[b,p]]);
    const price=graph([steps],Math.max(1,...bands.map(b=>b[3]))*1.05,last,[[0,'0'],[last/2,fmt(last/2,0)],[last,fmt(last,0)]],null);
    const stat=(label,value,unit,extra)=>'<div><small>'+label+'</small><b>'+value+'</b><span class="unit">'+unit+'</span><small>'+extra+'</small></div>';
    main.innerHTML='<header class="mast"><div><div class="kicker">'+esc(data.scenario)+'　/　次日生产运行报送</div><h2>'+esc(s.name)+'</h2></div><div class="meta"><b>'+rel+'</b>模拟传播 '+edge.minutes+' 分钟</div></header>'+
      '<div class="metrics">'+stat('原申报三小时顶峰能力',fmt(s.peak),'MW',clock(s.start)+'–'+clock(s.start+12))+stat('平均发电耗水率',fmt(s.rate,3),'m³/kWh','次日固定值')+
      stat('区间来水日均值',fmt(s.inflow.reduce((a,b)=>a+b,0)/96),'m³/s','96 点预测均值')+'</div>'+
      '<p class="logic"><b>'+(s.kind==='reservoir'?'库容式':'径流式近似')+'</b>　'+(s.kind==='reservoir'?'本场景从 '+fmt(s.initialStock/1e8,2)+' 亿 m³ 调节库容起步，可把水留到晚峰，日末须满足库存区间。':'可调库容按零处理。每 15 分钟，要把区间来水和上游到水分配给发电、专用旁泄与弃水。')+'</p>'+parameterGuide(s,stations,edge)+
      '<div class="timebar"><label>核对时段<output></output></label><input type="range" min="0" max="95" step="1" aria-label="核对时段"></div><div class="plots"></div>'+
      '<section class="bid"><div class="plothead"><h3>电能量交易申报</h3><span class="hint">全天 '+bands.length+' 段</span></div><div class="bidgrid"><div><div class="hint">报价（元/MWh）</div>'+price+'<div class="hint" style="text-align:right">累计出力（MW）</div></div><div>'+table(['段','出力范围（MW）','元/MWh'],bands.map(([i,a,b,p])=>[i,fmt(a,2)+' – '+fmt(b,2),fmt(p,0)]))+'</div></div></section>'+
      '<details class="summary"><summary>本站 96 点工况与申报数值</summary><div class="scroller">'+table(['时段','工况','并网下限 MW','并网上限 MW','区间来水 m³/s'],s.low.map((v,i)=>[clock(i)+'–'+clock(i+1),esc(s.status[i]),fmt(v,2),fmt(s.high[i],2),fmt(s.inflow[i],2)]))+'</div></details>'+
      '<details class="summary"><summary>19 站报送总览</summary><div class="scroller">'+table(['电站','三小时顶峰 MW','耗水率 m³/kWh','日均区间来水 m³/s'],stations.map(r=>[r.name,fmt(r.peak,1),fmt(r.rate,3),fmt(r.inflow.reduce((a,b)=>a+b,0)/96,1)]))+'</div></details>'+
      '<p class="note">19 站组成模拟联合申报组合，实际业主分别登记。'+(data.hasOperations?'并网能力曲线已叠加工况与出力要求。水位控制采用线性库容换算，耗水率为次日定值。':'本版本仅含基础申报。')+'</p>';
    main.querySelector('.parameter-guide').open = guideWasOpen;
    const slider=main.querySelector('input');
    function update(){
      const q=Number(slider.value); state.q=q;
      const stamp=clock(q)+'–'+clock(q+1);
      main.querySelector('output').textContent=stamp+' · '+s.status[q];
      slider.setAttribute('aria-valuetext',stamp);
      main.querySelector('.plots').innerHTML=timeline('发电能力','MW',[[s.name+'上限',s.high],[peer.name+'上限',peer.high],[s.name+'下限',s.low]],q)+
        timeline('区间天然来水','m³/s',[[s.name,s.inflow],[peer.name,peer.inflow]],q);
    }
    slider.value=q;slider.oninput=update;update();
  }
  el.querySelector('nav').onclick=e=>{const b=e.target.closest('button');if(b){state.uid=b.dataset.id;draw()}};
  select.onchange=()=>{state.uid=select.value;draw()};
  draw();
}
