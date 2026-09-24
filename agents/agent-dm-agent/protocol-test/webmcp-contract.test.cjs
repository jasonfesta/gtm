// Offline contract checks only. Fake registration/fetch are NOT WebMCP proof.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const html = fs.readFileSync(__dirname + '/index.html','utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
async function harness({native=true, fail=false, hostname='127.0.0.1', storage=new Map(), registrationFails=false, response={id:'task-1',contextId:'context-1'}}={}) {
  const tools={}, elements={support:{},transcript:{}};
  let requests=0;
  const context={document:{getElementById:id=>elements[id]},navigator:{},location:{hostname,protocol:'http:'},
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    fetch:async()=>{requests++; if(fail)throw new Error('uncertain');return {ok:true,json:async()=>response};}};
  if(native)context.navigator.modelContext={registerTool:async tool=>{if(registrationFails)throw new Error('native rejected');tools[tool.name]=tool;}};
  await vm.runInNewContext(script,context);
  return {tools,elements,storage,requests:()=>requests};
}
test('no native capability and non-loopback origin fail closed',async()=>{
  for(const h of [await harness({native:false}),await harness({hostname:'example.org'})]) {
    assert.deepEqual(Object.keys(h.tools),[]);assert.match(h.elements.support.textContent,/BLOCKED/);
  }
});
test('follow-up must preserve both identifiers before any transport',async()=>{
  const h=await harness();
  for(const input of [{messageId:'x',text:'query',taskId:'t'},{messageId:'x',text:'query',contextId:'c'}, {messageId:'x',text:'',taskId:'t',contextId:'c'}])
    await assert.rejects(h.tools.send_agent_message.execute(input));
  assert.equal(h.requests(),0);
});
test('uncertain sends remain blocked across page reload; readback remains available',async()=>{
  const h=await harness({fail:true});const message={messageId:'unique',text:'controlled query'};
  await assert.rejects(h.tools.send_agent_message.execute(message),/uncertain/);
  const reloaded=await harness({storage:h.storage});
  await assert.rejects(reloaded.tools.send_agent_message.execute(message),/Unresolved attempt/);
  assert.equal(reloaded.requests(),0);
  await assert.rejects(reloaded.tools.get_agent_conversation.execute({taskId:'task-1'}),/metadata mismatch/);
  assert.equal(reloaded.requests(),1);
});
test('successful send is also never automatically repeated',async()=>{
  const h=await harness();const message={messageId:'unique',text:'controlled query'};
  await h.tools.send_agent_message.execute(message);
  await assert.rejects(h.tools.send_agent_message.execute(message),/Unresolved attempt/);
  assert.equal(h.requests(),1);
});

test('fresh message ID cannot bypass an unresolved send',async()=>{
 const h=await harness();
 await h.tools.send_agent_message.execute({messageId:'one',text:'controlled query'});
 await assert.rejects(h.tools.send_agent_message.execute({messageId:'two',text:'different id'}),/Unresolved attempt/);
 assert.equal(h.requests(),1);
});

test('asynchronous registration rejection never displays success',async()=>{
 const h=await harness({registrationFails:true});
 assert.match(h.elements.support.textContent,/BLOCKED: native registration failed/);
 assert.deepEqual(Object.keys(h.tools),[]);
});
test('follow-up requires confirmed native readback in exact context',async()=>{
 const response={id:'task-1',contextId:'context-1',status:{state:'input-required'},metadata:{protocol:'webmcp',controlled_test:true,organic_outreach:false},history:[
  {messageId:'one',role:'user',taskId:'task-1',contextId:'context-1',parts:[{kind:'text',text:'query'}]},
  {messageId:'reply-one',role:'agent',taskId:'task-1',contextId:'context-1',parts:[{kind:'text',text:'response'}]}]};
 const h=await harness({response});
 await h.tools.send_agent_message.execute({messageId:'one',text:'query'});
 await h.tools.get_agent_conversation.execute({taskId:'task-1'});
 await assert.rejects(h.tools.send_agent_message.execute({messageId:'two',text:'followup',taskId:'task-1',contextId:'wrong'}),/exact task/);
 await h.tools.send_agent_message.execute({messageId:'two',text:'followup',taskId:'task-1',contextId:'context-1'});
 assert.equal(h.requests(),3);
});

for (const [name, mutate] of [
 ['outbound text',r=>r.history[0].parts[0].text='different'],
 ['duplicate IDs',r=>r.history[1].messageId='one'],
 ['empty inbound',r=>r.history[1].parts[0].text=' '],
 ['wrong context',r=>{r.contextId='wrong';r.history.forEach(m=>m.contextId='wrong');}],
 ['wrong task',r=>{r.id='wrong';r.history.forEach(m=>m.taskId='wrong');}],
 ['working state',r=>r.status.state='working']
]) test('mismatched '+name+' cannot clear unresolved send',async()=>{
 const response={id:'task-1',contextId:'context-1',status:{state:'input-required'},metadata:{protocol:'webmcp',controlled_test:true,organic_outreach:false},history:[
  {messageId:'one',role:'user',taskId:'task-1',contextId:'context-1',parts:[{kind:'text',text:'query'}]},
  {messageId:'reply-one',role:'agent',taskId:'task-1',contextId:'context-1',parts:[{kind:'text',text:'reply'}]}]};
 const h=await harness({response});
 await h.tools.send_agent_message.execute({messageId:'one',text:'query'});
 mutate(response);
 await assert.rejects(h.tools.get_agent_conversation.execute({taskId:'task-1'}));
 await assert.rejects(h.tools.send_agent_message.execute({messageId:'two',text:'query'}),/Unresolved attempt/);
 assert.equal(h.requests(),2);
});
