/* Bridge the cashier's existing assistant to the local agent. */
(()=>{
 async function api(path,data){
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok||result.error){const error=new Error(result.error||`HTTP ${response.status}`);error.status=response.status;throw error}
  return result;
 }
 async function exportDatabase(){
  const current=window.rizqExportSnapshot?.();
  if(current){let binary='';for(let i=0;i<current.length;i+=32768)binary+=String.fromCharCode(...current.subarray(i,i+32768));return btoa(binary)}
  const db=await new Promise((resolve,reject)=>{const request=indexedDB.open('rizq-pos',1);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)});
  try {
   const data=await new Promise((resolve,reject)=>{const request=db.transaction('sqlite','readonly').objectStore('sqlite').get('db');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)});
   if(!data)throw new Error('قاعدة الكاشير غير جاهزة.');
   const bytes=new Uint8Array(data instanceof Blob?await data.arrayBuffer():data.buffer||data);
   let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
   return btoa(binary);
  } finally {db.close()}
 }
 let session='', currentUser='';
 window.rizqAgentHistory=async ()=>{
  const user=localStorage.getItem('rizq-user')||'';
  if(!user)return [];
  const result=await api('/api/memory/history',{user_id:user});
  return result.messages||[];
 };
 window.rizqAgentAsk=async question=>{
  const user=localStorage.getItem('rizq-user')||'';
  if(!user)throw new Error('سجل الدخول إلى رزق أولًا.');
  if(user!==currentUser){session='';currentUser=user}
  const sqlite_base64=await exportDatabase();
  let result;
  try{result=await api('/api/chat/local',{message:question,user_id:user,sqlite_base64,session})}
  catch(error){
   if(error.status!==404)throw error;
   await api('/api/database/import',{sqlite_base64});
   result=await api('/api/chat',{message:question,provider:'ollama',model:'qwen3.5:0.8b',approve_repairs:false});
  }
  session=result.session;
  return result.answer||'لم يرد النموذج. حاول مرة أخرى.';
 };
 window.rizqCodePropose=async (path,request)=>{
  const password=window.prompt('أدخل كلمة مرور مدير تطوير رزق:');
  if(!password)throw new Error('أُلغي الطلب.');
  return api('/api/code/propose',{path,request,password});
 };
 window.rizqCodeApply=async proposal=>{
  const password=window.prompt('لتطبيق التغيير، أدخل كلمة مرور مدير تطوير رزق:');
  if(!password)throw new Error('أُلغي الطلب.');
  const result=await api('/api/code/apply',{proposal,password});
  return 'تم حفظ التغيير في '+result.path+' مع نسخة احتياطية.';
 };
})();
