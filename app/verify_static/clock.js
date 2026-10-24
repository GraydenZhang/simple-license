'use strict';
// A display clock anchored to this response's server time. No requests or audit writes.
const clock=document.getElementById('current-time');
if(clock){
  const serverEpoch=Date.parse(clock.dataset.serverNow),started=performance.now();
  const format=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  const tick=()=>{const value=new Date(serverEpoch+performance.now()-started);const parts=Object.fromEntries(format.formatToParts(value).map(p=>[p.type,p.value]));clock.textContent=`${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;clock.dateTime=value.toISOString()};
  if(Number.isFinite(serverEpoch)){tick();setInterval(tick,1000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)tick()})}
}
