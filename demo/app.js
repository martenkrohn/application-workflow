const $ = id => document.getElementById(id);
const state = {data:null, sort:{key:'fit',direction:-1}};
const staticMode = Boolean(window.DEMO_DASHBOARD);
const token = document.querySelector('meta[name="dashboard-token"]').content;
function esc(value) { return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function saved(key,fallback) { try { return JSON.parse(localStorage.getItem(key))??fallback; } catch { return fallback; } }
function store(key,value) { try { localStorage.setItem(key,JSON.stringify(value)); } catch {} }
function setTheme(theme) { document.documentElement.dataset.theme=theme; $('theme').textContent=theme==='dark'?'Light mode':'Dark mode'; $('theme').setAttribute('aria-pressed',String(theme==='dark')); }
setTheme(saved('portfolio-theme',matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));
async function load() {
  try {
    if (staticMode) state.data=structuredClone(window.DEMO_DASHBOARD);
    else { const response=await fetch('/api/v1/dashboard'); if(!response.ok) throw new Error('Dashboard could not load.'); state.data=await response.json(); }
    const choices=saved('portfolio-demo-decisions',{});
    if(staticMode) for(const row of state.data.detailed) row.review_status=choices[row.group_id]||row.review_status;
    populateFilters(); render(); notice('');
  } catch(error) { notice(error.message,true); }
}
function notice(message,error=false) { $('notice').textContent=message; $('notice').className=error?'error':''; $('notice').hidden=!message; }
function scrapeDay(value) { return String(value||'').slice(0,10); }
function formatScrapeDay(value) { return value ? new Date(value+'T12:00:00').toLocaleDateString('en-GB'):'—'; }
function formatDeadline(value) { return value?formatScrapeDay(scrapeDay(value)):'Not stated'; }
function compare(a,b,direction) { if(a==null) return b==null?0:1; if(b==null) return -1; return direction*(typeof a==='number'?a-b:String(a).localeCompare(String(b))); }
function populateFilters() {
  const previous=$('scrapeDay').value;
  const days=[...new Set(state.data.detailed.map(row=>scrapeDay(row.first_seen_at)))].sort().reverse();
  $('scrapeDay').innerHTML='<option value="">All collection dates</option>'+days.map(day=>`<option value="${esc(day)}">${esc(formatScrapeDay(day))}</option>`).join('');
  if(days.includes(previous)) $('scrapeDay').value=previous;
  const container=$('locationBucketOptions'), previousBuckets=new Map([...container.querySelectorAll('input')].map(x=>[x.value,x.checked]));
  container.replaceChildren();
  for(const bucket of state.data.location_buckets) {
    const label=document.createElement('label'),input=document.createElement('input'); label.className='check bucket-toggle'; input.type='checkbox';input.value=bucket;input.checked=previousBuckets.get(bucket)??true;input.onchange=render;label.append(input,document.createTextNode(bucket));container.append(label);
  }
}
function render() {
  const all=state.data.detailed;
  $('metrics').innerHTML=[['Assessed vacancies',all.length],['Strong matches',all.filter(x=>x.fit>=85).length],['For closer review',all.filter(x=>x.fit<65).length],['Your shortlist',all.filter(x=>x.review_status==='interested').length]].map(([label,n])=>`<div class="metric"><b>${n}</b><span>${label}</span></div>`).join('');
  const query=$('search').value.trim().toLowerCase(),buckets=new Set([...$('locationBucketOptions').querySelectorAll('input:checked')].map(x=>x.value));
  const rows=all.filter(row=>(!query||JSON.stringify([row.title,row.employer,row.locations,row.summary,row.gaps]).toLowerCase().includes(query))&&buckets.has(row.location_bucket)&&row.fit>=Number($('minFit').value||0)&&(!$('scrapeDay').value||scrapeDay(row.first_seen_at)===$('scrapeDay').value)&&(!$('hideExpired').checked||!row.expired)&&(!$('onlySaved').checked||row.review_status==='interested'));
  rows.sort((a,b)=>compare(a[state.sort.key],b[state.sort.key],state.sort.direction)||compare(a.fit,b.fit,-1));
  $('resultCount').textContent=`${rows.length} of ${all.length} shown`;
  $('tableHead').innerHTML='<tr><th data-label="Assessment"><button data-sort="fit">Fit / confidence</button></th><th><button data-sort="title">Vacancy</button></th><th>Assessment summary</th><th>Deadline</th><th>Review</th></tr>';
  $('tableBody').innerHTML=rows.length?rows.map(row=>`<tr><td class="assessment"><div><span class="subtle">Fit</span> <span class="fit">${esc(row.fit)}</span></div><div><span class="subtle">Confidence</span> ${esc(row.confidence)}</div><div class="subtle">${esc(formatScrapeDay(scrapeDay(row.first_seen_at)))}</div></td><td class="vacancy"><span class="title">${esc(row.title)}</span><span class="employer">${esc(row.employer)}</span><span class="subtle">${esc((row.locations||[]).join(', '))}</span></td><td>${esc(row.summary)}</td><td>${esc(formatDeadline(row.deadline))}</td><td><div class="actions"><button class="small" data-details="${esc(row.group_id)}">Details</button><button class="small secondary" data-save="${esc(row.group_id)}" aria-pressed="${row.review_status==='interested'}">${row.review_status==='interested'?'Saved':'Save to shortlist'}</button><a class="small" href="demo-source.html#${encodeURIComponent(row.sources[0].external_id)}" target="_blank" rel="noopener">Source</a></div></td></tr>`).join(''):'<tr><td class="empty" colspan="5">No vacancies match these filters. Try a lower minimum fit or a broader search.</td></tr>';
  document.querySelectorAll('[data-sort]').forEach(button=>{button.setAttribute('aria-label','Sort by '+button.textContent);button.onclick=()=>{state.sort={key:button.dataset.sort,direction:state.sort.key===button.dataset.sort?-state.sort.direction:1};render();};});
  document.querySelectorAll('[data-details]').forEach(button=>button.onclick=()=>showDetails(all.find(row=>row.group_id===button.dataset.details)));
  document.querySelectorAll('[data-save]').forEach(button=>button.onclick=()=>saveDecision(button.dataset.save));
  labelTableCells();setupColumnResizing();
}
async function saveDecision(group) {
  const row=state.data.detailed.find(row=>row.group_id===group),status=row.review_status==='interested'?'new':'interested';
  try {
    if(staticMode) {const choices=saved('portfolio-demo-decisions',{});choices[group]=status;store('portfolio-demo-decisions',choices);}
    else {const response=await fetch('/api/v1/reviews',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Token':token},body:JSON.stringify({group_id:group,status})});if(!response.ok) throw new Error('The decision could not be saved.');}
    row.review_status=status;render();notice(staticMode?'Demo shortlist saved in this browser.':'Shortlist updated.');
  }catch(error){notice(error.message,true);}
}
function showDetails(row) {
  const list=(title,items)=>`<section class="detail-section"><h3>${title}</h3>${items?.length?`<ul>${items.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>`:'<p>Not stated</p>'}</section>`;
  const facts=Object.entries(row.vacancy||{}).filter(([,v])=>v!=null&&v!==''&&(!Array.isArray(v)||v.length)).map(([k,v])=>`<div><span class="subtle">${esc(k.replaceAll('_',' '))}</span><br>${esc(Array.isArray(v)?v.join('; '):v)}</div>`).join('');
  const components=Object.entries(row.score_components||{}).map(([k,v])=>`<div><b>${esc(k.replaceAll('_',' '))}: ${esc(v.score)}</b><p>${esc(v.rationale)}</p></div>`).join('');
  $('detailContent').innerHTML=`<p class="eyebrow">FIT ${row.fit} · CONFIDENCE ${row.confidence} · FICTIONAL EXAMPLE</p><h2>${esc(row.title)}</h2><p>${esc(row.summary)}</p>${list('Strengths',row.strengths)}${list('Gaps',row.gaps)}${list('Missing information',row.missing_information)}<section class="detail-section"><h3>Assessment criteria</h3><div class="detail-grid">${components}</div></section><section class="detail-section"><h3>Saved vacancy facts</h3><div class="detail-grid">${facts}</div></section>`;
  $('details').showModal();
}
function labelTableCells() {
  const headings = [...$('tableHead').querySelectorAll('th')].map(th=>th.dataset.label || th.textContent);
  $('tableBody').querySelectorAll('tr').forEach(row=>{
    [...row.cells].forEach((cell,index)=>{
      if (!cell.classList.contains('empty')) cell.dataset.label=headings[index];
    });
    row.lastElementChild?.classList.add('row-actions');
  });
}

const columnLayout = matchMedia('(min-width:851px)');
function setupColumnResizing(reset=false) {
  const table=$('tableHead').closest('table');
  const headers=[...$('tableHead').querySelectorAll('th')];
  const storageKey=columnStorageKey();
  table.querySelector('colgroup')?.remove();
  table.querySelectorAll('.column-resizer').forEach(handle=>handle.remove());
  table.classList.remove('resizable');
  if (reset) { try { localStorage.removeItem(storageKey); } catch (_) { /* resizing still works */ } }
  if (!columnLayout.matches) return;
  let widths;
  try { widths=JSON.parse(localStorage.getItem(storageKey)); } catch (_) { /* use natural widths */ }
  if (!Array.isArray(widths) || widths.length!==headers.length || widths.some(width=>!Number.isFinite(width) || width<=0)) {
    widths=headers.map(header=>header.getBoundingClientRect().width);
  }
  const total=widths.reduce((sum,width)=>sum+width,0);
  if (!total) return;
  widths=widths.map(width=>100*width/total);
  const group=document.createElement('colgroup');
  const columns=headers.map(()=>group.appendChild(document.createElement('col')));
  table.prepend(group);
  table.classList.add('resizable');
  const handles=[];
  const apply=()=>{
    columns.forEach((column,index)=>column.style.width=`${widths[index]}%`);
    handles.forEach((handle,index)=>{
      handle.setAttribute('aria-valuenow',String(Math.round(widths[index])));
      handle.setAttribute('aria-valuetext',`${Math.round(widths[index])}% of table width`);
    });
  };
  const save=()=>{ try { localStorage.setItem(storageKey,JSON.stringify(widths)); } catch (_) { /* keep current widths */ } };
  const resize=(index,original,delta)=>{
    const tableWidth=table.getBoundingClientRect().width;
    const pair=original[index]+original[index+1];
    const minimum=Math.min(100*80/tableWidth,pair/2);
    widths[index]=Math.max(minimum,Math.min(pair-minimum,original[index]+100*delta/tableWidth));
    widths[index+1]=pair-widths[index];
    apply();
  };
  headers.slice(0,-1).forEach((header,index)=>{
    const handle=document.createElement('div');
    handle.className='column-resizer';
    handle.tabIndex=0;
    handle.setAttribute('role','separator');
    handle.setAttribute('aria-orientation','vertical');
    handle.setAttribute('aria-valuemin','0');
    handle.setAttribute('aria-valuemax','100');
    const label=header.dataset.label || header.textContent.trim();
    handle.setAttribute('aria-label',`Resize ${label} column`);
    handle.title='Drag to resize. Use left/right arrow keys when focused.';
    handle.onclick=event=>event.stopPropagation();
    let drag=null;
    handle.onpointerdown=event=>{
      if (event.button!==0) return;
      event.preventDefault();
      drag={start:event.clientX,widths:[...widths]};
      handle.setPointerCapture(event.pointerId);
      handle.focus({preventScroll:true});
      document.body.classList.add('resizing-columns');
    };
    handle.onpointermove=event=>{ if (drag) resize(index,drag.widths,event.clientX-drag.start); };
    const finish=()=>{
      if (!drag) return;
      drag=null;
      document.body.classList.remove('resizing-columns');
      save();
    };
    handle.onpointerup=finish;
    handle.onpointercancel=finish;
    handle.onlostpointercapture=finish;
    handle.onkeydown=event=>{
      if (!['ArrowLeft','ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      resize(index,[...widths],(event.key==='ArrowRight'?1:-1)*(event.shiftKey?30:10));
      save();
    };
    header.append(handle);
    handles.push(handle);
  });
  apply();
}
columnLayout.addEventListener('change',()=>{ if (state.data) setupColumnResizing(); });


function columnStorageKey() { return 'portfolio-dashboard-columns'; }
for(const id of ['search','scrapeDay','minFit','hideExpired','onlySaved']) $(id).addEventListener('input',render);
$('refresh').onclick=()=>{store('portfolio-demo-decisions',{});load();};
$('resetColumns').onclick=()=>setupColumnResizing(true);
$('theme').onclick=()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';store('portfolio-theme',theme);setTheme(theme);};
document.querySelector('.dialog-close').onclick=()=>$('details').close();
load();
