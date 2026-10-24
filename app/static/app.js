'use strict';
const $=(s,root=document)=>root.querySelector(s), $$=(s,root=document)=>[...root.querySelectorAll(s)];
let toastTimer;
function toast(message){const el=$('#toast');if(!el)return;el.textContent=message;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),3500)}
async function api(url,body={},method='POST'){
 const response=await fetch(url,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':$('meta[name=csrf-token]')?.content||'','X-Requested-With':'campus-permits'},body:JSON.stringify(body)});
 let data;try{data=await response.json()}catch{throw new Error('服务暂时不可用，请稍后重试')}
 if(!response.ok){if(response.status===401&&!url.endsWith('/session')){location.href='/staff/login';throw new Error('登录已过期')}throw new Error(typeof data.detail==='string'?data.detail:(data.detail?.message||'请求未成功，请核对输入'))}
 return data;
}
function formData(form){const values=Object.fromEntries(new FormData(form));values.dormitories=$$('input[name=dormitories]:checked',form).map(x=>Number(x.value));if(values.date_mode==='single')values.end_date=values.start_date;delete values.date_mode;return values}
function bindForm(selector,handler){const form=$(selector);if(!form)return;form.addEventListener('submit',async event=>{event.preventDefault();const error=$('.form-error',form);if(error)error.hidden=true;const button=$('button[type=submit]',form)||$('button:not([type=button])',form);if(button){button.disabled=true;button.setAttribute('aria-busy','true')}try{await handler(form)}catch(err){if(error){error.textContent=err.message;error.hidden=false;error.focus();error.scrollIntoView({block:'center',behavior:'auto'})}else toast(err.message)}finally{if(button){button.disabled=false;button.removeAttribute('aria-busy')}}})}
function bindChoices(root=document){$$('.location-field',root).forEach(field=>{const change=()=>{const other=$('input[name=location_type]:checked',field)?.value==='OTHER';$('[data-dorms]',field).hidden=other;$('[data-other]',field).hidden=!other;$('[data-other]',field).required=other};$$('input[name=location_type]',field).forEach(x=>x.addEventListener('change',change));change()});$$('input[name=date_mode]',root).forEach(x=>x.addEventListener('change',()=>dateFields(x.closest('fieldset'))));$$('input[name=date_mode]:checked',root).forEach(x=>dateFields(x.closest('fieldset')))}
function dateFields(field){const range=$('input[name=date_mode]:checked',field).value==='range';$$('[data-end]',field).forEach(x=>x.hidden=!range);const end=$('input[name=end_date]',field);end.required=range;end.disabled=!range;const display=end.closest('.date-control')?.querySelector('.date-display');if(display){display.required=range;display.disabled=!range}}
// Native date placeholders follow browser locale (including a Chinese day label).
// Present an explicit yyyy.mm.dd text field while retaining the native picker and ISO form value.
$$('input[type=date][name=start_date],input[type=date][name=end_date]').forEach(native=>{
 const wrap=document.createElement('span');wrap.className='date-control';
 if(native.hasAttribute('data-end')){wrap.setAttribute('data-end','');native.removeAttribute('data-end')}
 native.before(wrap);wrap.append(native);native.classList.add('date-native');native.setAttribute('aria-label',native.getAttribute('aria-label')+'，打开日历');
 const display=document.createElement('input');display.type='text';display.className='form-control date-display';display.placeholder='yyyy.mm.dd';display.inputMode='numeric';display.autocomplete='off';display.maxLength=10;display.required=native.required;display.setAttribute('aria-label',native.getAttribute('aria-label').replace('，打开日历','')+'，格式 yyyy.mm.dd');display.value=native.value.replaceAll('-','.');
 const icon=document.createElement('span');icon.className='date-picker-icon';icon.textContent='▦';icon.setAttribute('aria-hidden','true');wrap.prepend(display);wrap.append(icon);
 display.addEventListener('input',()=>{const value=display.value;const iso=value.replaceAll('.','-');const match=/^\d{4}\.\d{2}\.\d{2}$/.test(value);native.value=match?iso:'';display.setCustomValidity(!value||match&&native.value===iso?'':'请输入有效日期，格式 yyyy.mm.dd')});
 native.addEventListener('change',()=>{display.value=native.value.replaceAll('-','.');display.setCustomValidity('')});
});
bindChoices();
$$('[data-open]').forEach(button=>button.addEventListener('click',()=>$('#'+button.dataset.open).showModal()));
$$('[data-close]').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$$('dialog').forEach(dialog=>dialog.addEventListener('click',e=>{if(e.target===dialog){const box=dialog.getBoundingClientRect();if(e.clientX<box.left||e.clientX>box.right||e.clientY<box.top||e.clientY>box.bottom)dialog.close()}}));
$$('[data-refresh]').forEach(button=>button.addEventListener('click',()=>location.reload()));


$('[data-toggle-password]')?.addEventListener('click',e=>{const input=$('input',e.target.parentElement);input.type=input.type==='password'?'text':'password';e.target.textContent=input.type==='password'?'显示':'隐藏'});
async function suggestPasswordChange(){
 const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','建议修改密码');
 const form=document.createElement('form');form.method='dialog';
 const title=document.createElement('h2');title.textContent='建议修改密码';
 const message=document.createElement('p');message.className='muted';message.textContent='为保护账户安全，建议您修改初始密码。您也可以稍后通过右上角用户菜单修改，当前可直接使用系统。';
 const actions=document.createElement('div');actions.className='modal-actions';
 const later=document.createElement('button');later.className='btn button secondary';later.value='later';later.textContent='稍后再说';
 const change=document.createElement('button');change.className='btn button primary';change.value='change';change.textContent='去修改密码';
 actions.append(later,change);form.append(title,message,actions);dialog.append(form);document.body.append(dialog);
 return new Promise(resolve=>{dialog.addEventListener('close',()=>{const chosen=dialog.returnValue==='change';dialog.remove();resolve(chosen)},{once:true});dialog.showModal()});
}
bindForm('#login-form',async form=>{const data=await api('/api/staff/session',Object.fromEntries(new FormData(form)));const change=data.recommend_password_change&&await suggestPasswordChange();location.href=change?'/staff/password':data.redirect});
$('[data-logout]')?.addEventListener('click',async()=>{try{const data=await api('/api/staff/logout');location.href=data.redirect}catch(err){toast(err.message)}});
bindForm('#password-form',async form=>{const data=Object.fromEntries(new FormData(form));if(data.new_password!==data.confirm_password)throw new Error('两次新密码输入不一致');const result=await api('/api/staff/password',data);location.href=result.redirect});
bindForm('#invite-form',async form=>{const data=await api('/api/staff/invitations',Object.fromEntries(new FormData(form)));form.hidden=true;$('#invite-result').hidden=false;$('#invite-qr').src=data.qr;$('#invite-url').value=data.url;$('#invite-expiry').textContent='首次提交截止：'+data.expires_at+'（北京时间）';$('#invite-dialog').dataset.created='true'});
$('#copy-invite')?.addEventListener('click',async()=>{const input=$('#invite-url');try{await navigator.clipboard.writeText(input.value);toast('申请链接已复制')}catch{input.select();toast('请按 Ctrl+C 复制选中的申请链接')}});
$('#invite-dialog')?.addEventListener('close',()=>{if($('#invite-dialog').dataset.created)location.href='/staff/invitations'});
if(new URLSearchParams(location.search).get('create')==='1')$('#invite-dialog')?.showModal();
function askReason(title,noReason=false){return new Promise(resolve=>{const dialog=$('#confirm-dialog'),field=$('#confirm-reason');$('#confirm-title').textContent=title;$('#confirm-message').textContent=noReason?'停用后，申请人将不能继续通过原链接提交、查结果或下载凭证。':'操作将留存审计记录，请填写具体原因。';field.hidden=noReason;$('label[for=confirm-reason]').hidden=noReason;field.value='';field.required=!noReason;dialog.returnValue='';dialog.onclose=()=>resolve(dialog.returnValue==='ok'?field.value.trim():null);dialog.showModal()})}
$$('[data-reason-url]').forEach(button=>button.addEventListener('click',async()=>{const noReason=button.dataset.noReason==='true';const reason=await askReason(button.dataset.reasonTitle,noReason);if(reason===null)return;if(!noReason&&!reason){toast('请填写具体原因');return}button.disabled=true;try{await api(button.dataset.reasonUrl,{reason});location.reload()}catch(err){toast(err.message);button.disabled=false}}));
bindForm('#review-form',async form=>{const result=await api('/api/staff/applications/'+form.dataset.id+'/approve',{data:formData(form)});location.href='/staff/permits/'+result.id});
bindForm('#reissue-form',async form=>{const result=await api('/api/staff/permits/'+form.dataset.id+'/reissue',{data:formData(form)});location.href='/staff/permits/'+result.id});
let activeUploads=0;
bindForm('#apply-form',async form=>{if(activeUploads)throw new Error('请等待附件上传完成后提交');await api('/api/apply/'+form.dataset.token+'/submit',formData(form));location.reload()});
function uploadFile(url,file,kind){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest(),data=new FormData();data.append('kind',kind);data.append('file',file);xhr.open('POST',url);xhr.setRequestHeader('X-Requested-With','campus-permits');xhr.setRequestHeader('X-CSRF-Token',$('meta[name=csrf-token]')?.content||'');xhr.upload.onprogress=e=>{if(e.lengthComputable){$('#upload-progress progress').value=e.loaded/e.total*100;$('#upload-progress span').textContent=file.name+' · '+Math.round(e.loaded/e.total*100)+'%'}};xhr.onload=()=>{let result;try{result=JSON.parse(xhr.responseText)}catch{reject(new Error('上传失败，请重试'));return}if(xhr.status>=200&&xhr.status<300)resolve(result);else reject(new Error(typeof result.detail==='string'?result.detail:(result.detail?.message||'上传失败')))};xhr.onerror=()=>reject(new Error('网络中断，请重新上传'));xhr.timeout=20*60*1000;xhr.ontimeout=()=>reject(new Error('上传超时，请重试'));xhr.send(data)})}
$$('[data-upload-kind]').forEach(input=>input.addEventListener('change',async()=>{const form=$('#apply-form');activeUploads++;$$('[data-upload-kind]').forEach(x=>x.disabled=true);$('#upload-progress').hidden=false;try{for(const file of input.files){const max=input.dataset.uploadKind==='WORKER_PHOTO'?10:200;if(file.size>max*1024*1024)throw new Error(file.name+' 超过 '+max+' MB');const result=await uploadFile('/api/apply/'+form.dataset.token+'/attachments',file,input.dataset.uploadKind);const item=document.createElement('div');item.className='attachment';item.dataset.attachment=result.id;const text=document.createElement('span');const title=document.createElement('strong');title.textContent=result.name;text.append(title);const remove=document.createElement('button');remove.type='button';remove.className='text-button danger-text';remove.dataset.deleteAttachment=result.id;remove.textContent='移除';item.append(text,remove);$('#attachment-items').append(item)}toast('材料上传完成')}catch(err){toast(err.message)}finally{activeUploads--;input.value='';$$('[data-upload-kind]').forEach(x=>x.disabled=false);$('#upload-progress').hidden=true}}));
$('#attachment-items')?.addEventListener('click',async e=>{const button=e.target.closest('[data-delete-attachment]');if(!button)return;try{await api('/api/apply/'+$('#apply-form').dataset.token+'/attachments/'+button.dataset.deleteAttachment,{},'DELETE');button.closest('.attachment').remove();toast('附件已移除')}catch(err){toast(err.message)}});
bindForm('#user-form',async form=>{await api('/api/staff/users',Object.fromEntries(new FormData(form)));location.reload()});
$$('[data-user-toggle]').forEach(button=>button.addEventListener('click',async()=>{try{await api('/api/staff/users/'+button.dataset.userToggle,{is_active:button.dataset.active==='true'});location.reload()}catch(err){toast(err.message)}}));
$$('[data-reset-user]').forEach(button=>button.addEventListener('click',()=>{$('#reset-form').dataset.user=button.dataset.resetUser;$('#reset-label').textContent='即将重置 '+button.dataset.username+' 的密码，并使已有登录会话失效。';$('#reset-dialog').showModal()}));
bindForm('#reset-form',async form=>{await api('/api/staff/users/'+form.dataset.user,Object.fromEntries(new FormData(form)));location.reload()});
bindForm('#settings-form',async form=>{await api('/api/staff/settings/issuer',Object.fromEntries(new FormData(form)),'PUT');toast('签发与日志设置已保存')});
// Presentation only: responsive navigation, table labels, dialog focus and feedback.
const nav=$('#staff-navigation'),navToggle=$('[data-menu]'),navScrim=$('.nav-scrim'),navMedia=matchMedia('(max-width:900px)');
function closeNav(restore=true){if(!nav)return;nav.classList.remove('open');navToggle.setAttribute('aria-expanded','false');navToggle.setAttribute('aria-label','打开导航');navScrim.hidden=true;$('.workspace').inert=false;nav.inert=navMedia.matches;document.body.classList.remove('navigation-open');if(restore)navToggle.focus()}
function openNav(){nav.inert=false;nav.classList.add('open');navToggle.setAttribute('aria-expanded','true');navToggle.setAttribute('aria-label','关闭导航');navScrim.hidden=false;$('.workspace').inert=true;document.body.classList.add('navigation-open');$('[data-nav-dismiss]',nav).focus()}
if(nav){nav.inert=navMedia.matches;navToggle.addEventListener('click',openNav);$$('[data-nav-dismiss]').forEach(x=>x.addEventListener('click',()=>closeNav()));navMedia.addEventListener('change',()=>closeNav(false));document.addEventListener('keydown',e=>{if(!nav.classList.contains('open'))return;if(e.key==='Escape'){e.preventDefault();closeNav()}if(e.key==='Tab'){const items=$$('a,button',nav).filter(x=>x.getClientRects().length);const first=items[0],last=items[items.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}})}
$$('dialog').forEach(dialog=>{dialog.addEventListener('close',()=>{if(dialog._trigger?.isConnected)dialog._trigger.focus()});if(!dialog.getAttribute('aria-label')&&!dialog.getAttribute('aria-labelledby'))dialog.setAttribute('aria-label',$('h2',dialog)?.textContent||'操作弹窗')});
document.addEventListener('click',e=>{if(e.target.closest('[data-open],[data-reason-url],[data-reset-user]')){const trigger=e.target.closest('button,a');queueMicrotask(()=>{const dialog=$('dialog[open]');if(dialog)dialog._trigger=trigger})}});
$$('.responsive-records table').forEach(table=>{table.setAttribute('role','table');const headings=$$('thead th',table).map(x=>x.textContent.trim());$$('thead tr,tbody tr',table).forEach(row=>row.setAttribute('role','row'));$$('th',table).forEach(th=>{th.setAttribute('scope','col');th.setAttribute('role','columnheader')});$$('tbody tr',table).forEach(row=>$$('td',row).forEach((cell,i)=>{cell.dataset.label=headings[i]||'操作';cell.setAttribute('role','cell');const value=document.createElement('div');value.className='record-value';while(cell.firstChild)value.append(cell.firstChild);cell.append(value)}))});
$$('.desktop-data th').forEach(th=>th.setAttribute('scope','col'));
$$('.nav-item.active,.tabs a.selected').forEach(link=>link.setAttribute('aria-current','page'));
$$('button[aria-busy=true]').forEach(button=>button.removeAttribute('aria-busy'));

// Keep filter labels visible after values are entered; names and submitted values stay unchanged.
$$('.filter-bar').forEach(form=>{const fields=[...form.children].filter(e=>e.matches('input:not([type=hidden]),select'));if(!fields.length)return;form.classList.add('labelled-filters');fields.forEach(field=>{const label=document.createElement('label');label.className='filter-control';const title=document.createElement('span');title.className='filter-label';title.textContent=field.getAttribute('aria-label')||field.placeholder;field.before(label);label.append(title,field)});[...form.children].filter(e=>e.tagName==='SPAN'&&e.textContent.trim()==='至').forEach(e=>{e.hidden=true})});
$$('.form-error').forEach(error=>{error.setAttribute('tabindex','-1');error.setAttribute('role','alert')});

// Output URL settings never alter the browser's internal navigation or API base.
bindForm('#access-settings-form',async form=>{await api('/api/staff/settings/access',Object.fromEntries(new FormData(form)),'PUT');toast('网址配置已保存，仅影响新生成的邀请与凭证')});
bindForm('#branding-form',async form=>{await api('/api/staff/settings/branding',Object.fromEntries(new FormData(form)),'PUT');location.reload()});
const accessForm=$('#access-settings-form');
function updateVerifyHost(){if(!accessForm)return;let host='—';try{host=new URL(accessForm.elements.verify_base_url.value).hostname.replace(/^\[|\]$/g,'')}catch{}$('#verify-host-preview').textContent=host}
if(accessForm){accessForm.addEventListener('input',updateVerifyHost);updateVerifyHost()}
let headerLogoUrl;
$('#header-logo-upload')?.addEventListener('change',e=>{const file=e.target.files[0];if(!file)return;if(file.size>5*1024*1024){toast('标志图片不得超过5 MB');e.target.value='';return}if(headerLogoUrl)URL.revokeObjectURL(headerLogoUrl);headerLogoUrl=URL.createObjectURL(file);$('#header-logo-preview').src=headerLogoUrl;$('#header-logo-preview').hidden=false;$('#header-logo-save').hidden=false});
$('#header-logo-save')?.addEventListener('click',async e=>{const file=$('#header-logo-upload').files[0];if(!file)return;const body=new FormData();body.append('file',file);e.target.disabled=true;try{const response=await fetch('/api/staff/settings/branding/logo',{method:'POST',headers:{'X-CSRF-Token':$('meta[name=csrf-token]').content},body});const result=await response.json();if(!response.ok)throw new Error(result.detail||'标志保存失败');location.reload()}catch(err){toast(err.message)}finally{e.target.disabled=false}});
const userMenu=$('.user-menu'),userTrigger=$('.user-trigger'),userDropdown=$('.user-dropdown');let menuCloseTimer;
function openUserMenu(){clearTimeout(menuCloseTimer);userDropdown.hidden=false;userTrigger.setAttribute('aria-expanded','true')}
function closeUserMenu(){clearTimeout(menuCloseTimer);userDropdown.hidden=true;userTrigger.setAttribute('aria-expanded','false')}
if(userMenu){userTrigger.addEventListener('click',openUserMenu);userMenu.addEventListener('pointerenter',e=>{if(e.pointerType==='mouse')openUserMenu()});userMenu.addEventListener('pointerleave',e=>{if(e.pointerType==='mouse')menuCloseTimer=setTimeout(()=>{if(!userMenu.contains(document.activeElement))closeUserMenu()},180)});userMenu.addEventListener('focusout',e=>{if(!userMenu.contains(e.relatedTarget))closeUserMenu()});userMenu.addEventListener('keydown',e=>{if(e.key==='Escape'){closeUserMenu();userTrigger.focus()}if(e.key==='ArrowDown'){e.preventDefault();openUserMenu();$('a',userDropdown).focus()}});document.addEventListener('click',e=>{if(!userMenu.contains(e.target))closeUserMenu()})}

$('#header-logo-reset')?.addEventListener('click',async e=>{e.target.disabled=true;try{await api('/api/staff/settings/branding/logo',{},'DELETE');location.reload()}catch(err){toast(err.message)}finally{e.target.disabled=false}});
