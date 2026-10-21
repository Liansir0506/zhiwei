const assert=require('assert'),{consume}=require('../../static/local-entry-auth.js');
for(const host of ['localhost','127.0.0.1','[::1]']){
 const storage=new Map([['api_key','expired']]),history=[];
 assert(consume({hostname:host,hash:'#local_demo_key='+'x'.repeat(43),pathname:'/',search:'?a=1'},
  {replaceState:(s,t,u)=>history.push(u)},{setItem:(k,v)=>storage.set(k,v)}));
 assert.equal(storage.get('api_key'),'x'.repeat(43));assert.deepEqual(history,['/?a=1']);
}
for(const [host,key] of [['example.com','x'.repeat(43)],['localhost.evil','x'.repeat(43)],['localhost','short'],['localhost','<script>']]){
 const storage=new Map([['api_key','existing']]),history=[];
 assert.equal(consume({hostname:host,hash:'#local_demo_key='+encodeURIComponent(key),pathname:'/',search:''},
  {replaceState:(s,t,u)=>history.push(u)},{setItem:(k,v)=>storage.set(k,v)}),false);
 assert.equal(storage.get('api_key'),'existing');assert.deepEqual(history,['/']);
}
assert.equal(consume({hash:'#ordinary-anchor'},{replaceState:()=>assert.fail('ordinary anchor changed')},{}),false);
console.log('Local auth contract: valid loopbacks, expired replacement, remote/malformed refusal, URL cleanup passed');
