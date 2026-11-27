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
      '<div class="metrics">'+stat('三小时顶峰能力',fmt(s.peak),'MW',clock(s.start)+'–'+clock(s.start+12))+stat('平均发电耗水率',fmt(s.rate,3),'m³/kWh','次日固定值')+
      stat('区间来水日均值',fmt(s.inflow.reduce((a,b)=>a+b,0)/96),'m³/s','96 点预测均值')+'</div>'+
      '<div class="timebar"><label>核对时段<output></output></label><input type="range" min="0" max="95" step="1" aria-label="核对时段"></div><div class="plots"></div>'+
      '<section class="bid"><div class="plothead"><h3>电能量交易申报</h3><span class="hint">全天 '+bands.length+' 段</span></div><div class="bidgrid"><div><div class="hint">报价（元/MWh）</div>'+price+'<div class="hint" style="text-align:right">累计出力（MW）</div></div><div>'+table(['段','出力范围（MW）','元/MWh'],bands.map(([i,a,b,p])=>[i,fmt(a,2)+' – '+fmt(b,2),fmt(p,0)]))+'</div></div></section>'+
      '<details class="summary"><summary>本站 96 点原始报送数值</summary><div class="scroller">'+table(['时段','下限 MW','上限 MW','区间来水 m³/s'],s.low.map((v,i)=>[clock(i)+'–'+clock(i+1),fmt(v,2),fmt(s.high[i],2),fmt(s.inflow[i],2)]))+'</div></details>'+
      '<details class="summary"><summary>19 站报送总览</summary><div class="scroller">'+table(['电站','三小时顶峰 MW','耗水率 m³/kWh','日均区间来水 m³/s'],stations.map(r=>[r.name,fmt(r.peak,1),fmt(r.rate,3),fmt(r.inflow.reduce((a,b)=>a+b,0)/96,1)]))+'</div></details>'+
      '<p class="note">模拟日前纳入 19 站，实时选择参加并假设审核通过。特殊运行控制需求未单列。水位—库容及 NHQ 曲线未完整建模。</p>';
    const slider=main.querySelector('input');
    function update(){
      const q=Number(slider.value); state.q=q;
      const stamp=clock(q)+'–'+clock(q+1);
      main.querySelector('output').textContent=stamp;
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
