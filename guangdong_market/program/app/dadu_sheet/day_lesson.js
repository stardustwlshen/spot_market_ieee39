function lessonTime(q) {
  return String(Math.floor(q / 4)).padStart(2, '0') + ':' + String(q % 4 * 15).padStart(2, '0');
}

function lessonFacts(run, id, q) {
  const station = run.inputs.stations.find(s => s.id === id);
  const offer = run.company.offers[id];
  const row = run.da[q];
  const w = row.hydro[id];
  const hydraulic = run.company.hydraulic_declaration;
  const lake = hydraulic.reservoirs.find(s => s.id === id);
  const reach = hydraulic.reaches.find(r => r.downstream === id);
  const previous = q ? run.da[q-1].hydro[id].storage_m3 : lake.initial_storage_m3;
  const power = row.power[id];
  const arrival = w.upstream_arrival_m3 / 900;
  const incoming = w.natural_inflow_m3s + arrival;
  const outgoing = w.turbine_m3s + w.eco_bypass_m3s + w.water_spill_m3s;
  const [up, down] = row.ancillary_holds_mw?.[id] || [0,0];
  const [low, high] = run.bounds[q][id];
  const accepted = row.accepted_segments_mw?.[id];
  let start = 0;
  const bids = run.raw_offers[id].map(([width, price], i) => {
    const band = {start, end:start+width, price, used:run.effective_offers[id][i][1], award:accepted?.[i]};
    start += width;
    return band;
  });
  return {station, q, power, mwh:power/4, declared:offer.available_mw[q], low:low+down, high:high-up,
    scuc:run.scuc[q].power[id], price:row.raw_nodal_prices[station.bus], bids, previous, end:w.storage_m3,
    local:w.natural_inflow_m3s, arrival, incoming, outgoing, turbine:w.turbine_m3s, bypass:w.eco_bypass_m3s,
    spill:w.water_spill_m3s, delta:w.storage_m3-previous, waterChange:900*(incoming-outgoing),
    upstream:reach ? run.inputs.stations.find(s=>s.id===reach.upstream).name : null, lag:reach?.travel_quarters};
}

function gradeLessonEnergy(value, p) {
  const raw = String(value).trim().replace(/[，,]/g, '');
  const n = Number(raw);
  if (!raw || !Number.isFinite(n) || n < 0) return '请输入一个非负数，单位是 MWh。';
  if (Math.abs(n-p/4) <= 0.051) return '算对了。15 分钟是四分之一小时，电量等于平均功率乘以 0.25 小时。';
  if (Math.abs(n-p) <= 0.051) return '这个数是功率。按这个功率发满一小时才有这么多 MWh，再把时间换成 0.25 小时试试。';
  if (Math.abs(n-p*15) <= Math.max(.1,p*.0001)) return '你用了 15 分钟。单位 MWh 中的 h 是小时，要先用 15 除以 60。';
  return '再试一次。用“安排发电”的 MW 数乘以 15 ÷ 60，保留一位小数即可。';
}

async function readLessonArchive(packed) {
  const bytes = Uint8Array.from(atob(packed), c => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(stream).text());
}

