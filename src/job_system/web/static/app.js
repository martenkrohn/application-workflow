const token = document.querySelector('meta[name="dashboard-token"]').content;
const state = { data:null, queue:null, queueChanges:new Map(), queueStale:false, busy:false, loading:false, sort:{key:'fit', direction:-1} };
const $ = id => document.getElementById(id);
const filters = ['search','view','scrapeDay','minFit','hideExpired'];

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('theme').textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
  $('theme').setAttribute('aria-pressed', String(theme === 'dark'));
}

setTheme(localStorage.getItem('dashboard-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

async function request(path, options={}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json','X-Dashboard-Token':token}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

async function load() {
  if (state.loading) return;
  if (state.busy || state.queueChanges.size) { notice('Apply or discard selection changes before refreshing.',true); return; }
  state.loading=true; $('refresh').disabled=true;
  notice('Loading…');
  try {
    const {dashboard:data, queue} = await request('/api/v1/dashboard-state');
    state.data=data; state.queue=queue; state.queueStale=false;
    if (queue.previewed) $('queueCount').value=queue.total;
    populateScrapeDays(); populateLocationBuckets(); render(); notice('');
  }
  catch (error) { notice(error.message, true); }
  finally { state.loading=false; $('refresh').disabled=state.busy || Boolean(state.queueChanges.size); }
}

function render() {
  if (!state.data) return;
  const c = state.data.counts;
  const all = [...state.data.detailed, ...state.data.quick];
  const today = scrapeDay(new Date());
  const newest = all.map(row=>scrapeDay(row.first_seen_at)).filter(Boolean).sort().at(-1);
  $('metrics').innerHTML = [['Detailed',c.detailed],['Quick review',c.quick],['Added today',all.filter(row=>scrapeDay(row.first_seen_at)===today).length],['Latest scrape',newest?formatScrapeDay(newest):'—']].map(([label,n]) => `<div class="metric"><b>${n}</b><span>${label}</span></div>`).join('');
  const detailed = $('view').value === 'detailed';
  const queueView = $('view').value === 'queue';
  document.querySelectorAll('[data-view]').forEach(button=>{
    const active=button.dataset.view===$('view').value;
    button.classList.toggle('secondary',!active);
    button.setAttribute('aria-current',active?'page':'false');
  });
  document.querySelectorAll('.controls label').forEach(label=>{
    if (!label.contains($('view'))) label.hidden=queueView;
  });
  $('locationBuckets').hidden=queueView;
  $('minFit').closest('label').hidden = !detailed;
  $('minFit').disabled = !detailed;
  $('quickQueue').hidden = detailed || queueView;
  $('assessmentQueue').hidden = !queueView;
  if (queueView) { renderQueue(); return; }
  $('tableTitle').textContent = detailed ? 'Detailed review' : 'Quick review';
  const rows = filtered(detailed ? state.data.detailed : state.data.quick, detailed);
  $('resultCount').textContent = `${rows.length} shown`;
  renderTable(rows, detailed);
}

function filtered(rows, detailed) {
  const query = $('search').value.trim().toLowerCase();
  const selectedBuckets = new Set([...$('locationBucketOptions').querySelectorAll('input:checked')].map(input=>input.value));
  const min = Number($('minFit').value || 0);
  const selectedScrapeDay = $('scrapeDay').value;
  const result = rows.filter(row => {
    const haystack = JSON.stringify([row.title,row.employer,row.locations,row.summary,row.gaps]).toLowerCase();
    if (query && !haystack.includes(query)) return false;
    if (!selectedBuckets.has(row.location_bucket)) return false;
    if (detailed && row.fit != null && row.fit < min) return false;
    if (selectedScrapeDay && scrapeDay(row.first_seen_at) !== selectedScrapeDay) return false;
    if (detailed && $('hideExpired').checked && row.expired) return false;
    return true;
  });
  const {key,direction} = state.sort;
  return result.sort((a,b) => compareRows(a,b,key,direction,detailed));
}

function compareRows(a,b,key,direction,detailed=true) {
  const primary = key === 'first_seen_at'
    ? compare(scrapeDay(a.first_seen_at),scrapeDay(b.first_seen_at),direction)
    : compare(a[key],b[key],direction);
  if (primary) return primary;
  const byFit = compare(a.fit,b.fit,-1);
  if (byFit) return byFit;
  const byConfidence = compare(a.confidence,b.confidence,-1);
  if (byConfidence) return byConfidence;
  return compare(a.first_seen_at,b.first_seen_at,-1)
    || compare(a.source||a.sources?.[0]?.source,b.source||b.sources?.[0]?.source,1)
    || compare(a.external_id||a.group_id,b.external_id||b.group_id,1);
}
function compare(a,b,direction) { if (a == null) return b == null ? 0 : 1; if (b == null) return -1; const order=typeof a === 'number' ? a-b : String(a).localeCompare(String(b)); return direction*order; }
function scrapeDay(value) { const date=value instanceof Date?value:new Date(value); if (Number.isNaN(date.getTime())) return ''; return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`; }
function formatScrapeDay(day) { if (!day) return '—'; const [year,month,date]=day.split('-'); return `${date}/${month}/${year.slice(-2)}`; }
function formatScrapeAge(value) { const day=scrapeDay(value); if (!day) return '—'; const today=new Date(); today.setHours(0,0,0,0); const seen=new Date(`${day}T00:00:00`); const age=Math.round((today-seen)/86400000); return age===0?'Today':age===1?'Yesterday':age>1?`${age} days ago`:formatScrapeDay(day); }
function populateScrapeDays() { const select=$('scrapeDay'), selected=select.value; const days=[...new Set([...state.data.detailed,...state.data.quick].map(row=>scrapeDay(row.first_seen_at)).filter(Boolean))].sort().reverse(); select.innerHTML='<option value="">All scrape days</option>'+days.map(day=>`<option value="${day}">${formatScrapeDay(day)}</option>`).join(''); if (days.includes(selected)) select.value=selected; }
function populateLocationBuckets() {
  const container=$('locationBucketOptions');
  const previous=new Map([...container.querySelectorAll('input')].map(input=>[input.value,input.checked]));
  container.replaceChildren();
  for (const bucket of state.data.location_buckets) {
    const label=document.createElement('label'), input=document.createElement('input');
    label.className='check bucket-toggle';
    input.type='checkbox'; input.value=bucket; input.checked=previous.get(bucket) ?? true;
    input.addEventListener('change',render);
    label.append(input,document.createTextNode(bucket));
    container.append(label);
  }
}
function formatDeadline(value) { const text=String(value??''); const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(text); return match ? `${match[3]}/${match[2]}/${match[1].slice(-2)}` : text||'—'; }
function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function loc(row) { return (row.locations || []).join(', ') || '—'; }
function sourceLink(url,label='Open') {
  if (!url) return '—';
  let parsed;
  try { parsed=new URL(url); } catch { return '—'; }
  if (!['https:','http:'].includes(parsed.protocol)) return '—';
  const href=parsed.hostname.endsWith('.invalid')?'/demo-source#'+encodeURIComponent(parsed.pathname.split('/').pop()):parsed.href;
  return `<a href="${esc(href)}" target="_blank" rel="noopener">${label}</a>`;
}
function heading(key,label) { return `<th><button data-sort="${key}">${label}</button></th>`; }
function vacancyCell(row) { return `<td class="vacancy"><span class="title">${esc(row.title||'Untitled')}</span><span class="employer">${esc(row.employer||'—')}</span><span class="subtle">${esc(loc(row))}</span></td>`; }
function assessmentCell(row,detailed=false) {
  return `<td class="assessment"><div><span class="subtle">${detailed?'Fit':'Quick priority'}</span> <span class="fit" title="Assessed with ${esc(row.assessment_profile_id || 'Not screened')}">${esc(row.fit ?? '-')}</span></div><div><span class="subtle">Confidence</span> ${esc(row.confidence ?? '-')}</div><div title="${esc(formatScrapeDay(scrapeDay(row.first_seen_at)))}"><span class="subtle">First scraped</span> ${esc(formatScrapeAge(row.first_seen_at))}</div></td>`;
}
function assessmentHeading(detailed) { return `<th class="assessment-heading" data-label="Assessment"><div class="stacked-heading"><button data-sort="fit">${detailed?'Fit':'Quick priority (1-100)'}</button><button data-sort="confidence">Confidence</button><button data-sort="first_seen_at">First scraped</button></div></th>`; }
function queueKey(row) { return JSON.stringify([row.source,row.external_id]); }
function queueRows() { return state.queue.rows.map(row=>({...row,selected:state.queueChanges.get(queueKey(row)) ?? row.selected})); }
function updateQueueSummary() {
  const q=state.queue, selected=queueRows().filter(row=>row.selected), pending=state.queueChanges.size;
  const blocked=state.busy || state.queueStale;
  const manual=selected.filter(row=>row.origin==='manual').length;
  const ready=selected.filter(row=>!row.waiting_reason).length;
  $('resultCount').textContent=`${q.manual.length} manually queued / ${selected.length} selected`;
  $('queueSummary').textContent=q.previewed?`${manual} manual + ${selected.length-manual} suggested = ${selected.length} selected of target ${q.total}. ${ready} ready; ${selected.length-ready} waiting.${selected.length>q.total?' Selection exceeds the target; adjust the list or preview a larger total.':''}${pending?` ${pending} unsaved selection ${pending===1?'change':'changes'}. Apply selection changes to save.`:''}`:'Enter a total and preview the selection. Your manual choices are shown below.';
  $('prepareQueue').textContent=`Prepare batches for ${selected.length} vacancies`;
  $('prepareQueue').disabled=blocked || Boolean(pending) || !q.previewed || !selected.length || selected.length>q.total;
  $('fillQueue').disabled=blocked || Boolean(pending) || !q.previewed || selected.length>=q.total;
  $('previewQueue').disabled=blocked || Boolean(pending);
  $('queueCount').disabled=blocked || Boolean(pending);
  $('saveQueue').disabled=blocked || !pending;
  $('saveQueue').textContent=pending?`Apply ${pending} selection ${pending===1?'change':'changes'}`:'Apply selection changes';
  $('discardQueue').disabled=blocked || !pending;
  $('refresh').disabled=state.loading || state.busy || Boolean(pending);
  document.querySelectorAll('[data-remove-queue]').forEach(button=>button.disabled=blocked || Boolean(pending));
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
function columnStorageKey() { return `dashboard-column-widths-v1-${$('view').value}`; }
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

function renderTable(rows,detailed) {
  $('tableHead').innerHTML = `<tr>${assessmentHeading(detailed)}${detailed?'':'<th>Screening decision</th>'}${heading('title','Vacancy')}${detailed?'<th>Gaps</th>':heading('deadline','Deadline')}<th>Actions</th></tr>`;
  $('tableHead').querySelectorAll('[data-sort]').forEach(button => button.onclick=()=>{ const key=button.dataset.sort; state.sort = state.sort.key===key ? {key,direction:-state.sort.direction} : {key,direction:1}; render(); });
  if (!rows.length) { $('tableBody').innerHTML=`<tr><td class="empty" colspan="${detailed?4:5}">No vacancies match these filters.</td></tr>`; setupColumnResizing(); return; }
  $('tableBody').innerHTML = rows.map((row,index) => detailed ? detailedRow(row,index) : quickRow(row,index)).join('');
  labelTableCells();
  setupColumnResizing();
  $('tableBody').querySelectorAll('[data-detail]').forEach(b => b.onclick=()=>showDetails(rows[Number(b.dataset.detail)]));
  $('tableBody').querySelectorAll('[data-workspace]').forEach(b => b.onclick=()=>openWorkspace(b.dataset.workspace, b.dataset.employer, b.dataset.title, b.dataset.profile));
  $('tableBody').querySelectorAll('[data-queue]').forEach(b => b.onclick=()=>queueAssessment(b));
  $('tableBody').querySelectorAll('[data-remove-queue]').forEach(b => b.onclick=()=>queueAction('/remove',{source:b.dataset.source,external_id:b.dataset.id}));
  $('tableBody').querySelectorAll('[data-dismiss]').forEach(b => b.onclick=()=>dismissVacancy(b));
}

function detailedRow(row,index) {
  const workspace = `<button class="small" data-workspace="${esc(row.group_id)}" data-profile="${esc(row.assessment_profile_id)}" data-employer="${esc(row.employer)}" data-title="${esc(row.title)}">Start application</button>`;
  return `<tr>${assessmentCell(row,true)}${vacancyCell(row)}<td class="gaps">${esc((row.gaps||[]).join('; ')||'—')}</td><td><div class="actions"><button class="small secondary" data-detail="${index}">Details</button>${workspace}<button class="small dismiss" data-dismiss="${esc(row.group_id)}">Not interested</button>${sourceLink(row.application_url||row.source_url,'Source')}</div></td></tr>`;
}
function quickRow(row,index) {
  const decision=row.decision==='promote'?'Pass to detailed review':row.decision==='deprioritize'?'Obvious mismatch':row.screening_exclusion_reason?'Excluded before quick screening':row.screening_queued?'Quick screening queued':'Not screened';
  const exclusion=row.screening_exclusion_reason?`<div class="subtle">${esc(row.screening_exclusion_label || row.screening_exclusion_reason)}</div>`:'';
  const locationReview=row.location_assessment;
  const locationNote=locationReview?`<div class="subtle">${esc(locationReview.status==='unresolved'?'Location unresolved — kept for detailed review':locationReview.group || 'Location outside scope')}: ${esc(locationReview.summary)}</div>`:!row.decision && !row.screening_exclusion_reason && row.screening_location_status==='unresolved'?'<div class="subtle">Location unresolved — awaiting AI review</div>':'';
  const manual=(state.queue?.manual || []).some(([source,id])=>source===row.source && id===row.external_id);
  const queued=row.assessment_queued?'Prepared for detailed review':manual?`<button class="small secondary" data-remove-queue="1" data-source="${esc(row.source)}" data-id="${esc(row.external_id)}">Remove from queue</button>`:`<button class="small" data-queue="1" data-source="${esc(row.source)}" data-id="${esc(row.external_id)}">Queue detailed review</button>`;
  return `<tr>${assessmentCell(row)}<td>${esc(decision)}${exclusion}${locationNote}</td>${vacancyCell(row)}<td>${esc(formatDeadline(row.deadline))}</td><td><div class="actions">${queued}<button class="small dismiss" data-dismiss="${esc(row.group_id)}">Not interested</button>${sourceLink(row.source_url,'Source')}</div></td></tr>`;
}
function renderQueue() {
  const q=state.queue;
  $('tableTitle').textContent='Assessment queue';
  const rows=q.previewed?queueRows():q.manual_rows.map(row=>({...row,origin:'manual',selected:true}));
  $('queuePool').textContent=`${q.available} suggestions available: ${Object.entries(q.by_location).map(([group,count])=>`${group}: ${count}`).join(' · ') || 'none'}. ${q.manual.length} manually queued.`;
  $('unavailableManual').innerHTML=q.unavailable_manual.map(([source,id])=>`<p>${esc(source)}: ${esc(id)} is unavailable. <button class="small" data-remove-queue="1" data-source="${esc(source)}" data-id="${esc(id)}">Remove from queue</button></p>`).join('');
  $('preparedBatches').innerHTML=q.pending_batches.length?`<p><b>Prepared work for your agent workspace</b></p><ul>${q.pending_batches.map(batch=>`<li><code>${esc(batch.batch_id)}</code> — ${batch.item_count} vacancies (${esc(batch.status)})</li>`).join('')}</ul><p class="subtle">Use the generated HANDOFF.md with your chosen agent. These batches do not count toward this new selection.</p>`:'';
  $('tableHead').innerHTML='<tr><th>Include</th><th>Origin / state</th><th>Assessment</th><th>Vacancy</th><th>Actions</th></tr>';
  $('tableBody').innerHTML=rows.length?rows.map(row=>`<tr><td><input class="queue-select" type="checkbox" aria-label="Include ${esc(row.title)}" data-select="1" data-source="${esc(row.source)}" data-id="${esc(row.external_id)}" ${row.selected?'checked':''} ${!q.previewed||state.busy?'disabled':''}></td><td>${row.origin==='manual'?'Manually queued':'Suggested'}<div class="subtle">${row.waiting_reason?`Waiting: ${esc(row.waiting_reason)}`:'Ready to prepare'}</div><div class="subtle">${esc(row.location_group)}</div></td>${assessmentCell(row)}${vacancyCell(row)}<td>${row.origin==='manual'?`<button class="small secondary" data-remove-queue="1" data-source="${esc(row.source)}" data-id="${esc(row.external_id)}" ${state.busy?'disabled':''}>Remove from queue</button>`:''} ${sourceLink(row.source_url,'Source')}</td></tr>`).join(''):'<tr><td class="empty" colspan="5">No vacancies selected yet.</td></tr>';
  labelTableCells();
  $('tableBody').querySelectorAll('[data-select]').forEach(input=>{
    if (state.queueStale) input.disabled=true;
    input.onchange=()=>{
      const row=q.rows.find(row=>row.source===input.dataset.source && row.external_id===input.dataset.id);
      if (!row || state.busy || state.queueStale) return;
      const key=queueKey(row);
      if (input.checked===row.selected) state.queueChanges.delete(key);
      else state.queueChanges.set(key,input.checked);
      updateQueueSummary();
    };
  });
  document.querySelectorAll('[data-remove-queue]').forEach(button=>button.onclick=()=>queueAction('/remove',{source:button.dataset.source,external_id:button.dataset.id}));
  updateQueueSummary();
  setupColumnResizing();
}
async function queueAction(action,body) {
  if (state.busy || state.loading) return;
  if (state.queueStale) { notice('Refresh data before changing the assessment queue.',true); return; }
  if (state.queueChanges.size && action!=='/select-many') { notice('Apply or discard selection changes first.',true); return; }
  state.busy=true; render();
  notice(action==='/prepare'?'Preparing batches from your saved vacancies…':'Saving selection…');
  try {
    const result=await request(`/api/v1/assessment-queue${action}`,{method:'POST',body:JSON.stringify(body)});
    if (action==='/prepare') {
      state.queue=result.queue;
      state.data=result.dashboard;
      notice(`${result.prepared} vacancies prepared in ${result.batches.length} ${result.batches.length===1?'batch':'batches'}. ${result.waiting.length} selected vacancies remain waiting. Start assessment in your agent workspace.`,Boolean(result.waiting.length));
    } else { state.queue=result; state.queueChanges.clear(); notice('Selection saved.'); }
  } catch(error) {
    notice(error.message,true);
    if (action!=='/select-many') {
      try { state.queue=await request('/api/v1/assessment-queue'); } catch (_) { /* keep last preview */ }
    }
  } finally { state.busy=false; $('refresh').disabled=Boolean(state.queueChanges.size); render(); }
}
function slugify(value) { return String(value||'application').toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,70) || 'application'; }
function openWorkspace(group,employer,title,profile) { $('workspaceForm').dataset.profile=profile; $('workspaceGroup').value=group; $('workspaceSlug').value=slugify(`${employer}-${title}`); $('workspaceDialog').showModal(); }
function canSaveReview() {
  if (state.busy || state.loading) return false;
  if (state.queueChanges.size) { notice('Apply or discard selection changes in the Assessment queue first.',true); return false; }
  if (state.queueStale) { notice('Refresh data before making another change.',true); return false; }
  return true;
}
async function removeVacancyFromView(group) {
  const affectsQueue=state.data.quick.some(row=>row.group_id===group);
  for (const view of ['detailed','quick']) {
    state.data[view]=state.data[view].filter(row=>row.group_id!==group);
    state.data.counts[view]=state.data[view].length;
  }
  if (affectsQueue) state.queueStale=true;
  populateScrapeDays(); render();
  if (affectsQueue) {
    try { state.queue=await request('/api/v1/assessment-queue'); state.queueStale=false; }
    catch(error) { notice(`Vacancy saved, but the assessment queue could not refresh: ${error.message} Use Refresh data before changing the queue.`,true); }
  }
}
async function queueAssessment(button) {
  if (!canSaveReview()) return;
  state.busy=true; button.disabled=true; $('refresh').disabled=true;
  try {
    state.queue=await request('/api/v1/assessments',{method:'POST',body:JSON.stringify({source:button.dataset.source,external_id:button.dataset.id})});
    notice('Added to the assessment queue for your next detailed-assessment run, regardless of quick-screen fit. Review the list and prepare batches in the Assessment queue tab.');
  } catch(error) { notice(error.message,true); }
  finally { state.busy=false; $('refresh').disabled=Boolean(state.queueChanges.size); render(); }
}
async function dismissVacancy(button) {
  if (!canSaveReview()) return;
  state.busy=true; button.disabled=true; $('refresh').disabled=true;
  try {
    await request('/api/v1/reviews',{method:'POST',body:JSON.stringify({group_id:button.dataset.dismiss,status:'dismissed'})});
    notice('Vacancy hidden as not interested.');
    await removeVacancyFromView(button.dataset.dismiss);
  } catch(error) { notice(error.message,true); }
  finally { state.busy=false; $('refresh').disabled=Boolean(state.queueChanges.size); render(); }
}

function showDetails(row) {
  const list=(title,items)=>`<section class="detail-section"><h3>${title}</h3>${items&&items.length?`<ul>${items.map(i=>`<li>${esc(i)}</li>`).join('')}</ul>`:'<p>—</p>'}</section>`;
  const vacancy=Object.entries(row.vacancy||{}).filter(([,v])=>v!=null && v!=='' && (!Array.isArray(v)||v.length)).map(([k,v])=>`<div><span class="subtle">${esc(k.replaceAll('_',' '))}</span><br>${esc(k==='application_deadline'?formatDeadline(v):Array.isArray(v)?v.join('; '):v)}</div>`).join('');
  $('detailContent').innerHTML=`<p class="eyebrow">FIT ${row.fit} · CONFIDENCE ${row.confidence}</p><h2>${esc(row.title)}</h2><p>${esc(row.summary)}</p><div class="detail-grid">${vacancy}</div>${list('Strengths',row.strengths)}${list('Gaps',row.gaps)}${list('Deal-breakers',row.deal_breakers)}${list('Missing information',row.missing_information)}`;
  $('details').showModal();
}
function notice(message,error=false) { $('notice').textContent=message; $('notice').className=error?'error':''; $('notice').style.display=message?'block':'none'; }

filters.forEach(id => $(id).addEventListener(id==='view'?'change':'input', render));
$('refresh').onclick=load;
$('resetColumns').onclick=()=>setupColumnResizing(true);
$('openQueue').onclick=()=>{ $('view').value='queue'; render(); };
$('previewQueue').onclick=()=>queueAction('/preview',{total:Number($('queueCount').value)});
$('saveQueue').onclick=()=>queueAction('/select-many',{revision:state.queue.revision,changes:[...state.queueChanges].map(([key,selected])=>{ const [source,external_id]=JSON.parse(key); return {source,external_id,selected}; })});
$('discardQueue').onclick=()=>{ state.queueChanges.clear(); render(); notice('Provisional selection changes discarded.'); };
window.addEventListener('beforeunload',event=>{ if (state.queueChanges.size) { event.preventDefault(); event.returnValue=''; } });
$('fillQueue').onclick=()=>queueAction('/fill',{revision:state.queue.revision});
$('prepareQueue').onclick=()=>queueAction('/prepare',{revision:state.queue.revision});
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{ $('view').value=button.dataset.view; render(); });
$('theme').onclick=()=>{ const theme=document.documentElement.dataset.theme==='dark'?'light':'dark'; localStorage.setItem('dashboard-theme',theme); setTheme(theme); };
document.querySelectorAll('.dialog-close').forEach(button=>button.onclick=()=>button.closest('dialog').close());
$('workspaceForm').onsubmit=async event=>{
  event.preventDefault();
  if (!canSaveReview()) return;
  state.busy=true; $('refresh').disabled=true;
  try {
    const group=$('workspaceGroup').value;
    const result=await request('/api/v1/workspaces',{method:'POST',body:JSON.stringify({group_id:group,profile_id:$('workspaceForm').dataset.profile,slug:$('workspaceSlug').value})});
    $('workspaceDialog').close();
    notice(`Created ${result.path}. Open this folder in your editor to review the application documents.`);
    await removeVacancyFromView(group);
  } catch(error) { notice(error.message,true); }
  finally { state.busy=false; $('refresh').disabled=Boolean(state.queueChanges.size); render(); }
};
load();
