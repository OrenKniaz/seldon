"""Canonical changes reach a retained inspector through the shipped GET/load/DOM path."""

import json
import subprocess
from pathlib import Path

from codex_harness.core import status_api


def journey(samples, actions):
    """DOM/fetch primitives only; actual load, normalize, render and dialog callbacks run."""
    script = r"""
const fs=require('fs'),vm=require('vm'),input=JSON.parse(fs.readFileSync(0,'utf8'));
const roots=new Map(),requests=[],writes=[],records=[];
let queue=[],active=null,savedCopy=null;
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.listeners={};
  this.attrs={};this.dataset={};this.style={};this.ownText='';this.open=false;this.scrollTop=0;
  this.classList={add:()=>{}};}
 set textContent(v){this.ownText=String(v);this.children=[];}
 get textContent(){return this.ownText+this.children.map(c=>c.textContent).join('\n');}
 append(...items){this.children.push(...items.map(c=>c instanceof Element?c:
  Object.assign(new Element('#text'),{textContent:String(c)})));}
 replaceChildren(...items){this.ownText='';this.children=[];this.append(...items);}
 setAttribute(k,v){this.attrs[k]=v;if(k==='id')this.id=v;}
 removeAttribute(k){delete this.attrs[k];}
 addEventListener(k,fn){this.listeners[k]=fn;}
 querySelectorAll(selector){return descendants(this).filter(e=>matches(e,selector));}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 showModal(){this.open=true;}
 close(){this.open=false;}
 focus(){active=this;}
 cloneNode(){return new Element(this.tagName);}
}
function descendants(e){return e.children.flatMap(c=>[c,...descendants(c)]);}
function matches(e,s){return s.startsWith('#')?e.id===s.slice(1):s.startsWith('.')?
 String(e.className||'').split(' ').includes(s.slice(1)):s.startsWith('[')?
 Object.hasOwn(e.attrs,s.slice(1,-1)):e.tagName===s.toUpperCase();}
function select(s){for(const root of roots.values()){
 if(matches(root,s))return root;const found=root.querySelector(s);if(found)return found;}
 if(!roots.has(s))roots.set(s,new Element('div'));return roots.get(s);}
const document={querySelector:select,querySelectorAll:()=>[],createElement:t=>new Element(t),
 createElementNS:(_,t)=>new Element(t),createTextNode:t=>Object.assign(new Element('#text'),
 {textContent:t}),documentElement:new Element('html'),get activeElement(){return active;}};
const context={document,Node:Element,URL,URLSearchParams,location:{search:'',href:'http://local/'},
 history:{replaceState(){}},navigator:{clipboard:{async writeText(t){writes.push(t);}}},
 async fetch(url,options){requests.push({url,method:options.method});
  if(!queue.length)throw new Error('Unexpected request');const next=queue.shift();
  if(next.error)throw new Error(next.error);return {ok:true,json:async()=>next};}};
const source=fs.readFileSync(process.argv[1],'utf8');
vm.runInNewContext(source.slice(0,source.indexOf("\ndocument.querySelectorAll('[data-palette]')"))+
 '\nglobalThis.api={load,decisions,recordedDecisions,recordedReviews,show};})()',context);
const dialog=select('#inspector');let body=null;
const buttons=()=>dialog.querySelectorAll('button');
const button=label=>buttons().find(e=>e.textContent===label);
async function click(e,force=false){if(e&&(force||!e.disabled))await e.listeners.click?.();}
(async()=>{
 for(const action of input.actions){
  if(action.load){queue.push(input.samples[action.load]);await context.api.load();}
  if(action.open){context.api[action.open]();body=dialog.querySelector('.review-member')||
    dialog.querySelector('.decision')||dialog.children[2];
   dialog.scrollTop=77;body?.focus();for(const d of dialog.querySelectorAll('details'))d.open=true;
   savedCopy=button('Copy suggested reply');}
  if(action.copy)await click(savedCopy,action.copy==='force');
  if(action.refresh)await click(button('Show latest received details'));
  if(action.generic)context.api.show('Unrelated detail',new Element('p'));
  if(action.capture)records.push({name:action.capture,text:dialog.textContent,
   sameBody:descendants(dialog).includes(body),sameFocus:active===body,scroll:dialog.scrollTop,
   copyDisabled:savedCopy?.disabled===true,refreshDisabled:
    button('Show latest received details')?.disabled===true,writes:[...writes],
   disclosures:dialog.querySelectorAll('details').every(e=>e.open)});
 }
 console.log(JSON.stringify({records,requests,writes}));
})().catch(e=>{console.error(e);process.exit(1);});
"""
    completed = subprocess.run(
        ["node", "-e", script, str(Path(status_api.__file__).with_name("cockpit.js"))],
        input=json.dumps(dict(samples=samples, actions=actions)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    result = json.loads(completed.stdout)
    assert all(r == dict(url="/api/status", method="GET") for r in result["requests"])
    return result