function renderDayLesson(root, cases) {
  const esc = x => String(x).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const f = (x, places=1) => x == null ? '未归档' : (Math.abs(x)<0.5*10**-places?0:x).toLocaleString('zh-CN',{minimumFractionDigits:places,maximumFractionDigits:places});
  const options = (items,selected) => items.map(([value,label])=>'<option value="'+esc(value)+'"'+(String(value)===String(selected)?' selected':'')+'>'+esc(label)+'</option>').join('');
  const state = root._lessonState || {index:0, id:'D10', q:72};
  root._lessonState = state;
  let run, serial=0;
  const $ = selector => root.querySelector(selector);
  async function loadCase() {
    const request=++serial, selected=cases[state.index] || cases[0];
    root.setAttribute('aria-busy','true');
    try {
      const archive = await readLessonArchive(selected.packed);
      if(request!==serial || root._lessonVersion!==loadCase) return;
      run=archive;
      if(!run.inputs.stations.some(s=>s.id===state.id)) state.id=run.inputs.stations[0].id;
      root.innerHTML='<header><h2>明天这座水电站要发多少电？</h2><p>今天，电厂把明天能发多少、每档出力报什么价告诉市场。市场把一天分成 96 个十五分钟，给每座电站排出计划。我们挑其中一段，看这张计划怎样读。</p></header>'+
        '<div class="lesson-controls">'+(cases.length>1?'<label>选择算例<select data-lesson="case">'+options(cases.map((a,i)=>[i,a.label]),state.index)+'</select></label>':'<strong>'+esc(selected.label)+'</strong>')+
        '<label>看哪座电站<select data-lesson="station">'+options(run.inputs.stations.map(s=>[s.id,s.name]),state.id)+'</select></label>'+
        '<label>看哪 15 分钟（北京时间）<select data-lesson="quarter">'+options(Array.from({length:96},(_,q)=>[q,lessonTime(q)+'–'+lessonTime(q+1)]),state.q)+'</select></label></div>'+
        '<div class="lesson-body"></div><footer>四川规则研究算例 · IEEE 39 节点模拟电网 · 19 站统一申报是教学设定。页面使用已完成的日前计算结果。</footer>';
      if(cases.length>1) $('[data-lesson="case"]').onchange=e=>{state.index=Number(e.target.value); loadCase();};
      $('[data-lesson="station"]').onchange=e=>{state.id=e.target.value;draw();};
      $('[data-lesson="quarter"]').onchange=e=>{state.q=Number(e.target.value);draw();};
      const context=document.createElement('p');
      context.className='lesson-small';
      context.textContent='丰水或枯水决定本算例采用哪组来水和负荷预测。预测误差越大，市场要多留一些能随时增加的出力。本场景为预测误差额外预留 '+f(run.margin.da_extra_up_mw,0)+' MW。';
      $('.lesson-controls').after(context);
      draw();
    } catch(error) {
      if(request===serial && root._lessonVersion===loadCase) root.textContent='未能打开教学算例。请在最新版 Edge、Chrome、Firefox 或 Safari 中重新打开。';
    } finally {
      if(request===serial && root._lessonVersion===loadCase) root.setAttribute('aria-busy','false');
    }
  }
  function draw() {
    const d=lessonFacts(run,state.id,state.q), name=esc(d.station.name);
    const bar=(label,value,col) => '<div class="lesson-meter"><span>'+label+'</span><div class="track"><i style="width:'+Math.min(100,Math.max(0,value/d.station.capacity_mw*100))+'%;background:'+col+'"></i></div><b>'+f(value)+' MW</b></div>';
    const date=lessonTime(d.q)+'–'+lessonTime(d.q+1);
    const first=d.bids[0];
    const model=d.station.model==='reservoir';
    const arrivalText=d.upstream ? '上游是'+esc(d.upstream)+'。这段河道在算例中需要 '+d.lag*15+' 分钟。'+
      (d.q>=d.lag?'本段的上游到水对应上游 '+lessonTime(d.q-d.lag)+'–'+lessonTime(d.q-d.lag+1)+' 的下泄。':'本段收到的是日初已经在河道中途的水。') : '这是算例最上游的电站，没有来自模型内上一座电站的到水。';
    const reaches=run.company.hydraulic_declaration.reaches;
    const up=reaches.find(r=>r.downstream===state.id), down=reaches.find(r=>r.upstream===state.id);
    const stationName=id=>run.inputs.stations.find(s=>s.id===id).name;
    const riverButton=(id,q,verb)=>'<button type="button" data-river="'+esc(id)+'" data-time="'+q+'">'+verb+esc(stationName(id))+' '+lessonTime(q)+'</button>';
    const riverLinks=[];
    if (up && state.q>=up.travel_quarters) riverLinks.push(riverButton(up.upstream,state.q-up.travel_quarters,'追溯到'));
    if (down && state.q+down.travel_quarters<96) riverLinks.push(riverButton(down.downstream,state.q+down.travel_quarters,'跟水去'));
    const waterDirection = Math.abs(d.delta)<.05?'基本不变':d.delta>0?'增加':'减少';
    const bidRows=d.bids.map(b=>'<tr><td>'+f(b.start,0)+'–'+f(b.end,0)+'</td><td>'+f(b.price,2)+'</td><td>'+f(b.used,2)+'</td><td>'+f(b.award)+'</td></tr>').join('');
    $('.lesson-body').innerHTML=
      '<section><h3>电厂报了多少，市场安排了多少？</h3><p>看'+name+'的 '+date+'。装机是电站的设计规模，申报上限是它报给市场的本段能力，“安排发电”是最后排给它的并网计划。三条横线用同一把尺。</p>'+
      bar('设计装机',d.station.capacity_mw,'#a6b9ad')+bar('申报上限',d.declared,'#629c82')+bar('安排发电',d.power,'#126952')+
      '<p class="lesson-small">市场先决定哪些煤机开机（SCUC），再按已定开机状态分配各站出力（SCED）。本站经工况和辅助服务占位核准，可参与电能量的范围为 '+f(d.low)+'–'+f(d.high)+' MW。线路容量、用电需求和上下游水量一起约束这个计划。</p>'+
      '<details><summary>展开看电厂怎样分档报价</summary><p class="lesson-small">每行表示一段额外的功率。'+(first?'例如第 1 档覆盖 '+f(first.start,0)+'–'+f(first.end,0)+' MW，电厂报价 '+f(first.price,2)+' 元/MWh。':'')+'最右一列的功率相加，就是本站的并网中标功率。有些历史算例只保存了总数。</p>'+
      '<div class="table-wrap"><table><thead><tr><th>功率范围 MW</th><th>申报价<br>元/MWh</th><th>市场采用价<br>元/MWh</th><th>本档中标<br>MW</th></tr></thead><tbody>'+bidRows+'</tbody></table></div></details></section>'+
      '<div class="lesson-columns"><section><h3>这 15 分钟一共发多少电？</h3><p>MW 衡量发电的快慢，MWh 衡量一段时间发出的电量。以 100 MW 发电 1 小时是 100 MWh，只发 15 分钟就是 25 MWh。</p>'+
      '<div class="equation">'+name+'的计划功率为 <b>'+f(d.power,2)+' MW</b>，持续 <b>0.25 小时</b>。</div>'+
      '<form class="question" data-exercise><label>试着算出计划电量（MWh）<input inputmode="decimal" autocomplete="off" style="font:inherit;padding:7px;max-width:12em" placeholder="保留一位小数"></label><button type="submit">检查答案</button><button type="button" data-solution>看算式</button><output class="feedback" aria-live="polite"></output></form></section>'+
      '<section><h3>这个价格是谁的价格？</h3><p>'+name+'接入模拟电网的 <b>'+esc(d.station.bus)+' 号节点</b>。节点就是电站接入电网的位置。市场算出的该点日前参考价为 <b>'+f(d.price,2)+' 元/MWh</b>。</p>'+
      '<p class="lesson-small">可以把节点价理解为，在当前开机状态和约束下，该位置再多用一点电时，系统供电成本的边际变化。电厂给每档出力的申报价可以与节点价不同。</p>'+
      '<div class="question"><p>知道节点参考价，就知道电站最终收到多少钱了吗？</p><button data-price="yes">可以，直接乘电量</button><button data-price="no">还不能</button><output class="feedback" data-price-answer aria-live="polite"></output></div></section></div>'+
      '<section><h3>水从哪里来，过机后去了哪里？</h3><p><b>'+name+' · '+(model?'库容式':'径流式近似')+'</b>　'+(model?'这座站可以把一部分水留到后面的时段发电。':'本算例把这座站的可调库存设为零，到站的水在本段全部下泄。')+arrivalText+'</p>'+
      '<div class="lesson-columns"><div><b>每秒流入 '+f(d.incoming,2)+' m³</b><p class="lesson-small">本河段来水 '+f(d.local,2)+'，加上游到水 '+f(d.arrival,2)+' m³/s。</p></div><div><b>每秒流出 '+f(d.outgoing,2)+' m³</b><p class="lesson-small">过机发电 '+f(d.turbine,2)+'，生态旁泄 '+f(d.bypass,2)+'，弃水 '+f(d.spill,2)+' m³/s。各类下泄都会继续流向下游。</p></div></div>'+
      '<div class="equation">15 分钟 = 900 秒。库存变化 ≈（'+f(d.incoming,2)+' − '+f(d.outgoing,2)+'）× 900 ≈ <b>'+f(d.waterChange,0)+' m³</b>。</div><p class="lesson-small">按未舍入数据复算，模型库存从 '+f(d.previous,0)+' 到 '+f(d.end,0)+' m³，'+waterDirection+'。各站弃水相加可能重复统计同一股水。</p>'+ (riverLinks.length?'<div class="river-links">'+riverLinks.join(' ')+'</div>':'')+'</section>';
    root.querySelectorAll('[data-river]').forEach(btn=>btn.onclick=()=>{
      state.id=btn.dataset.river; state.q=Number(btn.dataset.time);
      $('[data-lesson="station"]').value=state.id;
      $('[data-lesson="quarter"]').value=String(state.q);
      draw();
      const heading=$('.lesson-body section:last-child h3');
      heading.tabIndex=-1;heading.focus({preventScroll:true});
    });
    const form=$('[data-exercise]');
    form.onsubmit=e=>{e.preventDefault();form.querySelector('output').textContent=gradeLessonEnergy(form.querySelector('input').value,d.power);};
    $('[data-solution]').onclick=()=>{form.querySelector('output').textContent=f(d.power,2)+' MW × 0.25 h ≈ '+f(d.mwh,1)+' MWh。';};
    root.querySelectorAll('[data-price]').forEach(btn=>btn.onclick=()=>{
      root.querySelectorAll('[data-price]').forEach(b=>b.setAttribute('aria-pressed',String(b===btn)));
      $('[data-price-answer]').textContent=(btn.dataset.price==='no'?'答对了。':'这里容易把计划当成收入。')+'本算例的日前预出清只给计划和参考价，此处不产生结算电费。';
    });
  }
  root._lessonVersion=loadCase;
  root.textContent='正在载入已出清的教学算例…';
  return loadCase();
}
