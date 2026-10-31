/* Bridge the cashier's existing assistant to the local Rizq agent.

   Identity: the cashier's sign-in is client-side only, so the browser cannot prove a
   role to the server. When the user signs in to Rizq, the POS hands that PIN once to
   /api/auth/login (see tools/pos_patches/patch_session.py). The server checks it against
   the cashier's own users table and returns an opaque token.

   The PIN is never stored. The token is, in localStorage, so reloading the page keeps
   the session and the conversation. Sessions are also persisted server-side, so
   restarting the server does not sign anyone out either. The browser never sends a role
   or a user id -- the server resolves both from the token. */
(()=>{
 const TOKEN_KEY='rizq-agent-token';
 let session='', identity=null;
 let token=(()=>{try{return localStorage.getItem(TOKEN_KEY)||''}catch(e){return''}})();
 const remember=t=>{token=t||'';try{t?localStorage.setItem(TOKEN_KEY,t):localStorage.removeItem(TOKEN_KEY)}catch(e){}};

 async function api(path,data){
  const headers={'Content-Type':'application/json'};
  if(token)headers.Authorization='Bearer '+token;
  const response=await fetch(path,{method:'POST',headers,body:JSON.stringify(data)});
  const result=await response.json().catch(()=>({error:'رد غير مفهوم من الخادم'}));
  if(!response.ok||result.error){
   const error=new Error(result.error||`HTTP ${response.status}`);
   error.status=response.status;error.code=result.code;throw error;
  }
  return result;
 }

 async function exportDatabase(){
  const toB64=bytes=>{let s='';for(let i=0;i<bytes.length;i+=32768)s+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(s)};
  const current=window.rizqExportSnapshot?.();
  if(current)return toB64(current);
  const db=await new Promise((res,rej)=>{const r=indexedDB.open('rizq-pos',1);r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error)});
  try{
   const data=await new Promise((res,rej)=>{const r=db.transaction('sqlite','readonly').objectStore('sqlite').get('db');r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error)});
   if(!data)throw new Error('قاعدة الكاشير غير جاهزة.');
   return toB64(new Uint8Array(data instanceof Blob?await data.arrayBuffer():data.buffer||data));
  }finally{db.close()}
 }

 /* Called by the POS the moment a PIN is accepted. Silent: never shows a dialog. */
 window.rizqAgentLogin=async pin=>{
  if(!pin)throw new Error('أدخل رقمك السري.');
  const sqlite_base64=await exportDatabase();
  const previous=token; remember('');
  try{
   const result=await api('/api/auth/login',{pin,sqlite_base64});
   remember(result.token); session='';
   identity={role:result.role,name:result.name,user_id:result.user_id,allowed_tools:result.allowed_tools};
   return identity;
  }catch(e){ remember(previous); throw e; }
 };

 window.rizqAgentLogout=async ()=>{
  const t=token;
  remember(''); session=''; identity=null;
  if(t)try{await api('/api/auth/logout',{token:t})}catch(e){}
 };

 /* Resolve the stored token to an identity (used after a page reload). */
 window.rizqAgentIdentity=async ()=>{
  if(identity)return identity;
  if(!token)return null;
  try{
   const who=await api('/api/auth/whoami',{token});
   identity={role:who.role,name:who.name,user_id:who.user_id,allowed_tools:who.allowed_tools};
   return identity;
  }catch(e){ if(e.status===403)remember(''); return null; }
 };

 /* The POS calls this on mount to repopulate the chat after a reload. */
 window.rizqAgentHistory=async ()=>{
  if(!token)return [];
  try{return (await api('/api/memory/history',{token})).messages||[]}
  catch(e){ if(e.status===403){remember('');identity=null;} return []; }
 };

 /* The POS restores a signed-in user from localStorage, but a restored session has no
    PIN -- and the server cannot issue a token without one, because trusting the browser
    is exactly what we refuse to do. So ask once, here, and only in that case. After a
    normal keypad sign-in a token already exists and this never runs. */
 async function ensureSession(){
  if(token)return true;
  const pin=window.prompt('لتشغيل المساعد الذكي، أدخل رقمك السري في رزق مرة واحدة:');
  if(!pin)throw new Error('المساعد يحتاج رقمك السري ليتأكد من صلاحياتك.');
  await window.rizqAgentLogin(pin);
  return true;
 }

 /* options.approve: a signed-in OWNER approves derived-data repairs for this one
    message. The server ignores the flag unless the token really resolves to an owner. */
 window.rizqAgentAsk=async (question,options)=>{
  await ensureSession();
  const sqlite_base64=await exportDatabase();
  const approve_repairs=!!(options&&options.approve);
  let result;
  try{
   result=await api('/api/chat/local',{message:question,token,sqlite_base64,session,approve_repairs});
  }catch(error){
   if(error.status===403){        // expired: ask for the PIN once and retry
    remember(''); identity=null;
    await ensureSession();
    result=await api('/api/chat/local',{message:question,token,sqlite_base64,session,approve_repairs});
    session=result.session;
    return result.answer||'لم يرد النموذج. حاول مرة أخرى.';
   }
   if(error.status===404){           // older server without the local chat endpoint
    await api('/api/database/import',{token,sqlite_base64});
    result=await api('/api/chat',{message:question,token,provider:'ollama',approve_repairs});
   } else throw error;
  }
  session=result.session;
  return result.answer||'لم يرد النموذج. حاول مرة أخرى.';
 };

 /* Owner-only: the agent's own audit trail of allowed and denied tool calls. */
 window.rizqAgentAudit=async (limit=50)=>{
  await ensureSession();
  return (await api('/api/audit',{token,limit})).entries||[];
 };

 /* Owner-only code changes, reviewed and applied by the code administrator. */
 window.rizqCodePropose=async (path,request)=>{
  const password=window.prompt('أدخل كلمة مرور مدير تطوير رزق:');
  if(!password)throw new Error('أُلغي الطلب.');
  return api('/api/code/propose',{path,request,password,token});
 };
 window.rizqCodeApply=async proposal=>{
  const password=window.prompt('لتطبيق التغيير، أدخل كلمة مرور مدير تطوير رزق:');
  if(!password)throw new Error('أُلغي الطلب.');
  const result=await api('/api/code/apply',{proposal,password,token});
  return 'تم حفظ التغيير في '+result.path+' مع نسخة احتياطية.';
 };
})();
