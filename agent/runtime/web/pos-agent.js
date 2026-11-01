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
 const TOKEN_KEY='rizq-agent-token',CONV_KEY='rizq-agent-conversation';
 let session='', identity=null;
 const newConv=()=>'c'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);
 let conversation=(()=>{try{return localStorage.getItem(CONV_KEY)||''}catch(e){return''}})()||newConv();
 const setConv=c=>{conversation=c;session='';try{localStorage.setItem(CONV_KEY,c)}catch(e){}};
 // Model replies are cleaned on the server; this is a last pass for anything shown or spoken.
 const tidy=t=>String(t||'').replace(/<\/?think>/gi,'').replace(/[*#`]+/g,'').replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\uFE0F]/gu,'').replace(/[ \t]{2,}/g,' ').trim();
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

 // Chromium desktop windows do not implement window.prompt. Keep secret entry
 // inside the app and never put its value into history or browser storage.
 function askSecret(label){
  return new Promise(resolve=>{
   const d=document.createElement('dialog');d.dir='rtl';
   d.style.cssText='width:min(420px,90vw);border:1px solid #d4af37;border-radius:16px;background:#18211b;color:#f5ecd7;padding:22px';
   const text=document.createElement('p');text.textContent=label;
   const input=document.createElement('input');input.type='password';input.autocomplete='off';
   input.style.cssText='width:100%;padding:9px;background:#26352b;color:white;border:1px solid #8a967f;border-radius:6px;box-sizing:border-box';
   const accept=document.createElement('button');accept.textContent='تأكيد';accept.type='button';
   const cancel=document.createElement('button');cancel.textContent='إلغاء';cancel.type='button';
   for(const b of [accept,cancel])b.style.cssText='margin:14px 0 0 8px;padding:9px 14px;border:1px solid #d4af37;border-radius:8px;background:#2e4835;color:white;cursor:pointer';
   d.append(text,input,accept,cancel);document.body.appendChild(d);
   let result=null;accept.onclick=()=>{result=input.value;input.value='';d.close()};
   cancel.onclick=()=>d.close();input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();accept.click()}};
   d.addEventListener('close',()=>{input.value='';d.remove();resolve(result)},{once:true});
   d.showModal();input.focus();
  });
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
  try{return (await api('/api/memory/history',{token,conversation})).messages||[]}
  catch(e){ if(e.status===403){remember('');identity=null;} return []; }
 };

 /* The POS restores a signed-in user from localStorage, but a restored session has no
    PIN -- and the server cannot issue a token without one, because trusting the browser
    is exactly what we refuse to do. So ask once, here, and only in that case. After a
    normal keypad sign-in a token already exists and this never runs. */
 async function ensureSession(){
  if(token)return true;
  const pin=await askSecret('لتشغيل المساعد الذكي، أدخل رقمك السري في رزق مرة واحدة:');
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
   result=await api('/api/chat/local',{message:question,token,sqlite_base64,session,approve_repairs,conversation});
  }catch(error){
   if(error.status===403){        // expired: ask for the PIN once and retry
    remember(''); identity=null;
    await ensureSession();
    result=await api('/api/chat/local',{message:question,token,sqlite_base64,session,approve_repairs,conversation});
    session=result.session;
    return tidy(result.answer)||'لم يرد النموذج. حاول مرة أخرى.';
   }
   if(error.status===404){           // older server without the local chat endpoint
    await api('/api/database/import',{token,sqlite_base64});
    result=await api('/api/chat',{message:question,token,provider:'auto',approve_repairs});
   } else throw error;
  }
  session=result.session;
  return tidy(result.answer)||'لم يرد النموذج. حاول مرة أخرى.';
 };

 /* Owner-only workflows: day close, payment reconciliation, stock alert, daily report, zakat. */
 window.rizqAgentWorkflow=async (name,params)=>{
  await ensureSession();
  const sqlite_base64=await exportDatabase();
  return api('/api/workflows/run',{token,name,params:params||{},sqlite_base64,conversation});
 };

 /* Owner-only: the agent's own audit trail of allowed and denied tool calls. */
 window.rizqAgentAudit=async (limit=50)=>{
  await ensureSession();
  return (await api('/api/audit',{token,limit})).entries||[];
 };

 /* Owner-only code changes, reviewed and applied by the code administrator. */
 window.rizqCodePropose=async (path,request)=>{
  const password=await askSecret('أدخل كلمة مرور مدير تطوير رزق:');
  if(!password)throw new Error('أُلغي الطلب.');
  return api('/api/code/propose',{path,request,password,token});
 };
 window.rizqCodeApply=async proposal=>{
  const password=await askSecret('لتطبيق التغيير، أدخل كلمة مرور مدير تطوير رزق:');
  if(!password)throw new Error('أُلغي الطلب.');
  const result=await api('/api/code/apply',{proposal,password,token});
  return 'تم حفظ التغيير في '+result.path+' مع نسخة احتياطية.';
 };

 // The manager's settings page owns this mount. No controls are placed outside the POS.
 const mountSettings=()=>{
  const panel=document.getElementById('rizq-ai-settings');
  if(!panel||panel.dataset.ready)return;
  panel.dataset.ready='1';
  panel.innerHTML=`<section class="rounded-2xl border border-white/10 bg-black/20 p-5" dir="rtl"><h2 class="text-lg font-bold text-rizq-gold">إعدادات المساعد الذكي</h2><p class="mt-2 text-sm text-rizq-light/60">اختر نموذجًا محليًا أو مزودًا سحابيًا. الإعدادات والمفتاح تنحفظ على هذا الجهاز فقط، والمفتاح ما يروح لأي مكان غير المزود اللي تختاره.</p><p class="mt-2 text-sm text-rizq-light/60">المزود السحابي قد يستقبل الرسائل ونتائج الأدوات وقد يفرض تكلفة.</p><form class="mt-4 space-y-3"><label class="block">المزود<select name="provider" class="mt-1 block w-full rounded-lg border border-white/20 bg-rizq-black p-2"><option value="auto">تلقائي محلي</option><option value="ollama">Ollama محلي</option><option value="openai">OpenAI</option><option value="claude">Claude</option><option value="rules">قواعد محلية فقط</option></select></label><label class="block">مفتاح API<input name="key" type="password" autocomplete="off" placeholder="أدخل مفتاح المزود السحابي" class="mt-1 block w-full rounded-lg border border-white/20 bg-rizq-black p-2"></label><label class="block">اسم النموذج (اختياري)<input name="model" placeholder="الافتراضي للمزود" class="mt-1 block w-full rounded-lg border border-white/20 bg-rizq-black p-2"></label><button type="submit" class="rounded-lg bg-rizq-gold px-4 py-2 font-bold text-rizq-black">حفظ إعدادات المساعد</button><p class="ai-message text-sm" role="status"></p></form><div class="ai-models mt-6 border-t border-white/10 pt-4"></div></section>`;
  const form=panel.querySelector('form'),status=panel.querySelector('.ai-message');
  const message=e=>e.status===404?'الخادم الحالي قديم. أعد تشغيل رزق لتفعيل إعدادات المساعد.':e.message;
  // Free local models: the server checks this device before anything is downloaded.
  const modelsBox=panel.querySelector('.ai-models');let polling=null;
  const esc=t=>String(t??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const btn='rounded-lg border border-rizq-gold/60 px-3 py-1 text-xs text-rizq-gold';
  async function showModels(){
   if(!panel.isConnected){clearInterval(polling);return}
   let info;
   try{info=await api('/api/ai/models',{})}catch(e){modelsBox.textContent=message(e);return}
   const d=info.device,rows=info.models.map(m=>{
    const dl=m.download||{};let right;
    if(m.installed||dl.state==='done')right=`<span class="text-green-400">✓ محمّل</span> <button type="button" data-use="${esc(m.name)}" class="${btn}">استخدمه</button>`;
    else if(dl.state==='downloading')right=`<span>جارٍ التنزيل ${dl.percent||0}%</span>`;
    else if(!m.fits)right=`<span class="text-xs text-rizq-light/50">جهازك ما يتحمله</span>`+(m.alternative?` <button type="button" data-pull="${esc(m.alternative)}" class="${btn}">نزّل ${esc(m.alternative)} بدلًا منه</button>`:'');
    else right=`<button type="button" data-pull="${esc(m.name)}" class="${btn}">تنزيل</button>`;
    const err=dl.state==='error'?`<div class="text-xs text-red-400">${esc(dl.detail)}</div>`:'';
    return `<li class="flex items-center justify-between gap-2 rounded-lg bg-black/20 p-2"><div><div class="font-bold" dir="ltr">${esc(m.name)}${m.name===info.recommended?' ⭐':''}</div><div class="text-xs text-rizq-light/60">${esc(m.label)} · ${m.size_gb} جيجا · يحتاج ${m.ram_needed_gb} جيجا ذاكرة</div>${err}</div><div class="shrink-0 text-sm">${right}</div></li>`;
   }).join('');
   modelsBox.innerHTML=`<h3 class="font-bold text-rizq-gold">النماذج المجانية</h3><p class="mt-1 text-xs text-rizq-light/60">جهازك: ${d.ram_gb} جيجا ذاكرة · ${d.disk_free_gb} جيجا مساحة فاضية.${info.ollama?'':' <b>Ollama مو شغّال</b> — افتحه أولًا (مجاني من ollama.com).'} النموذج الخفيف يجاوب أول، وإذا ما قدر يتحقق من الجواب يحوّل السؤال تلقائيًا لأكبر نموذج محمّل يتحمله جهازك. ⭐ = الأنسب لجهازك.</p><ul class="mt-2 space-y-2">${rows}</ul>`;
   const busy=info.models.some(m=>m.download&&m.download.state==='downloading');
   if(busy&&!polling)polling=setInterval(showModels,2000);
   if(!busy&&polling){clearInterval(polling);polling=null}
  }
  modelsBox.addEventListener('click',async e=>{
   const b=e.target.closest('button');if(!b)return;b.disabled=true;
   try{
    await ensureSession();
    if(b.dataset.pull){await api('/api/ai/models/pull',{name:b.dataset.pull});status.textContent='بدأ تنزيل '+b.dataset.pull+'.'}
    else if(b.dataset.use){await api('/api/ai/settings',{provider:'ollama',model:b.dataset.use,key:''});session='';form.elements.provider.value='ollama';form.elements.model.value=b.dataset.use;status.textContent='تم: المساعد يستخدم '+b.dataset.use+' الآن.'}
   }catch(err){status.textContent=message(err)}
   showModels();
  });
  if(token)showModels();else modelsBox.innerHTML='<button type="button" data-load="1" class="'+btn+'">عرض النماذج المجانية</button>';
  if(token)api('/api/ai/settings/status',{}).then(current=>{
   if(!panel.isConnected)return;
   form.elements.provider.value=current.provider;
   form.elements.model.value=current.model;
   if(current.configured)status.textContent='مفتاح محفوظ على هذا الجهاز. أدخله مجددًا فقط لو تبي تغيّره.';
  }).catch(e=>{if(panel.isConnected)status.textContent=message(e)});
  form.addEventListener('submit',async e=>{
   e.preventDefault();const button=form.querySelector('button');button.disabled=true;
   status.textContent='جارٍ الحفظ...';
   try{
    await ensureSession();
    const who=await window.rizqAgentIdentity();
    if(!who||who.role!=='owner')throw new Error('إعداد المساعد متاح لمالك المطعم فقط.');
    status.textContent='جارٍ التحقق من المفتاح...';
    const saved=await api('/api/ai/settings',{provider:form.elements.provider.value,model:form.elements.model.value.trim(),key:form.elements.key.value});
    session='';form.elements.key.value='';status.textContent=saved.verified?'تم الحفظ وتم التحقق من المفتاح ✓ المساعد جاهز.':'تم الحفظ.';
   }catch(err){status.textContent=message(err)}finally{button.disabled=false}
  });
 };
 // ---------------------------------------------------------------- quick actions
 // Work with or without internet: they use the verified skills and the cashier's own
 // functions. Anything that changes data is proposed in the chat and runs only when the
 // owner presses the assistant's confirm button.
 const goldBtn='rounded-lg border border-rizq-gold/50 px-3 py-1.5 text-xs text-rizq-gold hover:bg-rizq-gold/10';
 function screen(title,build){
  const d=document.createElement('dialog');d.dir='rtl';
  d.style.cssText='width:min(460px,92vw);border:1px solid #d4af37;border-radius:16px;background:#18211b;color:#f5ecd7;padding:20px';
  const h=document.createElement('h3');h.textContent=title;h.style.cssText='font-weight:700;color:#d4af37;margin-bottom:12px';
  const body=document.createElement('div');body.style.cssText='display:flex;flex-direction:column;gap:10px;max-height:60vh;overflow-y:auto';
  const close=document.createElement('button');close.type='button';close.textContent='إغلاق';close.className=goldBtn;close.style.marginTop='14px';
  close.onclick=()=>d.close();d.addEventListener('close',()=>d.remove(),{once:true});
  d.append(h,body,close);document.body.appendChild(d);build(body,d);d.showModal();return d;
 }
 const field=(label,attrs)=>{const l=document.createElement('label');l.textContent=label;l.style.cssText='display:flex;flex-direction:column;gap:4px;font-size:13px';
  const i=document.createElement(attrs.tag||'input');for(const[k,v]of Object.entries(attrs))if(k!=='tag')i[k]=v;i.style.cssText='padding:8px;background:#26352b;color:white;border:1px solid #8a967f;border-radius:6px';l.append(i);return[l,i]};
 const option=(text,onclick)=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.className=goldBtn;b.style.textAlign='right';b.style.padding='10px';b.onclick=onclick;return b};
 const alertIn=(box,msg)=>{let p=box.querySelector('.rizq-err');if(!p){p=document.createElement('p');p.className='rizq-err';p.style.color='#f87171';box.append(p)}p.textContent=msg};
 const send=text=>{if(window.rizqAssistantSend)window.rizqAssistantSend(text)};
 const propose=(text,rows,summary,run)=>window.rizqAssistantPropose&&window.rizqAssistantPropose(text,rows,run?{summary,run}:undefined);
 const QUICK=[
  ['كم اليوم؟',()=>send('كم مبيعات اليوم'),false],
  ['وش عندي؟',()=>screen('وش تبي تعرف؟',(b,d)=>[['المخزون الناقص','المخزون الناقص'],['قيمة المخزون','كم قيمة المخزون'],['أكثر صنف ينباع','وش أكثر صنف ينباع'],['مبيعات آخر 7 أيام','كم المبيعات آخر 7 أيام'],['الضريبة هذا الشهر','كم الضريبة هذا الشهر']]
     .forEach(([t,q])=>b.append(option(t,()=>{d.close();send(q)})))),false],
  ['سوّ خصم',()=>screen('كود خصم جديد',(b,d)=>{const[l1,code]=field('اسم الكود (حروف إنجليزية)',{value:'VIP',maxLength:16});const[l2,pct]=field('نسبة الخصم %',{type:'number',value:10,min:1,max:90});
     b.append(l1,l2,option('جهّز الخصم',()=>{const c=code.value.replace(/[^A-Za-z0-9]/g,'').toUpperCase(),p=Number(pct.value);if(!c||!(p>0&&p<=90))return alertIn(b,'اكتب اسم كود ونسبة بين 1 و90.');d.close();send(`أنشئ كود خصم ${c} بنسبة ${p}%`)}))}),true],
  ['جرد وتخزين',()=>screen('جرد صنف وإضافة كمية',(b,d)=>{const items=(window.rizqPos?window.rizqPos.products():[]);const[l1,sel]=field('الصنف',{tag:'select'});
     items.forEach(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=`${p.name} — الموجود ${p.stock}`;sel.append(o)});const[l2,qty]=field('الكمية المضافة',{type:'number',value:10,min:1});
     b.append(l1,l2,option('جهّز الإضافة',()=>{const p=items.find(x=>x.id===sel.value),q=Number(qty.value);if(!p||!(q>0))return alertIn(b,'اختر الصنف واكتب الكمية.');d.close();
      propose(`سأضيف ${q} إلى مخزون «${p.name}».`,[{label:'الموجود الآن',value:String(p.stock)},{label:'بعد الإضافة',value:String(p.stock+q)}],'تأكيد الإضافة',()=>window.rizqPos.restock(p.id,q))}))}),true],
  ['أضف موظف',()=>screen('موظف جديد (كاشير)',(b,d)=>{const[l1,name]=field('اسم الموظف',{});const[l2,pin]=field('رقمه السري (8 أرقام)',{type:'password',inputMode:'numeric',maxLength:8,autocomplete:'off'});
     b.append(l1,l2,option('جهّز الإضافة',()=>{const n=name.value.trim(),k=pin.value;pin.value='';if(!n||!/^\d{8}$/.test(k))return alertIn(b,'اكتب الاسم ورقم سري من 8 أرقام.');d.close();
      propose(`سأضيف «${n}» ككاشير.`,[{label:'الدور',value:'كاشير'},{label:'الرقم السري',value:'••••••••'}],'تأكيد إضافة الموظف',()=>window.rizqPos.addStaff(n,k))}))}),true],
  ['استيراد Excel',()=>screen('استيراد أصناف من Excel أو CSV',(b,d)=>{
     const hint=document.createElement('p');hint.style.cssText='font-size:12px;color:#8a967f';hint.textContent='الأعمدة: اسم الصنف، السعر، وإذا تبي: الكمية، التكلفة، القسم. تقدر ترفع الملف من أي جهاز داخل التطبيق.';
     const[l1,file]=field('الملف',{type:'file',accept:'.xlsx,.csv'});b.append(hint,l1);
     file.onchange=async()=>{const f=file.files[0];if(!f)return;alertIn(b,'جارٍ قراءة الملف...');
      try{await ensureSession();const buf=new Uint8Array(await f.arrayBuffer());let bin='';for(let i=0;i<buf.length;i+=32768)bin+=String.fromCharCode(...buf.subarray(i,i+32768));
       const r=await api('/api/import/products',{token,filename:f.name,file_base64:btoa(bin)});
       alertIn(b,`جاهز: ${r.items.length} صنف.`+(r.skipped.length?` تجاهلت ${r.skipped.length} صف (مثال: صف ${r.skipped[0].row}: ${r.skipped[0].reason}).`:''));
       if(r.items.length)b.append(option('جهّز الاستيراد للحفظ',()=>{d.close();
        propose(`سأضيف ${r.items.length} صنف من «${f.name}». الأصناف الموجودة من قبل ما راح تتغير.`,r.items.slice(0,5).map(it=>({label:it.name,value:`${it.price} ر.س · كمية ${it.stock}`})),'حفظ الأصناف في قاعدة البيانات',()=>window.rizqPos.importProducts(r.items))}))
      }catch(e){alertIn(b,e.message)}};
    }),true],
  ['سير العمل',()=>screen('سير العمل',(b,d)=>{
     const flows=[['day_close','إقفال اليوم: المبيعات والربح والمخزون وفحص البيانات'],['reconcile','مطابقة المدفوعات: طرق الدفع اليوم والطلبات اللي تحتاج مراجعة'],
      ['low_stock','تنبيه المخزون: الأصناف الناقصة'],['daily_report','تقرير اليوم: ملخص قصير'],['zakat','تذكير الزكاة']];
     for(const[id,label]of flows)b.append(option(label,async()=>{
      const params={};
      d.close();
      try{const r=await window.rizqAgentWorkflow(id,params);propose(r.title+'\n'+r.report,null,null,null)}
      catch(e){propose('تعذر تشغيل سير العمل: '+e.message,null,null,null)}
     }));
    }),true],
  ['المحادثات',()=>showConversations(),false],
 ];
 async function showConversations(){
  await ensureSession();
  const list=(await api('/api/memory/conversations',{token})).conversations||[];
  screen('محادثاتك',(b,d)=>{
   b.append(option('+ محادثة جديدة',()=>{setConv(newConv());if(window.rizqAssistantLoad)window.rizqAssistantLoad([]);d.close()}));
   let day='';
   for(const c of list){
    if(c.day!==day){day=c.day;const h=document.createElement('div');h.textContent=new Date(day+'T12:00').toLocaleDateString('ar-SA',{weekday:'long',day:'numeric',month:'long'});h.style.cssText='margin-top:8px;font-size:12px;color:#8a967f';b.append(h)}
    b.append(option(`${c.started} · ${c.title||'محادثة'} (${c.messages})${c.id===conversation?' — الحالية':''}`,async()=>{setConv(c.id);const m=(await api('/api/memory/history',{token,conversation:c.id})).messages||[];if(window.rizqAssistantLoad)window.rizqAssistantLoad(m);d.close()}));
   }
   if(!list.length)b.append(document.createTextNode('ما فيه محادثات محفوظة بعد.'));
  });
 }
 async function netStatus(pill){
  let online=navigator.onLine;try{online=(await (await fetch('/api/net/status')).json()).online}catch(e){}
  pill.textContent=online?'● متصل بالإنترنت: المساعد الذكي شغّال':'● بدون إنترنت: المساعد الذكي متوقف، استخدم الاختيارات السريعة';
  pill.style.color=online?'#4ade80':'#fbbf24';
 }
 const mountQuick=()=>{
  if(document.getElementById('rizq-quick'))return;
  const h1=[...document.querySelectorAll('h1')].find(x=>x.textContent.trim()==='المساعد الذكي');
  const header=h1&&h1.closest('div.border-b');if(!header)return;
  const bar=document.createElement('div');bar.id='rizq-quick';bar.dir='rtl';bar.className='flex flex-wrap items-center gap-2 border-b border-white/5 px-5 py-3';
  const pill=document.createElement('span');pill.className='w-full text-xs';bar.append(pill);
  const owner=!identity||identity.role==='owner';
  for(const[label,fn,ownerOnly]of QUICK){if(ownerOnly&&!owner)continue;const b=document.createElement('button');b.type='button';b.textContent=label;b.className=goldBtn;
   b.onclick=()=>Promise.resolve().then(fn).catch(e=>{pill.textContent=e.message;pill.style.color='#f87171'});bar.append(b)}
  header.after(bar);netStatus(pill);const t=setInterval(()=>{if(!pill.isConnected)return clearInterval(t);netStatus(pill)},20000);
 };
 // ---------------------------------------------------------------- payment device (settings)
 const mountDevice=()=>{
  const panel=document.getElementById('rizq-ai-settings');
  if(!panel||!panel.dataset.ready||document.querySelector('.rizq-device'))return;
  const box=document.createElement('section');box.className='rizq-device mt-4 rounded-2xl border border-white/10 bg-black/20 p-5';box.dir='rtl';
  box.innerHTML='<h2 class="text-lg font-bold text-rizq-gold">جهاز الدفع</h2><p class="mt-1 text-xs text-rizq-light/60">اربط جهاز الشبكة بالكاشير. الفاتورة ما تصدر إلا بعد ما يأكد الجهاز الدفع. ربط أجهزة المورّدين الحقيقية يُضاف مع حزمتها، وما تم التحقق منه على جهاز فعلي بعد.</p><div class="mt-3 grid gap-2"><select class="dev-type rounded-lg border border-white/20 bg-rizq-black p-2"></select><input class="dev-host rounded-lg border border-white/20 bg-rizq-black p-2" placeholder="عنوان IP الجهاز مثل 192.168.1.50" dir="ltr"><input class="dev-port rounded-lg border border-white/20 bg-rizq-black p-2" placeholder="المنفذ مثل 8080" dir="ltr" inputmode="numeric"><button type="button" class="dev-save rounded-lg px-4 py-2 font-bold" style="background:#d4af37;color:#111">اختبار الاتصال وحفظ</button><p class="dev-msg text-sm" role="status"></p></div>';
  panel.after(box);
  const type=box.querySelector('.dev-type'),host=box.querySelector('.dev-host'),port=box.querySelector('.dev-port'),msg=box.querySelector('.dev-msg');
  const sync=()=>{const net=type.value==='network';host.style.display=port.style.display=net?'':'none'};
  if(token)api('/api/payments/device/status',{}).then(c=>{for(const[k,v]of Object.entries(c.types||{})){const o=document.createElement('option');o.value=k;o.textContent=v;type.append(o)}
   type.value=c.type||'none';host.value=c.host||'';port.value=c.port||'';sync();msg.textContent=c.type&&c.type!=='none'?'الجهاز الحالي: '+c.label:''}).catch(e=>{msg.textContent=e.message});
  type.onchange=sync;
  box.querySelector('.dev-save').onclick=async()=>{msg.textContent='جارٍ الاختبار...';try{await ensureSession();const r=await api('/api/payments/device',{type:type.value||'none',host:host.value.trim(),port:port.value.trim()});msg.textContent=r.connected?'تم الربط: '+r.label:'تم الحفظ: تسجيل يدوي بدون جهاز.'}catch(e){msg.textContent=e.message}};
 };
 const mountAll=()=>{mountSettings();mountDevice();mountQuick()};
 const setup=()=>{new MutationObserver(mountAll).observe(document.getElementById('root'),{childList:true,subtree:true});mountAll()};
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})();
